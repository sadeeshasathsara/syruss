"""
Every outbound call a destination makes goes through here: the address is checked first (no private networks), redirects are never followed,
the reply is read only up to a limit, and a failure comes back as a plain result instead of an exception.
"""
import json
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional
from urllib.parse import urlsplit
import httpx
from app.core.config import settings
from app.services import source_data
from app.services.source_data import SourceError

MAX_REPLY_BYTES = 1_000_000
EXCERPT = 1500


@dataclass
class Reply:
    status: Optional[int] = None
    headers: Dict[str, str] = field(default_factory=dict)
    text: str = ""
    cookies: Dict[str, str] = field(default_factory=dict)
    error: Optional[str] = None          # set when no reply was received at all
    retryable: bool = True               # for an error: whether trying again could help
    elapsed_ms: float = 0.0

    @property
    def ok(self) -> bool:
        return self.error is None and self.status is not None and 200 <= self.status < 300

    @property
    def retry_after(self) -> Optional[int]:
        raw = self.headers.get("retry-after", "")
        return int(raw) if raw.isdigit() else None

    def json(self) -> Any:
        try:
            return json.loads(self.text)
        except ValueError:
            return None


def excerpt(value: Any, limit: int = EXCERPT) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= limit else text[:limit] + f"… ({len(text) - limit} more characters)"


def redact(headers: Dict[str, str]) -> Dict[str, str]:
    """Headers for the log: anything that carries a credential is hidden."""
    hidden = ("authorization", "signature", "api-key", "apikey", "token", "secret", "password", "cookie")
    return {k: ("•••" if any(h in k.lower() for h in hidden) else v) for k, v in headers.items()}


async def call(
    method: str, url: str, *, headers: Optional[Dict[str, str]] = None, json_body: Any = None, content: Optional[bytes] = None,
    data: Optional[Dict[str, Any]] = None, params: Optional[Dict[str, Any]] = None, auth: Optional[httpx.Auth] = None,
    timeout: Optional[float] = None,
) -> Reply:
    """One request. Never raises."""
    if settings.is_production and urlsplit(url).scheme != "https":
        return Reply(error="Destinations must use https in production.", retryable=False)
    try:
        await source_data._check_host(url)
    except SourceError as exc:
        message = str(exc)
        return Reply(error=message, retryable="could not be found" in message)
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(transport=source_data.HTTP_TRANSPORT, timeout=timeout or settings.DELIVERY_TIMEOUT_SECONDS, follow_redirects=False) as client:
            async with client.stream(method, url, headers=headers, json=json_body, content=content, data=data, params=params, auth=auth) as response:
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > MAX_REPLY_BYTES:
                        break
                    chunks.append(chunk)
                reply = Reply(status=response.status_code, headers={k.lower(): v for k, v in response.headers.items()},
                              text=b"".join(chunks).decode(errors="replace"), cookies=dict(response.cookies))
    except httpx.TimeoutException:
        return Reply(error="The other side did not answer in time.", elapsed_ms=(time.perf_counter() - started) * 1000)
    except httpx.HTTPError as exc:
        return Reply(error=f"Could not reach the other side: {type(exc).__name__}.", elapsed_ms=(time.perf_counter() - started) * 1000)
    reply.elapsed_ms = (time.perf_counter() - started) * 1000
    return reply


def failure_from(reply: Reply, what: str = "The other side") -> tuple[str, bool]:
    """(sentence, retryable) for a reply that was not a success."""
    if reply.error:
        return reply.error, reply.retryable
    code = reply.status or 0
    if 300 <= code < 400:
        return f"{what} answered with a redirect (HTTP {code}). Redirects are not followed; use the final address.", False
    if code in (408, 425, 429) or code >= 500:
        return f"{what} answered HTTP {code}.", True
    if code in (401, 403):
        return f"{what} refused the credentials (HTTP {code}).", False
    return f"{what} answered HTTP {code}.", False


async def bearer_from_client_credentials(token_url: str, client_id: str, client_secret: str, scope: str = "") -> tuple[Optional[str], Optional[str]]:
    """(access token, None) or (None, why not) using the OAuth2 client-credentials grant."""
    form = {"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret}
    if scope:
        form["scope"] = scope
    reply = await call("POST", token_url, data=form)
    if not reply.ok:
        return None, failure_from(reply, "The sign-in service")[0]
    token = (reply.json() or {}).get("access_token") if isinstance(reply.json(), dict) else None
    return (token, None) if token else (None, "The sign-in service did not return an access_token.")
