"""
Checks the token that came with each event before any work starts.

The core server signs the token (EdDSA); this service holds only the public key, so it can verify a
token but never make one. A token must match the event it arrived with, so an event cannot be replayed
into another workspace or under another person's name.
"""
import hashlib
from functools import lru_cache
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from agentic_framework.config import settings
from agentic_framework.events import AgentEvent

ISSUER = "syruss-core"
AUDIENCE = "syruss-agent-tools"


class BadToken(Exception):
    pass


@lru_cache(maxsize=1)
def _public_key():
    if settings.AGENT_TOKEN_PUBLIC_KEY:
        return serialization.load_pem_public_key(settings.AGENT_TOKEN_PUBLIC_KEY.replace("\n", "\n").encode())
    if settings.ENV.lower() == "production":
        raise RuntimeError("Set AGENT_TOKEN_PUBLIC_KEY in production.")
    # Development: the same keypair the core derives from the shared dev secret
    return Ed25519PrivateKey.from_private_bytes(hashlib.sha256(settings.AGENT_TOKEN_SECRET.encode()).digest()).public_key()


def check_startup() -> None:
    _public_key()


def verify(event: AgentEvent) -> None:
    """Raises BadToken unless the token is genuine, unexpired, and for exactly this event."""
    try:
        claims = jwt.decode(event.agent_token, _public_key(), algorithms=["EdDSA"], audience=AUDIENCE, issuer=ISSUER,
                            options={"require": ["exp", "iat", "tid", "tpl", "evt", "jti"]})
    except jwt.PyJWTError as exc:
        raise BadToken(f"the event's token is not valid ({exc.__class__.__name__})") from exc
    actor = (event.actor or {}).get("id") or "system"
    if (claims["tid"], claims["tpl"], claims["evt"], claims.get("sub")) != (event.tenant_id, event.template_id, event.event_id, actor):
        raise BadToken("the event's token was issued for a different workspace, event or person")
