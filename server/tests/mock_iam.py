"""
Who is calling, in tests: identity from X-* headers (or the test owner). The product itself has no such thing:
it only accepts Keycloak tokens. Tests install this with set_iam_provider so every other layer runs for real.
"""
import time
from starlette.requests import HTTPConnection
from app.core.auth.context import Identity
from app.core.auth.provider import BaseIAMProvider

DEV_USER_ID = "usr_admin_default"
DEV_USER_NAME = "Sadeepa Admin"
DEV_EMAIL = "dev@syruss.test"


class MockIAMProvider(BaseIAMProvider):
    async def authenticate(self, request: HTTPConnection) -> Identity:
        return Identity(
            user_id=request.headers.get("X-User-ID") or DEV_USER_ID,
            username=request.headers.get("X-Username", DEV_USER_NAME),
            email=request.headers.get("X-Email") or (DEV_EMAIL if not request.headers.get("X-User-ID") else None),
            email_verified=request.headers.get("X-Email-Verified", "true") != "false",
            picture=request.headers.get("X-Picture"),
            auth_time=int(request.headers["X-Auth-Time"]) if "X-Auth-Time" in request.headers else int(time.time()),
        )
