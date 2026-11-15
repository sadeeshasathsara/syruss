"""
A Microsoft account (Outlook.com, Microsoft 365), used through Microsoft Graph with the permissions someone approved in the
admin app (oauth.py): Mail.Send to send, Mail.Read to read. Nothing else: it can't delete, move or change mail.

Its sign-in is a refresh token (sealed in the store). Unlike Google's, Microsoft hands out a NEW refresh token with most
refreshes, so each new one is saved at once (`on_secret`) and the old one is no longer relied on. Access tokens are made from
it when needed and kept in memory until they expire. When Microsoft refuses the refresh token (access removed, password
changed, tenant policy), the account is marked "needs reconnecting" until someone connects it again.

Sending: the message is built as MIME here and posted whole (/me/sendMail with a base64 MIME body), so it looks the same as
mail sent any other way.

Reading: a delta query on the Inbox. The first read walks the folder only to learn where it is now (what was there already is
left alone); later reads follow the saved delta link and fetch each new message whole (/messages/{id}/$value). A delta link
that Microsoft no longer knows (410) starts over from now. A read that stops with pages left saves where it stopped.
"""
import asyncio
import base64
import logging
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

import httpx

from mail import apps
from mail.accounts.base import Capability, MailAccount, MailError, Outbound, SentReceipt, TestResult
from mail.accounts.mime import build

logger = logging.getLogger("mail.graph")
API = "https://graph.microsoft.com/v1.0/me"
MAX_PER_READ = 25
MAX_PAGES = 200
_tokens: Dict[str, Tuple[str, float]] = {}   # account id -> (access token, expires at)
_locks: Dict[str, asyncio.Lock] = {}


class AccessLost(MailError):
    """Microsoft refused the sign-in: someone must connect the account again."""


