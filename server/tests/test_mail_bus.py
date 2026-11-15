"""
The bus to the mail service: commands are signed in the format the mail service checks, a command waits for its own answer, the
directory is rebuilt from the compacted topic (a tombstone removes a thing), and nothing is guessed when the mail service is silent.
"""
import asyncio
import base64
import json

import pytest

from app.core.config import settings
from app.services import mail_identity, mail_uses
from app.services.mail_bus import MailBus, MailBusError, box_seal, canonical, sign
from tests.mail_fakes import BOX_PRIVATE, BOX_PUBLIC, directory


# The same vector is asserted in mail/tests/test_sealing.py: the two services cannot drift apart without a test failing.
VECTOR_ENVELOPE = {"id": "cmd-1", "type": "job.create", "ts": 1700000000.5, "by": "usr_1",
                   "payload": {"key": "partners", "label": "Partners ü", "direction": "send"}}
VECTOR_SIGNATURE = "2fujoiWBA7efQjfbpa5pMU2PBqdC9Mu3kQ5CRZ/M0QfzIFcH+mLZr+3ZHE0ctfx/Ntzcvv4FTSs6SWkBzTLyCg=="
VECTOR_BOX = ("VxR2nRFr92Q2rnS8eT0sMK0ZA8WaxSc4BcfiaYtBDDYAAQIDBAUGBwgJCgt2fEkp2hRqJA6vwdZcT5KTlfWoxb0U01Nwyu9efe1qW7wDCtLLVQ==")


def test_the_signature_and_the_sealed_box_match_the_mail_service_byte_for_byte():
    assert base64.b64encode(sign(bytes([7]) * 32, canonical(VECTOR_ENVELOPE))).decode() == VECTOR_SIGNATURE
    assert canonical({**VECTOR_ENVELOPE, "sig": "ignored"}) == canonical(VECTOR_ENVELOPE)       # `sig` is never part of what is signed
    box = box_seal(BOX_PUBLIC, b'{"smtp_password":"pw"}', bytes(range(101, 133)), bytes(range(12)))
    assert base64.b64encode(box).decode() == VECTOR_BOX


class Wire:
    """A producer that hands each command to a function playing the mail service, and its answer back to the bus."""

    def __init__(self, bus, answer):
        self.bus, self.answer, self.sent = bus, answer, []

    async def send_and_wait(self, topic, key, value, headers=None):
        self.sent.append((topic, key, json.loads(value)))
        if topic == settings.MAIL_COMMANDS_TOPIC:
            reply = self.answer(json.loads(value))
            if reply is not None:
                asyncio.get_running_loop().call_soon(lambda: asyncio.ensure_future(self.bus._replies(reply["id"], json.dumps(reply).encode())))

    async def stop(self):
        pass


@pytest.fixture
def bus(monkeypatch):
    monkeypatch.setattr(settings, "MAIL_BACKEND", "service")
    private = bytes([7]) * 32
    monkeypatch.setattr(settings, "MAIL_COMMAND_SIGNING_KEY", base64.b64encode(private).decode())
    return MailBus()


async def test_a_command_is_signed_sent_and_answered_by_its_own_id(bus):
    def mail_service(envelope):
        return {"id": envelope["id"], "type": envelope["type"], "ok": True, "data": {"key": envelope["payload"]["key"]}}

    bus._producer = Wire(bus, mail_service)
    data = await bus.command("job.create", {"key": "partners", "label": "P", "direction": "send"}, by="usr_pat")
    assert data == {"key": "partners"}
    topic, key, envelope = bus._producer.sent[0]
    assert topic == settings.MAIL_COMMANDS_TOPIC and key == envelope["id"].encode() and envelope["by"] == "usr_pat" and envelope["type"] == "job.create"
    from app.services.mail_bus import ed25519
    signature = base64.b64decode(envelope["sig"])
    public = ed25519.Ed25519PrivateKey.from_private_bytes(bytes([7]) * 32).public_key()
    public.verify(signature, canonical(envelope))                                                  # what the mail service checks
    assert bus.pending == {}                                                                       # nothing left waiting


async def test_a_refusal_comes_back_as_an_error_with_the_reason(bus):
    bus._producer = Wire(bus, lambda e: {"id": e["id"], "type": e["type"], "ok": False, "error": "The Help job needs to read and send."})
    with pytest.raises(MailBusError, match="needs to read and send") as e:
        await bus.command("job.route", {"purpose": "support", "account_id": "x"})
    assert e.value.status == 422


