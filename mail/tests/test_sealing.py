"""
Sealing: master keys that rotate, the box the admin app seals secrets to, and the signatures commands carry.
"""
import pytest
from mail import sealing
from mail.config import settings


def keys(monkeypatch, master="", legacy=""):
    monkeypatch.setattr(settings, "MAIL_MASTER_KEYS", master)
    monkeypatch.setattr(settings, "MAIL_SECRETS_KEY", legacy)


def test_a_new_master_key_in_front_rotates_without_losing_anything(monkeypatch):
    old, new = sealing.generate_master_key(), sealing.generate_master_key()
    keys(monkeypatch, master=old)
    token = sealing.seal({"password": "s3cret"})
    blob = sealing.seal_bytes(b"Subject: hello")
    assert "s3cret" not in token and b"hello" not in blob

    keys(monkeypatch, master=f"{new},{old}")                  # the new key goes first; the old one still opens
    assert sealing.unseal(token) == {"password": "s3cret"} and sealing.open_bytes(blob) == b"Subject: hello"
    rotated = sealing.rotate(token)

    keys(monkeypatch, master=new)                              # the old key can be dropped once everything is re-sealed
    assert sealing.unseal(rotated) == {"password": "s3cret"}
    with pytest.raises(ValueError):
        sealing.unseal(token)                                  # still under the old key only


def test_the_single_key_from_before_still_opens_old_data(monkeypatch):
    legacy = sealing.generate_master_key()
    keys(monkeypatch, legacy=legacy)
    token = sealing.seal({"a": 1})
    keys(monkeypatch, master=sealing.generate_master_key(), legacy=legacy)
    assert sealing.unseal(token) == {"a": 1}


def test_bytes_kept_before_sealing_are_read_as_they_are_and_a_wrong_key_is_refused(monkeypatch):
    keys(monkeypatch, master=sealing.generate_master_key())
    assert sealing.open_bytes(b"Subject: old row") == b"Subject: old row"
    blob = sealing.seal_bytes(b"x")
    keys(monkeypatch, master=sealing.generate_master_key())
    with pytest.raises(ValueError):
        sealing.open_bytes(blob)


def test_only_the_holder_of_the_private_key_opens_a_box():
    private, public = sealing.new_box_keypair()
    blob = sealing.box_seal(public, b'{"smtp_password": "s3cret"}')
    assert b"s3cret" not in blob and len(blob) > 60
    assert sealing.box_open(private, public, blob) == b'{"smtp_password": "s3cret"}'
    other_private, _ = sealing.new_box_keypair()
    with pytest.raises(ValueError):
        sealing.box_open(other_private, public, blob)
    with pytest.raises(ValueError):
        sealing.box_open(private, public, blob[:-1] + bytes([blob[-1] ^ 1]))      # damaged
    with pytest.raises(ValueError):
        sealing.box_open(private, public, b"short")
    assert sealing.box_seal(public, b"same") != sealing.box_seal(public, b"same")   # a fresh key every time


def test_the_fingerprint_is_short_stable_and_differs_per_key():
    _, a = sealing.new_box_keypair()
    _, b = sealing.new_box_keypair()
    assert sealing.fingerprint(a) == sealing.fingerprint(a) != sealing.fingerprint(b)
    parts = sealing.fingerprint(a).split("-")
    assert len(parts) == 5 and all(len(p) == 4 for p in parts)


def test_a_signature_holds_only_for_the_same_bytes_and_the_same_key():
    import base64
    private, public = sealing.new_signing_keypair()
    sig = sealing.sign(private, b"command")
    pub = base64.b64encode(public).decode()
    assert sealing.verify(pub, b"command", sig)
    assert not sealing.verify(pub, b"command!", sig)
    _, someone_else = sealing.new_signing_keypair()
    assert not sealing.verify(base64.b64encode(someone_else).decode(), b"command", sig)
    assert not sealing.verify("not base64 at all", b"command", sig) and not sealing.verify("", b"command", sig)


# The same vector is asserted in server/tests/test_mail_bus.py: the two services cannot drift apart without a test failing.
def test_the_signature_and_the_sealed_box_match_the_server_byte_for_byte():
    import base64
    from mail.commands import canonical
    envelope = {"id": "cmd-1", "type": "job.create", "ts": 1700000000.5, "by": "usr_1",
                "payload": {"key": "partners", "label": "Partners ü", "direction": "send"}}
    private = bytes([7]) * 32
    assert base64.b64encode(sealing.sign(private, canonical(envelope))).decode() ==         "2fujoiWBA7efQjfbpa5pMU2PBqdC9Mu3kQ5CRZ/M0QfzIFcH+mLZr+3ZHE0ctfx/Ntzcvv4FTSs6SWkBzTLyCg=="
    recipient_private = bytes(range(1, 33))
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import x25519
    recipient_public = x25519.X25519PrivateKey.from_private_bytes(recipient_private).public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    box = sealing.box_seal(recipient_public, b'{"smtp_password":"pw"}', bytes(range(101, 133)), bytes(range(12)))
    assert base64.b64encode(box).decode() == ("VxR2nRFr92Q2rnS8eT0sMK0ZA8WaxSc4BcfiaYtBDDYAAQIDBAUGBwgJCgt2fEkp2hRqJA6vwdZcT5KTlfWoxb0U01Nwyu9efe1qW7wDCtLLVQ==")
    assert sealing.box_open(recipient_private, recipient_public, box) == b'{"smtp_password":"pw"}'