class GraphAccount(MailAccount):
    kind = "graph"
    supports = Capability.READ | Capability.SEND

    def __init__(self, *, id: str, address: str, name: str, granted: Capability, refresh_token: str,
                 on_lost: Optional[Callable[[str, str], Awaitable[None]]] = None,
                 on_secret: Optional[Callable[[str, Dict[str, Any]], Awaitable[None]]] = None,
                 transport: Optional[httpx.AsyncBaseTransport] = None):
        super().__init__(id=id, address=address, name=name, granted=granted)
        self._refresh_token, self._on_lost, self._on_secret, self._transport = refresh_token, on_lost, on_secret, transport

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
            app = apps.get("microsoft")
            if app is None:
                raise MailError("Microsoft isn't set up: add its app under Email › Provider apps.")
            from mail.oauth import microsoft_url
            async with self._client() as client:
                res = await client.post(microsoft_url(app, "token"), data={
                    "grant_type": "refresh_token", "refresh_token": self._refresh_token, "client_id": app["client_id"],
                    "client_secret": app["client_secret"]})
            body = res.json() if res.content else {}
            if res.status_code in (400, 401) and body.get("error") in ("invalid_grant", "interaction_required", "consent_required"):
                _tokens.pop(self.id, None)
                why = "Microsoft no longer accepts this account's sign-in: connect it again."
                if self._on_lost:
                    await self._on_lost(self.id, why)
                raise AccessLost(why)
            if res.status_code != 200:
                raise MailError(f"Microsoft's sign-in answered {res.status_code}.")
            _tokens[self.id] = (body["access_token"], time.time() + int(body.get("expires_in", 3600)))
            new = body.get("refresh_token")
            if new and new != self._refresh_token:
                self._refresh_token = new
                if self._on_secret:
                    await self._on_secret(self.id, {"refresh_token": new})
            return body["access_token"]

    async def _call(self, method: str, url: str, **kwargs) -> httpx.Response:
        """One Graph call (a path under /me, or a full link Graph gave), signing in again once if the access token was refused."""
        target = url if url.startswith("https://") else f"{API}{url}"
        headers = kwargs.pop("headers", {})
        for attempt in (0, 1):
            token = await self._token(fresh=attempt == 1)
            async with self._client() as client:
                res = await client.request(method, target, headers={"Authorization": f"Bearer {token}", **headers}, **kwargs)
            if res.status_code != 401:
                return res
        return res

    # ---- sending -------------------------------------------------------------------------------------

    async def _send(self, message: Outbound) -> SentReceipt:
        raw = base64.b64encode(build(message, self.address, self.name).as_bytes())
        try:
            res = await self._call("POST", "/sendMail", content=raw, headers={"Content-Type": "text/plain"})
        except httpx.HTTPError as e:
            raise MailError("Microsoft couldn't be reached.") from e
        if res.status_code in (200, 202):
            return SentReceipt(provider_id=None)
        if res.status_code == 403:
            raise MailError("Microsoft refused: this account wasn't given permission to send.", permanent=True)
        if res.status_code == 400:
            raise MailError("Microsoft refused the message (the address may be invalid).", permanent=True)
        if res.status_code == 429:
            raise MailError("Microsoft asked to slow down; trying again later.")
        raise MailError(f"Microsoft answered {res.status_code} when sending.")

    # ---- reading -------------------------------------------------------------------------------------

    async def _walk(self, link: str) -> Tuple[List[str], Dict[str, Any]]:
        """Follows delta pages from `link`; returns (new message ids, the cursor to continue from)."""
        ids: List[str] = []
        for _ in range(MAX_PAGES):
            res = await self._call("GET", link, headers={"Prefer": "odata.maxpagesize=50"})
            if res.status_code == 410:
                raise LookupError("delta expired")
            if res.status_code != 200:
                raise MailError(f"Microsoft answered {res.status_code} when reading.")
            body = res.json()
            for item in body.get("value", []):
                if "@removed" not in item and item.get("id") and item["id"] not in ids:
                    ids.append(item["id"])
            if body.get("@odata.deltaLink"):
                return ids, {"delta": body["@odata.deltaLink"]}
            link = body.get("@odata.nextLink")
            if not link:
                raise MailError("Microsoft's answer ended without saying where to read from next.")
            if len(ids) >= MAX_PER_READ:
                return ids, {"next": link}
        return ids, {"next": link}

    async def _start_now(self) -> Dict[str, Any]:
        """The cursor for 'from now on': walks the folder without fetching anything."""
        link = "/mailFolders/inbox/messages/delta?$select=id"
        for _ in range(MAX_PAGES):
            res = await self._call("GET", link, headers={"Prefer": "odata.maxpagesize=200"})
            if res.status_code != 200:
                raise MailError(f"Microsoft answered {res.status_code} for the mailbox.")
            body = res.json()
            if body.get("@odata.deltaLink"):
                return {"delta": body["@odata.deltaLink"]}
            link = body.get("@odata.nextLink")
            if not link:
                break
        raise MailError("Microsoft's mailbox listing didn't finish.")

    async def _fetch(self, cursor: Optional[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[bytes]]:
        try:
            link = (cursor or {}).get("next") or (cursor or {}).get("delta")
            if not link:
                return await self._start_now(), []
            try:
                ids, new_cursor = await self._walk(link)
            except LookupError:   # Microsoft no longer knows that delta link: start over from now
                return await self._start_now(), []
            out: List[bytes] = []
            for mid in ids[:MAX_PER_READ]:
                res = await self._call("GET", f"/messages/{mid}/$value")
                if res.status_code == 200:
                    out.append(res.content)
            if len(ids) > MAX_PER_READ:
                logger.warning("Read %d of %d new messages; the rest were skipped", MAX_PER_READ, len(ids))
            return new_cursor, out
        except httpx.HTTPError as e:
            raise MailError("Microsoft couldn't be reached.") from e

    # ---- checking ------------------------------------------------------------------------------------

    async def _test(self, needs: Capability) -> TestResult:
        try:
            res = await self._call("GET", "?$select=mail,userPrincipalName")
        except AccessLost as e:
            return TestResult(False, str(e))
        except (MailError, httpx.HTTPError) as e:
            return TestResult(False, f"Couldn't reach Microsoft: {e}")
        if res.status_code != 200:
            return TestResult(False, f"Microsoft answered {res.status_code}.")
        body = res.json()
        address = (body.get("mail") or body.get("userPrincipalName") or "").lower()
        if address and address != self.address.lower():
            return TestResult(False, f"Signed in as {address}, not {self.address}.")
        return TestResult(True, f"Signed in as {address or self.address}.")
