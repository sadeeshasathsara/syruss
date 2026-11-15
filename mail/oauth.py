"""
Connecting a Google or Microsoft account, from the admin app's Email page (/email).

    start(provider, purposes, redirect_uri, by)    the provider's sign-in address, asking only for the permissions those jobs need
                                         (send, and read where a job reads, plus who you are), offline access (a refresh token)
                                         and PKCE. The state is sealed here: the provider sends it back, and only this service
                                         can open it.
    finish(provider, code, state, redirect_uri, by) trades the code for tokens (the client secret never leaves this service),
                                         learns the address, and saves the account with what the provider actually granted (a
                                         person can untick a permission). Connecting the same address again updates it.
    revoke(provider, refresh_token)      tells the provider to forget the sign-in (when an account is removed), where it can.

The OAuth client (client id and secret) is the provider app set in the admin app (apps.py). The person who finishes must be
the one who started (the state carries their staff id), within STATE_MINUTES.
"""
import base64
import hashlib
import secrets
import time
import uuid
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlencode

import httpx
from sqlalchemy import select

from mail import apps
from mail.accounts.base import Capability
from mail.purposes import BUILTIN_NEEDS
from mail.store import AccountRow, Store, seal, unseal

STATE_MINUTES = 15
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GRAPH_API = "https://graph.microsoft.com/v1.0"


def microsoft_url(app: Optional[Dict[str, Any]], leaf: str) -> str:
    tenant = (app or {}).get("tenant") or "common"
    return f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/{leaf}"


GOOGLE_SCOPES = {Capability.SEND: "https://www.googleapis.com/auth/gmail.send", Capability.READ: "https://www.googleapis.com/auth/gmail.readonly"}
MICROSOFT_SCOPES = {Capability.SEND: "Mail.Send", Capability.READ: "Mail.Read"}


# account kind -> provider
KINDS = {"gmail": "google", "graph": "microsoft"}


class ConnectError(Exception):
    """Something a person can act on; the text is safe to show."""


def granted_by(provider: str, scope: str) -> Capability:
    """What the scopes the provider granted allow."""
    if provider == "microsoft":
        have = {s.rsplit("/", 1)[-1].lower() for s in (scope or "").split()}
        return _caps(have, {c: s.lower() for c, s in MICROSOFT_SCOPES.items()})
    return _caps(set((scope or "").split()), GOOGLE_SCOPES)


def _caps(have: set, wanted: Dict[Capability, str]) -> Capability:
    out = Capability.NONE
    for capability, needed in wanted.items():
        if needed in have:
            out |= capability
    return out


PROVIDERS: Dict[str, Dict[str, Any]] = {
    "google": {"label": "Google", "kind": "gmail", "scopes": GOOGLE_SCOPES,
               "authorize": lambda app: "https://accounts.google.com/o/oauth2/v2/auth", "token": lambda app: GOOGLE_TOKEN_URL},
    "microsoft": {"label": "Microsoft", "kind": "graph", "scopes": MICROSOFT_SCOPES,
                  "authorize": lambda app: microsoft_url(app, "authorize"), "token": lambda app: microsoft_url(app, "token")},
}
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"


def provider_of(key: str) -> Dict[str, Any]:
    if key not in PROVIDERS:
        raise ConnectError(f"This mail service can't connect '{key}'.")
    return PROVIDERS[key]


def needs_of(purposes: List[str], known: Optional[Dict[str, Capability]] = None) -> Capability:
    """What the jobs it is connected for need together. `known`: the jobs there are and what each needs (default: the built-in ones)."""
    known = known if known is not None else BUILTIN_NEEDS
    unknown = [p for p in purposes if p not in known]
    if unknown or not purposes:
        raise ConnectError(f"Choose what it is for: {', '.join(known)}.")
    out = Capability.NONE
    for p in purposes:
        out |= known[p]
    return out


def _app(provider: str) -> Dict[str, Any]:
    app = apps.get(provider)
    if app is None:
        raise ConnectError(f"{PROVIDERS[provider]['label']} isn't set up yet: add its app under Email › Provider apps.")
    return app


