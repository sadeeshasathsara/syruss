"""
Scoped tokens for the agent service.

The core server signs a token (an EdDSA JWT) for every agent event it publishes. It is bound to one
(tenant, workspace) and one event, and names the person the work is for. The agent service holds only
the public key: it can check a token but cannot make one, so it can never widen its own scope. In
production the keys are set explicitly; in development a keypair is derived from AGENT_TOKEN_SECRET.
"""
import hashlib
import time
import uuid
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from app.core.config import settings

ISSUER = "syruss-core"
AUDIENCE = "syruss-agent-tools"


@dataclass(frozen=True)
class AgentScope:
    tenant_id: str
    template_id: str
    event_id: str
    actor_id: str = ""
    token_id: str = ""


def _pem(value: str) -> bytes:
    return value.replace("\\n", "\n").encode()


@lru_cache(maxsize=1)
def _keys():
    """(private key or None, public key)."""
    if settings.AGENT_TOKEN_PRIVATE_KEY:
        private = serialization.load_pem_private_key(_pem(settings.AGENT_TOKEN_PRIVATE_KEY), password=None)
        return private, private.public_key()
    if settings.AGENT_TOKEN_PUBLIC_KEY:
        return None, serialization.load_pem_public_key(_pem(settings.AGENT_TOKEN_PUBLIC_KEY))
    if settings.is_production:
        raise RuntimeError("Set AGENT_TOKEN_PRIVATE_KEY (and give the agent AGENT_TOKEN_PUBLIC_KEY) in production.")
    private = Ed25519PrivateKey.from_private_bytes(hashlib.sha256(settings.AGENT_TOKEN_SECRET.encode()).digest())
    return private, private.public_key()


def check_agent_keys() -> None:
    """Fails startup when the signing key is missing (production) or this process cannot sign."""
    private, _ = _keys()
    if private is None:
        raise RuntimeError("The core server needs AGENT_TOKEN_PRIVATE_KEY to sign agent tokens.")


def mint_agent_token(tenant_id: str, template_id: str, event_id: str, ttl_seconds: Optional[int] = None,
                     actor_id: str = "") -> str:
    private, _ = _keys()
    if private is None:
        raise RuntimeError("This process has no signing key.")
    now = int(time.time())
    claims = {
        "iss": ISSUER, "aud": AUDIENCE, "sub": actor_id or "system", "tid": tenant_id, "tpl": template_id, "evt": event_id,
        "jti": uuid.uuid4().hex, "iat": now, "exp": now + (ttl_seconds if ttl_seconds is not None else settings.AGENT_TOKEN_TTL_SECONDS),
    }
    return jwt.encode(claims, private, algorithm="EdDSA")


def verify_agent_token(token: str) -> AgentScope:
    """Raises ValueError if the token is malformed, forged, or expired."""
    _, public = _keys()
    try:
        claims = jwt.decode(token, public, algorithms=["EdDSA"], audience=AUDIENCE, issuer=ISSUER,
                            options={"require": ["exp", "iat", "tid", "tpl", "evt", "jti"]})
    except jwt.ExpiredSignatureError:
        raise ValueError("token expired")
    except jwt.PyJWTError as exc:
        raise ValueError(f"invalid token ({exc.__class__.__name__})")
    actor = claims.get("sub", "")
    return AgentScope(tenant_id=claims["tid"], template_id=claims["tpl"], event_id=claims["evt"],
                      actor_id="" if actor == "system" else actor, token_id=claims["jti"])
