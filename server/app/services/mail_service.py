"""
The server's line to the mail service's internal API (mail/api.py): accounts, routes, the delivery log, connecting Google,
and collecting mail read from mailboxes. Every call carries MAIL_SERVICE_TOKEN. The mail service never hands back a
password or a token, so nothing here holds one.
"""
import logging
from typing import Any, Dict, List, Optional
import httpx
from app.core.config import settings

logger = logging.getLogger("core.mail_service")


class MailServiceError(Exception):
    """The mail service refused or couldn't be reached; `status` and the text are safe to pass on to staff."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


# Tests swap in a transport that answers like the mail service.
transport: Optional[httpx.AsyncBaseTransport] = None


async def call(method: str, path: str, *, json: Any = None, params: Optional[Dict[str, Any]] = None, raw: bool = False) -> Any:
    try:
        async with httpx.AsyncClient(base_url=settings.MAIL_SERVICE_URL.rstrip("/"), timeout=30, transport=transport,
                                     headers={"Authorization": f"Bearer {settings.MAIL_SERVICE_TOKEN}"}) as client:
            res = await client.request(method, path, json=json, params=params)
    except httpx.HTTPError as e:
        raise MailServiceError("The mail service can't be reached right now.") from e
    if res.status_code >= 400:
        try:
            detail = res.json().get("detail")
        except ValueError:
            detail = None
        if res.status_code == 401:
            logger.error("The mail service refused our token (MAIL_SERVICE_TOKEN differs between the server and mail.env)")
            raise MailServiceError("The mail service didn't accept the server's key.", 502)
        raise MailServiceError(detail if isinstance(detail, str) else f"The mail service answered {res.status_code}.",
                               res.status_code if res.status_code < 500 else 502)
    if raw:
        return res.content
    return res.json() if res.content else None


async def status() -> Dict[str, Any]:
    return await call("GET", "/status")


async def inbound() -> List[Dict[str, Any]]:
    return await call("GET", "/inbound")


async def inbound_raw(item_id: str) -> bytes:
    return await call("GET", f"/inbound/{item_id}", raw=True)


async def inbound_done(item_id: str) -> None:
    await call("DELETE", f"/inbound/{item_id}")
