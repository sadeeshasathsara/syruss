"""
A Google account (Gmail or Google Workspace), used through the Gmail API with the permissions someone approved in the
admin app (google.py): gmail.send to send, gmail.readonly to read. Nothing else: it can't delete, label or change mail.

Its sign-in is a refresh token (sealed in the store). Access tokens are made from it when needed and kept in memory until
they expire. When Google refuses the refresh token (access removed in the Google account, password changed, or the
7-day limit of an OAuth app still in testing), the account is marked "needs reconnecting" and its purposes fall back
to the account from the environment until someone connects it again.

Reading: the first read only notes the mailbox's current history id; later reads list what was added to the inbox since
(users.history.list) and fetch each message whole (format=raw). A history id too old for Google starts over from now.
"""
import asyncio
import base64
import logging
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple
import httpx
from mail.accounts.base import Capability, MailAccount, MailError, Outbound, SentReceipt, TestResult
from mail.accounts.mime import build
from mail.config import settings

logger = logging.getLogger("mail.gmail")
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://gmail.googleapis.com/gmail/v1/users/me"
SCOPES = {Capability.SEND: "https://www.googleapis.com/auth/gmail.send", Capability.READ: "https://www.googleapis.com/auth/gmail.readonly"}
MAX_PER_READ = 25
_tokens: Dict[str, Tuple[str, float]] = {}   # account id -> (access token, expires at)
_locks: Dict[str, asyncio.Lock] = {}


def granted_by(scope: str) -> Capability:
    """What the scopes Google granted allow."""
    have = set((scope or "").split())
    out = Capability.NONE
    for capability, needed in SCOPES.items():
        if needed in have:
            out |= capability
    return out


class AccessLost(MailError):
    """Google refused the sign-in: someone must connect the account again."""


class GmailAccount(MailAccount):
    kind = "gmail"
    supports = Capability.READ | Capability.SEND

    def __init__(self, *, id: str, address: str, name: str, granted: Capability, refresh_token: str,
                 on_lost: Optional[Callable[[str, str], Awaitable[None]]] = None, transport: Optional[httpx.AsyncBaseTransport] = None):
        super().__init__(id=id, address=address, name=name, granted=granted)
        self._refresh_token, self._on_lost, self._transport = refresh_token, on_lost, transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=30, transport=self._transport)

    async def _token(self, fresh: bool = False) -> str:
        cached = _tokens.get(self.id)
        if cached and not fresh and cached[1] > time.time() + 60:
            return cached[0]
        async with _locks.setdefault(self.id, asyncio.Lock()):
            cached = _tokens.get(self.id)
            if cached and not fresh and cached[1] > time.time() + 60:
                return cached[0]
            async with self._client() as client:
                res = await client.post(TOKEN_URL, data={"grant_type": "refresh_token", "refresh_token": self._refresh_token,
                                                         "client_id": settings.GOOGLE_MAIL_CLIENT_ID, "client_secret": settings.GOOGLE_MAIL_CLIENT_SECRET})
            if res.status_code == 400 and (res.json() if res.content else {}).get("error") == "invalid_grant":
                _tokens.pop(self.id, None)
                if self._on_lost:
                    await self._on_lost(self.id, "Google no longer accepts this account's sign-in: connect it again.")
                raise AccessLost("Google no longer accepts this account's sign-in: connect it again.")
            if res.status_code != 200:
                raise MailError(f"Google's sign-in answered {res.status_code}.")
            body = res.json()
            _tokens[self.id] = (body["access_token"], time.time() + int(body.get("expires_in", 3600)))
            return body["access_token"]

    async def _call(self, method: str, path: str, **kwargs) -> httpx.Response:
        """One Gmail API call, signing in again once if the access token was refused."""
        for attempt in (0, 1):
            token = await self._token(fresh=attempt == 1)
            async with self._client() as client:
                res = await client.request(method, f"{API}{path}", headers={"Authorization": f"Bearer {token}"}, **kwargs)
            if res.status_code != 401:
                return res
        return res

    # ---- sending -------------------------------------------------------------------------------------

    async def _send(self, message: Outbound) -> SentReceipt:
        raw = base64.urlsafe_b64encode(build(message, self.address, self.name).as_bytes()).decode()
        try:
            res = await self._call("POST", "/messages/send", json={"raw": raw})
        except httpx.HTTPError as e:
            raise MailError("Google couldn't be reached.") from e
        if res.status_code == 200:
            return SentReceipt(provider_id=res.json().get("id"))
        if res.status_code == 403:
            raise MailError("Google refused: this account wasn't given permission to send.", permanent=True)
        if res.status_code == 400:
            raise MailError("Google refused the message (the address may be invalid).", permanent=True)
        raise MailError(f"Google answered {res.status_code} when sending.")

    # ---- reading -------------------------------------------------------------------------------------

    async def _profile_history(self) -> str:
        res = await self._call("GET", "/profile")
        if res.status_code != 200:
            raise MailError(f"Google answered {res.status_code} for the mailbox.")
        return str(res.json()["historyId"])

    async def _fetch(self, cursor: Optional[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[bytes]]:
        try:
            if cursor is None or not cursor.get("history_id"):
                return {"history_id": await self._profile_history()}, []
            ids: List[str] = []
            page, latest = None, cursor["history_id"]
            while len(ids) < MAX_PER_READ:
                params = {"startHistoryId": cursor["history_id"], "historyTypes": "messageAdded", "labelId": "INBOX"}
                if page:
                    params["pageToken"] = page
                res = await self._call("GET", "/history", params=params)
                if res.status_code == 404:  # too old for Google: start over from now
                    return {"history_id": await self._profile_history()}, []
                if res.status_code != 200:
                    raise MailError(f"Google answered {res.status_code} when reading.")
                body = res.json()
                latest = str(body.get("historyId") or latest)
                for h in body.get("history", []):
                    for added in h.get("messagesAdded", []):
                        mid = added.get("message", {}).get("id")
                        if mid and mid not in ids:
                            ids.append(mid)
                page = body.get("nextPageToken")
                if not page:
                    break
            out: List[bytes] = []
            for mid in ids[:MAX_PER_READ]:
                res = await self._call("GET", f"/messages/{mid}", params={"format": "raw"})
                if res.status_code == 200:
                    out.append(base64.urlsafe_b64decode(res.json()["raw"] + "=="))
            return {"history_id": latest}, out
        except httpx.HTTPError as e:
            raise MailError("Google couldn't be reached.") from e

    # ---- checking ------------------------------------------------------------------------------------

    async def _test(self, needs: Capability) -> TestResult:
        try:
            res = await self._call("GET", "/profile")
        except AccessLost as e:
            return TestResult(False, str(e))
        except (MailError, httpx.HTTPError) as e:
            return TestResult(False, f"Couldn't reach Google: {e}")
        if res.status_code == 403 and Capability.READ not in self.granted:
            return TestResult(True, "Signed in (this account may only send).")
        if res.status_code != 200:
            return TestResult(False, f"Google answered {res.status_code}.")
        address = res.json().get("emailAddress", "")
        if address.lower() != self.address.lower():
            return TestResult(False, f"Signed in as {address}, not {self.address}.")
        return TestResult(True, f"Signed in as {address}.")
