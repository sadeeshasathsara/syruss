"""
Everything secret in this service is sealed here, three ways:

  master keys     Fernet keys from MAIL_MASTER_KEYS (newest first; every one still opens what it sealed, so adding a key
                  rotates without losing anything; `rotate` re-seals under the newest). They seal what is kept in the
                  database: accounts' secrets and config, OAuth tokens, the box private key, and mail read from mailboxes.
  box             a public key people can seal TO and only this service can open: an ephemeral X25519 key, HKDF-SHA256,
                  AES-256-GCM. The admin app seals a mailbox password in the browser with it, so the server and Kafka only
                  ever carry ciphertext. The fingerprint lets an owner check they are sealing to the right key.
  signatures      Ed25519: commands are signed by the server; this service holds only the server's public key.

Layout of a box: ephemeral public key (32) | nonce (12) | ciphertext + tag. Key = HKDF-SHA256(secret, salt = ephemeral
public key | recipient public key, info = b"syruss-mail-seal-v1"); the info is also the GCM associated data.
"""
import base64
import hashlib
import json
import os
from typing import Any, Dict, List, Optional, Tuple

from cryptography.exceptions import InvalidSignature
from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, x25519
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from mail.config import settings

INFO = b"syruss-mail-seal-v1"
SEALED = b"S1:"   # starts bytes sealed by `seal_bytes`; bytes without it were stored before sealing and are read as they are


# ---- master keys -----------------------------------------------------------------------------------

def master_keys() -> List[str]:
    """The keys, newest first. MAIL_SECRETS_KEY (the old single key) still opens what it sealed."""
    keys = [k.strip() for k in settings.MAIL_MASTER_KEYS.split(",") if k.strip()]
    legacy = settings.MAIL_SECRETS_KEY.strip()
    if legacy and legacy not in keys:
        keys.append(legacy)
    return keys


def _fernet() -> MultiFernet:
    keys = master_keys()
    if not keys:
        # Development only (check_config refuses a real server without a key)
        keys = [base64.urlsafe_b64encode(hashlib.sha256(b"syruss-mail-development-key").digest()).decode()]
    return MultiFernet([Fernet(k.encode()) for k in keys])


def seal(values: Dict[str, Any]) -> str:
    return _fernet().encrypt(json.dumps(values).encode()).decode()


def unseal(token: Optional[str]) -> Dict[str, Any]:
    if not token:
        return {}
    try:
        return json.loads(_fernet().decrypt(token.encode()))
    except InvalidToken as e:
        raise ValueError("A sealed mail secret couldn't be opened (MAIL_MASTER_KEYS changed?).") from e


def seal_bytes(data: bytes) -> bytes:
    return SEALED + _fernet().encrypt(data)


def open_bytes(blob: bytes) -> bytes:
    if not blob.startswith(SEALED):
        return blob
    try:
        return _fernet().decrypt(blob[len(SEALED):])
    except InvalidToken as e:
        raise ValueError("Sealed mail couldn't be opened (MAIL_MASTER_KEYS changed?).") from e


def rotate(token: str) -> str:
    """The same secret, sealed under the newest master key."""
    return _fernet().rotate(token.encode()).decode()


def generate_master_key() -> str:
    return Fernet.generate_key().decode()


# ---- the box (what people seal to) -----------------------------------------------------------------

def _raw_public(key: x25519.X25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def new_box_keypair() -> Tuple[bytes, bytes]:
    """(private, public), 32 bytes each."""
    private = x25519.X25519PrivateKey.generate()
    return private.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()), _raw_public(private)


def _box_key(secret: bytes, ephemeral_public: bytes, recipient_public: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=ephemeral_public + recipient_public, info=INFO).derive(secret)


def box_seal(recipient_public: bytes, data: bytes, ephemeral_private: Optional[bytes] = None, nonce: Optional[bytes] = None) -> bytes:
    """What the admin app's browser does (and tests): seal `data` so only the holder of the private key can open it.
    `ephemeral_private` and `nonce` are only for the test vector that pins this format (the server has its own copy)."""
    ephemeral = x25519.X25519PrivateKey.from_private_bytes(ephemeral_private) if ephemeral_private else x25519.X25519PrivateKey.generate()
    ephemeral_public = _raw_public(ephemeral)
    secret = ephemeral.exchange(x25519.X25519PublicKey.from_public_bytes(recipient_public))
    nonce = nonce or os.urandom(12)
    return ephemeral_public + nonce + AESGCM(_box_key(secret, ephemeral_public, recipient_public)).encrypt(nonce, data, INFO)


def box_open(private: bytes, recipient_public: bytes, blob: bytes) -> bytes:
    if len(blob) < 32 + 12 + 16:
        raise ValueError("Not a sealed box.")
    ephemeral_public, nonce, ciphertext = blob[:32], blob[32:44], blob[44:]
    try:
        secret = x25519.X25519PrivateKey.from_private_bytes(private).exchange(x25519.X25519PublicKey.from_public_bytes(ephemeral_public))
        return AESGCM(_box_key(secret, ephemeral_public, recipient_public)).decrypt(nonce, ciphertext, INFO)
    except Exception as e:  # noqa: BLE001  (a wrong key, a damaged box: one answer, nothing about why)
        raise ValueError("The sealed box couldn't be opened.") from e


def fingerprint(public: bytes) -> str:
    """What an owner compares: the first 10 bytes of the key's SHA-256, in groups (A1B2-C3D4-E5F6-7788-99AA)."""
    digest = hashlib.sha256(public).hexdigest().upper()[:20]
    return "-".join(digest[i:i + 4] for i in range(0, 20, 4))


# ---- signatures (commands from the server) ---------------------------------------------------------

def new_signing_keypair() -> Tuple[bytes, bytes]:
    """(private, public), 32 bytes each. The server keeps the private key; this service is given the public one."""
    private = ed25519.Ed25519PrivateKey.generate()
    return (private.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()),
            private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))


def sign(private: bytes, message: bytes) -> bytes:
    return ed25519.Ed25519PrivateKey.from_private_bytes(private).sign(message)


DIRECTORY_CONTEXT = b"syruss-mail-directory-v1"


def directory_message(public_key_b64: str, fingerprint: str) -> bytes:
    """What the mail service signs about its box key: the key people seal secrets to, and its fingerprint."""
    return DIRECTORY_CONTEXT + b"|" + public_key_b64.encode() + b"|" + fingerprint.encode()


def verify(public_b64: str, message: bytes, signature: bytes) -> bool:
    try:
        ed25519.Ed25519PublicKey.from_public_bytes(base64.b64decode(public_b64)).verify(signature, message)
        return True
    except (InvalidSignature, ValueError, TypeError):
        return False