async def test_a_silent_mail_service_times_out_with_a_plain_message(bus):
    bus._producer = Wire(bus, lambda e: None)
    with pytest.raises(MailBusError, match="didn't answer") as e:
        await bus.command("directory.refresh", timeout=0.1)
    assert e.value.status == 504 and bus.pending == {}


async def test_an_answer_for_another_server_is_ignored(bus):
    bus._producer = Wire(bus, lambda e: {"id": "someone-elses", "type": "x", "ok": True})
    with pytest.raises(MailBusError, match="didn't answer"):
        await bus.command("directory.refresh", timeout=0.1)


async def test_nothing_is_sent_without_a_signing_key_or_when_mail_does_not_go_through_the_service(bus, monkeypatch):
    bus._producer = Wire(bus, lambda e: None)
    monkeypatch.setattr(settings, "MAIL_COMMAND_SIGNING_KEY", "")
    with pytest.raises(MailBusError, match="MAIL_COMMAND_SIGNING_KEY") as e:
        await bus.command("directory.refresh")
    assert e.value.status == 503 and bus._producer.sent == []
    monkeypatch.setattr(settings, "MAIL_BACKEND", "console")
    with pytest.raises(MailBusError, match="isn't going through the mail service"):
        await bus.command("directory.refresh")


async def test_the_directory_is_rebuilt_from_the_topic_and_a_tombstone_removes_a_thing(bus):
    seen = []

    async def listener(key, entry):
        seen.append((key, entry is None))

    bus.listeners.append(listener)
    for key, value in directory().items():
        await bus._directory(key, json.dumps(value).encode())
    overview = bus.overview()
    assert set(overview["purposes"]) == {"notifications", "support", "documents"} and [a["id"] for a in overview["accounts"]] == ["env", "google-1"]
    await bus._directory("job:documents", None)
    await bus._directory("account:google-1", None)
    assert "documents" not in bus.overview()["purposes"] and [a["id"] for a in bus.overview()["accounts"]] == ["env"]
    assert ("job:documents", True) in seen


async def test_a_secret_is_sealed_to_the_key_in_the_directory_and_only_that_key_opens_it(bus):
    with pytest.raises(MailBusError, match="hasn't told this server its key"):
        bus.seal({"smtp_password": "pw"})
    await bus._directory("meta", json.dumps(directory()["meta"]).encode())
    sealed = base64.b64decode(bus.seal({"smtp_password": "s3cret"}))
    assert b"s3cret" not in sealed
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import x25519
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    ephemeral, nonce, ciphertext = sealed[:32], sealed[32:44], sealed[44:]
    secret = x25519.X25519PrivateKey.from_private_bytes(BOX_PRIVATE).exchange(x25519.X25519PublicKey.from_public_bytes(ephemeral))
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=ephemeral + BOX_PUBLIC, info=b"syruss-mail-seal-v1").derive(secret)
    assert json.loads(AESGCM(key).decrypt(nonce, ciphertext, b"syruss-mail-seal-v1")) == {"smtp_password": "s3cret"}


async def test_the_addresses_come_from_the_directory_and_are_never_guessed(bus, monkeypatch):
    monkeypatch.setattr(mail_identity, "bus", bus)
    monkeypatch.setattr(mail_uses, "bus", bus)
    mail_identity.forget()

    async def nobody(timeout: float = 0.0) -> bool:
        return False

    monkeypatch.setattr(bus, "ready", nobody)
    assert mail_identity.loaded() is False and mail_identity.current() == mail_identity.DEVELOPMENT
    with pytest.raises(MailBusError):
        await mail_identity.ensure(required=True)                      # before the mail service is heard from: no development address
    assert await mail_identity.ensure() == mail_identity.DEVELOPMENT   # a reader that can cope gets it

    for key, value in directory().items():
        await bus._directory(key, json.dumps(value).encode())
    known = await mail_identity.ensure(required=True)
    assert known.from_address == "no-reply@syruss.test" and known.support_address == "help@syruss.test"
    assert known.ours == frozenset({"no-reply@syruss.test", "help@syruss.test"})                   # every mailbox of ours
    await bus._directory("account:google-1", None)
    await bus._directory("job:support", None)
    assert mail_identity.current().support_address == "no-reply@syruss.test"                        # help falls back to the sender
    monkeypatch.setattr(settings, "MAIL_BACKEND", "console")
    mail_identity.forget()
    bus.state.clear()
    assert await mail_identity.ensure(required=True) == mail_identity.DEVELOPMENT      # a server without a mail service (local work, tests)
