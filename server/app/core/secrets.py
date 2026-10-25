"""
Credentials at rest. Knowledge source secrets (tokens, passwords, API keys) are stored
encrypted and only decrypted at the moment the server calls the source. They are never
returned to the browser and never shown to the agent.
"""
import base64
import hashlib
import json
from typing import Any, Dict
from cryptography.fernet import Fernet, InvalidToken
from app.core.config import settings


def _fernet() -> Fernet:
    key = settings.SECRETS_ENCRYPTION_KEY
    if not key:
        # Development only: a stable key derived from the token secret.
        key = base64.urlsafe_b64encode(hashlib.sha256(f"secrets:{settings.AGENT_TOKEN_SECRET}".encode()).digest()).decode()
    return Fernet(key.encode())


def seal(values: Dict[str, Any]) -> str:
    return _fernet().encrypt(json.dumps(values).encode()).decode()


def unseal(token: str | None) -> Dict[str, Any]:
    if not token:
        return {}
    try:
        return json.loads(_fernet().decrypt(token.encode()))
    except InvalidToken as exc:
        raise RuntimeError("Stored credentials cannot be decrypted; the encryption key has changed.") from exc
