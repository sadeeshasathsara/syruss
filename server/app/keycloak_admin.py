"""
Keycloak's admin API, for commands an operator runs on the server (python -m app.invite_staff), never for the running
API: the running server has no Keycloak admin access. The admin login comes from the operator's terminal for that one
command (KEYCLOAK_ADMIN, KEYCLOAK_ADMIN_PASSWORD) and is kept nowhere.

    KEYCLOAK_URL               where Keycloak answers (default: OIDC_ISSUER without /realms/<realm>)
    KEYCLOAK_ADMIN / _PASSWORD the Keycloak admin, as for bridge/keycloak/harden.py
"""
import os
from typing import Optional
from urllib.parse import quote
import httpx
from app.core.config import settings

# What someone given a new staff sign-in does from Keycloak's emailed link: choose a password, set up the one-time code
SETUP_ACTIONS = ["UPDATE_PASSWORD", "CONFIGURE_TOTP"]


class KeycloakAdminError(RuntimeError):
    pass


def _realm_and_base() -> tuple:
    issuer = settings.OIDC_ISSUER.rstrip("/")
    base, _, realm = issuer.rpartition("/realms/")
    return (os.environ.get("KEYCLOAK_URL") or base).rstrip("/"), realm


class KeycloakAdmin:
    def __init__(self, username: str, password: str, url: Optional[str] = None, realm: Optional[str] = None) -> None:
        base, default_realm = _realm_and_base()
        self.url, self.realm = (url or base).rstrip("/"), realm or default_realm
        self._login = (username, password)
        self._token: Optional[str] = None

    @classmethod
    def from_environment(cls) -> Optional["KeycloakAdmin"]:
        """From KEYCLOAK_ADMIN / KEYCLOAK_ADMIN_PASSWORD in this command's environment; None if they aren't there."""
        user, password = os.environ.get("KEYCLOAK_ADMIN"), os.environ.get("KEYCLOAK_ADMIN_PASSWORD")
        return cls(user, password) if user and password else None

    async def _call(self, method: str, path: str, **kwargs) -> httpx.Response:
        async with httpx.AsyncClient(timeout=20) as client:
            if self._token is None:
                res = await client.post(f"{self.url}/realms/master/protocol/openid-connect/token", data={
                    "client_id": "admin-cli", "grant_type": "password", "username": self._login[0], "password": self._login[1]})
                if res.status_code != 200:
                    raise KeycloakAdminError(f"Keycloak refused the admin sign-in at {self.url} ({res.status_code}). Check KEYCLOAK_ADMIN and its password.")
                self._token = res.json()["access_token"]
            res = await client.request(method, f"{self.url}/admin/realms/{self.realm}{path}",
                                       headers={"Authorization": f"Bearer {self._token}"}, **kwargs)
        if res.status_code >= 400:
            raise KeycloakAdminError(f"Keycloak answered {res.status_code} to {method} {path}: {res.text[:300]}")
        return res

    async def find_user(self, email: str) -> Optional[dict]:
        res = await self._call("GET", f"/users?email={quote(email)}&exact=true")
        matches = [u for u in res.json() if (u.get("email") or "").lower() == email.lower()]
        return matches[0] if matches else None

    async def create_user(self, email: str) -> str:
        """A sign-in for the address, with no password yet (it is chosen from the setup email). Returns its id (the `sub`)."""
        res = await self._call("POST", "/users", json={"username": email, "email": email, "enabled": True, "emailVerified": False,
                                                       "requiredActions": SETUP_ACTIONS})
        return res.headers["Location"].rstrip("/").rsplit("/", 1)[1]

    async def send_setup(self, user_id: str, *, client_id: str, redirect_uri: str, lifespan_seconds: int) -> None:
        """Keycloak emails a one-time link to choose a password and set up the code, which then leads to redirect_uri."""
        await self._call("PUT", f"/users/{user_id}/execute-actions-email?client_id={quote(client_id)}&redirect_uri={quote(redirect_uri, safe='')}"
                                f"&lifespan={lifespan_seconds}", json=SETUP_ACTIONS)

    async def delete_user(self, user_id: str) -> None:
        await self._call("DELETE", f"/users/{user_id}")