def start(provider: str, purposes: List[str], redirect_uri: str, by: str, known: Optional[Dict[str, Capability]] = None) -> str:
    spec = provider_of(provider)
    app = _app(provider)
    needs = needs_of(purposes, known)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = seal({"v": verifier, "p": purposes, "by": by, "pr": provider, "exp": time.time() + STATE_MINUTES * 60, "n": secrets.token_hex(8)})
    wanted = [spec["scopes"][c] for c in (Capability.SEND, Capability.READ) if c in needs]
    params = {"client_id": app["client_id"], "redirect_uri": redirect_uri, "response_type": "code", "code_challenge": challenge,
              "code_challenge_method": "S256", "state": state}
    if provider == "google":
        params.update(scope=" ".join(["openid", "email"] + wanted), access_type="offline", prompt="consent", include_granted_scopes="false")
    else:
        params.update(scope=" ".join(["openid", "email", "offline_access", "User.Read"] + wanted), response_mode="query", prompt="select_account")
    return spec["authorize"](app) + "?" + urlencode(params)


async def _identity(provider: str, client: httpx.AsyncClient, tokens: Dict[str, Any]) -> str:
    bearer = {"Authorization": f"Bearer {tokens['access_token']}"}
    if provider == "google":
        who = await client.get(USERINFO_URL, headers=bearer)
        body = who.json() if who.status_code == 200 else {}
        if not body.get("email") or body.get("email_verified") is False:
            raise ConnectError("Google didn't say which address this is.")
        return body["email"].lower()
    who = await client.get(f"{GRAPH_API}/me", params={"$select": "mail,userPrincipalName"}, headers=bearer)
    body = who.json() if who.status_code == 200 else {}
    address = (body.get("mail") or body.get("userPrincipalName") or "").lower()
    if "@" not in address:
        raise ConnectError("Microsoft didn't say which address this is.")
    return address


async def finish(store: Store, *, provider: str, code: str, state: str, redirect_uri: str, by: str,
                 transport: Optional[httpx.AsyncBaseTransport] = None) -> Dict[str, Any]:
    spec = provider_of(provider)
    label = spec["label"]
    app = _app(provider)
    try:
        st = unseal(state)
    except ValueError:
        raise ConnectError("This sign-in link isn't valid. Start again from the Email page.")
    if st.get("exp", 0) < time.time():
        raise ConnectError("This sign-in took too long. Start again from the Email page.")
    if st.get("by") != by:
        raise ConnectError(f"This {label} sign-in was started by someone else.")
    if st.get("pr", "google") != provider:
        raise ConnectError("This sign-in was started for another provider. Start again from the Email page.")
    async with httpx.AsyncClient(timeout=20, transport=transport) as client:
        res = await client.post(spec["token"](app), data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri, "code_verifier": st["v"],
            "client_id": app["client_id"], "client_secret": app["client_secret"]})
        if res.status_code != 200:
            raise ConnectError(f"{label} didn't accept the sign-in. Start again from the Email page.")
        tokens = res.json()
        refresh = tokens.get("refresh_token")
        if not refresh:
            raise ConnectError(f"{label} gave no lasting access. Remove Syruss under the account's third-party apps, then connect again.")
        address = await _identity(provider, client, tokens)
    granted = granted_by(provider, tokens.get("scope", ""))
    missing = needs_of(st["p"], {j.key: j.needs for j in await store.jobs()}) & ~granted
    if missing:
        raise ConnectError(f"{label} wasn't given permission to {' and '.join(missing.names())}. Connect again and leave those ticked.")
    async with store.session() as s:
        row = (await s.execute(select(AccountRow).where(AccountRow.kind == spec["kind"], AccountRow.address == address))).scalar_one_or_none()
        if row is None:
            from mail.config import settings
            row = AccountRow(id=f"{provider}-{uuid.uuid4().hex[:12]}", kind=spec["kind"], address=address, name=settings.MAIL_FROM_NAME, config={}, created_by=by)
            s.add(row)
        row.secret, row.granted, row.status, row.last_error = seal({"refresh_token": refresh}), granted.names(), "active", None
        await s.commit()
        account_id = row.id
    forget_tokens(account_id)
    return {"id": account_id, "address": address, "granted": granted.names(), "purposes": st["p"], "provider": provider}


def forget_tokens(account_id: str) -> None:
    """Drops the access tokens held in memory for an account (it was reconnected or removed)."""
    from mail.accounts import gmail, graph
    gmail._tokens.pop(account_id, None)
    graph._tokens.pop(account_id, None)


async def revoke(provider: str, refresh_token: str, transport: Optional[httpx.AsyncBaseTransport] = None) -> None:
    """Google can be told to forget a sign-in; Microsoft has no such call for a signed-in person's token (it ends when the person removes the app)."""
    if provider != "google" or not refresh_token:
        return
    try:
        async with httpx.AsyncClient(timeout=10, transport=transport) as client:
            await client.post(REVOKE_URL, data={"token": refresh_token})
    except httpx.HTTPError:
        pass  # removing the account here matters more; Google drops unused tokens in time
