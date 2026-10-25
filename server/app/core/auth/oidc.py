"""
Keycloak access tokens. The signature, issuer, audience and expiry are verified against the
realm's published keys; the result is identity only (who), never permissions (what).
"""
import asyncio
import logging
from typing import Optional
import jwt
from jwt import PyJWKClient
from starlette.requests import HTTPConnection
from app.core.auth.context import Identity
from app.core.auth.provider import BaseIAMProvider, unauthorized
from app.core.config import settings

logger = logging.getLogger("core.auth.oidc")
ALGORITHMS = ["RS256", "ES256"]  # never "none", never HMAC with a public key


class OidcProvider(BaseIAMProvider):
    def __init__(self, issuer: Optional[str] = None, audience: Optional[str] = None, jwks_url: Optional[str] = None,
                 allowed_clients: Optional[list] = None) -> None:
        self.issuer = (issuer or settings.OIDC_ISSUER).rstrip("/")
        self.audience = audience or settings.OIDC_AUDIENCE
        self.allowed_clients = allowed_clients if allowed_clients is not None else settings.oidc_allowed_clients
        url = jwks_url or settings.OIDC_JWKS_URL or f"{self.issuer}/protocol/openid-connect/certs"
        # Keys are cached; an unknown key id triggers one refresh (key rotation)
        self._jwks = PyJWKClient(url, cache_keys=True, lifespan=3600, timeout=5)

    def _decode(self, token: str) -> dict:
        key = self._jwks.get_signing_key_from_jwt(token).key
        return jwt.decode(
            token, key, algorithms=ALGORITHMS, audience=self.audience, issuer=self.issuer,
            leeway=settings.OIDC_CLOCK_SKEW_SECONDS, options={"require": ["exp", "iat", "sub", "iss", "aud"]},
        )

    async def verify(self, token: str) -> dict:
        try:
            claims = await asyncio.to_thread(self._decode, token)
        except jwt.PyJWTError as exc:
            logger.info("Rejected token: %s", exc)
            raise unauthorized("The access token is not valid.") from exc
        except Exception as exc:  # key server unreachable
            logger.warning("Could not verify token: %s", exc)
            raise unauthorized("The access token could not be verified.") from exc
        if self.allowed_clients and claims.get("azp") not in self.allowed_clients:
            raise unauthorized("This token was issued to a client that may not call the API.")
        return claims

    async def authenticate(self, request: HTTPConnection) -> Identity:
        header = request.headers.get("Authorization") or ""
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise unauthorized("Sign in to continue.")
        claims = await self.verify(token.strip())
        return Identity(
            user_id=claims["sub"],
            username=claims.get("name") or claims.get("preferred_username") or claims.get("email"),
            email=(claims.get("email") or "").lower() or None,
            email_verified=bool(claims.get("email_verified")),
            given_name=claims.get("given_name"),
            family_name=claims.get("family_name"),
            picture=claims.get("picture"),
            auth_time=int(claims["auth_time"]) if isinstance(claims.get("auth_time"), (int, float)) else None,
        )
