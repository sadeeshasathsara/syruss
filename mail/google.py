"""
Connecting a Google account, from the admin app's Email page (/email).

    start(purposes, redirect_uri, by)    Google's sign-in address, asking only for the permissions those purposes need
                                         (gmail.send to send, gmail.readonly to read, plus who you are), offline access
                                         (a refresh token) and PKCE. The state is sealed here: Google sends it back, and
                                         only this service can open it.
    finish(code, state, redirect_uri, by) trades the code for tokens (the client secret never leaves this service),
                                         learns the address, and saves the account with what Google actually granted (a
                                         person can untick a permission). Connecting the same address again updates it.
    revoke(refresh_token)                tells Google to forget the sign-in (when an account is removed).

The person who finishes must be the one who started (the state carries their staff id), within STATE_MINUTES.
"""
import base64
import hashlib
import secrets
import time
import uuid
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode
import httpx
from sqlalchemy import select
from mail.accounts.base import Capability
from mail.accounts.gmail import SCOPES, TOKEN_URL, granted_by
from mail.config import settings
from mail.router import PURPOSES
from mail.store import AccountRow, Store, seal, unseal

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
STATE_MINUTES = 15


class ConnectError(Exception):
    """Something a person can act on; the text is safe to show."""


def configured() -> bool:
    return bool(settings.GOOGLE_MAIL_CLIENT_ID and settings.GOOGLE_MAIL_CLIENT_SECRET)


def needs_of(purposes: List[str]) -> Capability:
    unknown = [p for p in purposes if p not in PURPOSES]
    if unknown or not purposes:
        raise ConnectError(f"Choose what it is for: {', '.join(PURPOSES)}.")
    out = Capability.NONE
    for p in purposes:
        out |= PURPOSES[p]
    return out


def start(purposes: List[str], redirect_uri: str, by: str) -> str:
    if not configured():
        raise ConnectError("Google isn't set up on this server (GOOGLE_MAIL_CLIENT_ID and GOOGLE_MAIL_CLIENT_SECRET in mail.env).")
    needs = needs_of(purposes)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = seal({"v": verifier, "p": purposes, "by": by, "exp": time.time() + STATE_MINUTES * 60, "n": secrets.token_hex(8)})
    scopes = ["openid", "email"] + [SCOPES[c] for c in (Capability.SEND, Capability.READ) if c in needs]
    return AUTHORIZE_URL + "?" + urlencode({
        "client_id": settings.GOOGLE_MAIL_CLIENT_ID, "redirect_uri": redirect_uri, "response_type": "code", "scope": " ".join(scopes),
        "access_type": "offline", "prompt": "consent", "include_granted_scopes": "false",
        "code_challenge": challenge, "code_challenge_method": "S256", "state": state,
    })


async def finish(store: Store, *, code: str, state: str, redirect_uri: str, by: str,
                 transport: Optional[httpx.AsyncBaseTransport] = None) -> Dict[str, Any]:
    try:
        st = unseal(state)
    except ValueError:
        raise ConnectError("This sign-in link isn't valid. Start again from the Email page.")
    if st.get("exp", 0) < time.time():
        raise ConnectError("This sign-in took too long. Start again from the Email page.")
    if st.get("by") != by:
        raise ConnectError("This Google sign-in was started by someone else.")
    async with httpx.AsyncClient(timeout=20, transport=transport) as client:
        res = await client.post(TOKEN_URL, data={
            "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri, "code_verifier": st["v"],
            "client_id": settings.GOOGLE_MAIL_CLIENT_ID, "client_secret": settings.GOOGLE_MAIL_CLIENT_SECRET})
        if res.status_code != 200:
            raise ConnectError("Google didn't accept the sign-in. Start again from the Email page.")
        tokens = res.json()
        refresh = tokens.get("refresh_token")
        if not refresh:
            raise ConnectError("Google gave no lasting access. In the Google account, remove Syruss under third-party access, then connect again.")
        who = await client.get(USERINFO_URL, headers={"Authorization": f"Bearer {tokens['access_token']}"})
    if who.status_code != 200 or not who.json().get("email") or who.json().get("email_verified") is False:
        raise ConnectError("Google didn't say which address this is.")
    address = who.json()["email"].lower()
    granted = granted_by(tokens.get("scope", ""))
    missing = needs_of(st["p"]) & ~granted
    if missing:
        raise ConnectError(f"Google wasn't given permission to {' and '.join(missing.names())}. Connect again and leave those ticked.")
    async with store.session() as s:
        row = (await s.execute(select(AccountRow).where(AccountRow.kind == "gmail", AccountRow.address == address))).scalar_one_or_none()
        if row is None:
            row = AccountRow(id=f"google-{uuid.uuid4().hex[:12]}", kind="gmail", address=address, name=settings.MAIL_FROM_NAME, config={}, created_by=by)
            s.add(row)
        row.secret, row.granted, row.status, row.last_error = seal({"refresh_token": refresh}), granted.names(), "active", None
        await s.commit()
        account_id = row.id
    from mail.accounts.gmail import _tokens
    _tokens.pop(account_id, None)
    return {"id": account_id, "address": address, "granted": granted.names(), "purposes": st["p"]}


async def revoke(refresh_token: str, transport: Optional[httpx.AsyncBaseTransport] = None) -> None:
    if not refresh_token:
        return
    try:
        async with httpx.AsyncClient(timeout=10, transport=transport) as client:
            await client.post(REVOKE_URL, data={"token": refresh_token})
    except httpx.HTTPError:
        pass  # removing the account here matters more; Google drops unused tokens in time
