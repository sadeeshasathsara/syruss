"""
Answers that browsers and proxies treat carefully, and requests that are too big to be real.

Two small pieces of middleware that sit in front of everything:
  * SecurityHeaders: every answer says it is data, not a page (nosniff, no framing, no referrer), API answers are never
    cached, and in production the connection is pinned to HTTPS.
  * RequestSizeLimit: a body larger than MAX_REQUEST_MB is refused, before it is read when it says its length, else as it arrives.
"""
import json
from fastapi import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from app.core.config import settings

_API = ("/api/", "/internal/")


class SecurityHeaders:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")

        async def with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = {k.lower(): v for k, v in message.get("headers", [])}
                add = {
                    b"x-content-type-options": b"nosniff",
                    b"referrer-policy": b"no-referrer",
                    b"x-frame-options": b"DENY",
                    b"permissions-policy": b"camera=(), microphone=(), geolocation=(), payment=()",
                    b"cross-origin-resource-policy": b"same-site",
                }
                if path.startswith(_API):
                    add[b"cache-control"] = b"no-store"
                    add[b"content-security-policy"] = b"default-src 'none'; frame-ancestors 'none'"
                if settings.is_production:
                    add[b"strict-transport-security"] = b"max-age=31536000; includeSubDomains"
                message["headers"] = [*message.get("headers", []), *[(k, v) for k, v in add.items() if k not in headers]]
            await send(message)

        await self.app(scope, receive, with_headers)


class _TooLarge(HTTPException):
    """Raised while the body is read: an HTTP error, so FastAPI answers 413 with it rather than 'could not parse the body'."""

    def __init__(self) -> None:
        super().__init__(status_code=413, detail=f"That request is larger than the {settings.MAX_REQUEST_MB} MB this server accepts.")


class RequestSizeLimit:
    """
    Refuses a body over MAX_REQUEST_MB: at once when it announces its length, and as it arrives when it doesn't (a chunked
    upload has no Content-Length, so the header alone can't be trusted to stop it).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    @staticmethod
    async def _refuse(send: Send) -> None:
        body = json.dumps({"detail": f"That request is larger than the {settings.MAX_REQUEST_MB} MB this server accepts."}).encode()
        await send({"type": "http.response.start", "status": 413, "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        cap = settings.MAX_REQUEST_MB * 1_000_000
        length = dict(scope.get("headers", [])).get(b"content-length", b"")
        if length.isdigit() and int(length) > cap:
            return await self._refuse(send)

        received = 0
        started = False

        async def counted() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > cap:
                    raise _TooLarge()
            return message

        async def watched(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counted, watched)
        except _TooLarge:
            if not started:
                await self._refuse(send)
