"""
A stand-in for the mail service, for tests of everything that talks to it. It plays the part of mail/ on the real bus
(services/mail_bus.py): the directory is loaded through the bus's own parser, commands are recorded and answered as scripted, and
what the server publishes (acknowledgements) is kept.
"""
import base64
import json
from typing import Any, Dict, List, Optional

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import x25519

from app.core.config import settings
from app.services.mail_bus import MailBusError, bus

BOX_PRIVATE = bytes(range(1, 33))
BOX_PUBLIC = x25519.X25519PrivateKey.from_private_bytes(BOX_PRIVATE).public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def account(id_: str, address: str, granted: List[str], jobs: List[str], env: bool = False) -> Dict[str, Any]:
    return {"id": id_, "kind": "smtp_imap", "address": address, "name": "Syruss", "granted": granted, "status": "active", "last_error": None,
            "checked_at": None, "created_at": None if env else "2026-11-15T10:00:00+00:00", "from_environment": env, "hosts": {}, "jobs": jobs}


def job(key: str, label: str, direction: str, account_id: str, address: str, routed: bool, reads: bool, system: bool = True) -> Dict[str, Any]:
    return {"key": key, "label": label, "direction": direction, "description": "", "system": system, "settings": {},
            "needs": {"send": ["send"], "read": ["read"], "both": ["read", "send"]}[direction],
            "account": {"id": account_id, "address": address, "kind": "smtp_imap", "from_environment": account_id == "env"},
            "routed": routed, "reads": reads, "last_sent_at": None, "last_read_at": None, "last_error": None}


def directory() -> Dict[str, Dict[str, Any]]:
    """What a mail service with two mailboxes says: the environment's sends notifications; a connected one serves help."""
    return {
        "meta": {"public_key": base64.b64encode(BOX_PUBLIC).decode(), "fingerprint": "A1B2-C3D4-E5F6-7788-99AA", "providers": {"google": True}},
        "account:env": account("env", "no-reply@syruss.test", ["send"], ["notifications", "accounts", "promotions", "documents"], env=True),
        "account:google-1": account("google-1", "help@syruss.test", ["send", "read"], ["support"]),
        "job:notifications": job("notifications", "Notifications", "send", "env", "no-reply@syruss.test", False, False),
        "job:support": job("support", "Help", "both", "google-1", "help@syruss.test", True, True),
        "job:documents": job("documents", "Document inbound", "read", "env", "no-reply@syruss.test", False, False),
    }


class FakeMail:
    """Install with `await FakeMail.install(monkeypatch)`; then `fake.calls`, `fake.answers[kind] = data`, `fake.refuse(kind, why)`."""

    def __init__(self):
        self.calls: List[tuple] = []
        self.answers: Dict[str, Dict[str, Any]] = {}
        self.refusals: Dict[str, str] = {}
        self.published: List[tuple] = []

    def refuse(self, kind: str, why: str) -> None:
        self.refusals[kind] = why

    async def command(self, kind: str, payload: Optional[Dict[str, Any]] = None, by: str = "", timeout: float = 20.0) -> Dict[str, Any]:
        self.calls.append((kind, payload, by))
        if kind in self.refusals:
            raise MailBusError(self.refusals[kind], 422)
        return self.answers.get(kind, {})

    async def send(self, topic: str, key: str, value: bytes, headers: Optional[list] = None) -> None:
        self.published.append((topic, key, json.loads(value)))

    def payloads(self, kind: str) -> List[Dict[str, Any]]:
        return [p for k, p, _ in self.calls if k == kind]

    @classmethod
    async def install(cls, monkeypatch, entries: Optional[Dict[str, Dict[str, Any]]] = None) -> "FakeMail":
        fake = cls()
        monkeypatch.setattr(settings, "MAIL_BACKEND", "service")
        monkeypatch.setattr(bus, "command", fake.command)
        monkeypatch.setattr(bus, "send", fake.send)
        bus.state.clear()
        bus.loaded.set()
        for key, value in (directory() if entries is None else entries).items():
            await bus._directory(key, json.dumps(value).encode())
        return fake

    @staticmethod
    def forget() -> None:
        bus.state.clear()
        bus.loaded.clear()
        bus.pending.clear()
