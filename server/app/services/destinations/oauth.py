"""
Signing in to QuickBooks Online and Xero: OAuth2 authorization code. A person approves access once in the vendor's own page; Syruss keeps
the refresh token sealed and trades it for short-lived access tokens, saving the new refresh token each time (both vendors replace it).
"""
import asyncio
import base64
import secrets as token
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlencode
from app.core.secrets import seal, unseal
from app.services.destinations import http
from app.services.destinations.base import SendContext

STATE_MINUTES = 15
_locks: Dict[str, asyncio.Lock] = {}


@dataclass(frozen=True)
class Provider:
    authorize: str
    token: str
    scope: str
    extra: Tuple[Tuple[str, str], ...] = ()


PROVIDERS = {
    "quickbooks": Provider("https://appcenter.intuit.com/connect/oauth2", "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer", "com.intuit.quickbooks.accounting"),
    "xero": Provider("https://login.xero.com/identity/connect/authorize", "https://identity.xero.com/connect/token", "offline_access accounting.transactions accounting.contacts"),
}


def new_state(destination_id: str, tenant_id: str) -> Tuple[str, str]:
    """(the state to send to the vendor, the nonce to remember). The state is sealed, so it can't be forged or read."""
    nonce = token.token_urlsafe(16)
    return seal({"d": destination_id, "t": tenant_id, "n": nonce, "exp": time.time() + STATE_MINUTES * 60}), nonce


def read_state(state: str) -> Optional[Dict[str, Any]]:
    try:
        data = unseal(state)
    except Exception:  # noqa: BLE001  (tampered, or not ours)
        return None
    return data if data.get("exp", 0) > time.time() and data.get("d") and data.get("n") else None


def authorize_url(kind: str, client_id: str, redirect_uri: str, state: str) -> str:
    p = PROVIDERS[kind]
    return f"{p.authorize}?" + urlencode({"client_id": client_id, "response_type": "code", "scope": p.scope, "redirect_uri": redirect_uri, "state": state})


def _basic(client_id: str, client_secret: str) -> Dict[str, str]:
    return {"Authorization": "Basic " + base64.b64encode(f"{client_id}:{client_secret}".encode()).decode(), "Accept": "application/json"}


def _tokens(reply: http.Reply, previous_refresh: str = "") -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if not reply.ok:
        body = reply.json() if isinstance(reply.json(), dict) else {}
        why = body.get("error_description") or body.get("error") or http.failure_from(reply, "The sign-in service")[0]
        return None, str(why)
    body = reply.json() or {}
    if not body.get("access_token"):
        return None, "The sign-in service did not return an access token."
    return {
        "access_token": body["access_token"], "refresh_token": body.get("refresh_token") or previous_refresh,
        "expires_at": (datetime.now(timezone.utc) + timedelta(seconds=int(body.get("expires_in", 1800)))).isoformat(),
    }, None


async def exchange_code(kind: str, client_id: str, client_secret: str, code: str, redirect_uri: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    reply = await http.call("POST", PROVIDERS[kind].token, headers=_basic(client_id, client_secret), data={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri})
    return _tokens(reply)


async def _refresh(kind: str, client_id: str, client_secret: str, refresh_token: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    reply = await http.call("POST", PROVIDERS[kind].token, headers=_basic(client_id, client_secret), data={"grant_type": "refresh_token", "refresh_token": refresh_token})
    return _tokens(reply, refresh_token)


def _fresh(secrets: Dict[str, Any]) -> bool:
    try:
        return bool(secrets.get("access_token")) and datetime.fromisoformat(secrets["expires_at"]) - timedelta(seconds=120) > datetime.now(timezone.utc)
    except (KeyError, ValueError):
        return False


async def access_token(kind: str, ctx: SendContext) -> Tuple[Optional[str], Optional[str]]:
    """(a usable access token, None) or (None, why not). Refreshes when it is about to expire, one caller at a time per destination."""
    secrets = ctx.secrets
    if not secrets.get("refresh_token"):
        return None, "This destination is not connected to your account yet. Open it and choose Connect your account."
    if _fresh(secrets):
        return secrets["access_token"], None
    async with _locks.setdefault(ctx.destination_id, asyncio.Lock()):
        if ctx.reload_secrets:
            secrets = await ctx.reload_secrets()
            if _fresh(secrets):
                ctx.secrets.update(secrets)
                return secrets["access_token"], None
        tokens, problem = await _refresh(kind, ctx.config.get("client_id", ""), secrets.get("client_secret", ""), secrets["refresh_token"])
        if problem or tokens is None:
            return None, f"Signing in again failed: {problem}. Open the destination and connect your account again."
        ctx.secrets.update(tokens)
        if ctx.save_secrets:
            await ctx.save_secrets(tokens)
        return tokens["access_token"], None
