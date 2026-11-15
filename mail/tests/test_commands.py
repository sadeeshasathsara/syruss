"""
Commands from the server: only a signed, recent, never-seen command is carried out; passwords arrive sealed and never come
back out; the directory and every answer hold nothing secret.
"""
import argparse
import base64
import io
import json
import sys
import time
import uuid

import pytest
from sqlalchemy import select

from mail import cli, sealing
from mail.commands import Commands, canonical
from mail.config import settings
from mail.delivery import Delivery
from mail.directory import Directory
from mail.operations import Operations
from mail.router import Router
from mail.store import AccountRow
from mail.tests.conftest import job

SECRET = "s3cret-Pa55word"


@pytest.fixture
def server_key(monkeypatch):
    private, public = sealing.new_signing_keypair()
    monkeypatch.setattr(settings, "MAIL_TRUSTED_SERVER_KEY", base64.b64encode(public).decode())
    return private


@pytest.fixture
def commands(store, fallback, server_key):
    replies = []

    async def reply(r):
        replies.append(r)

    published = []

    async def changed():
        published.append(time.time())

    c = Commands(store, Operations(store, Router(store, fallback)), reply, changed)
    c.replies, c.published = replies, published
    return c


def signed(private, kind, payload=None, by="usr_owner", ts=None, command_id=None) -> bytes:
    envelope = {"id": command_id or uuid.uuid4().hex, "type": kind, "ts": ts if ts is not None else time.time(), "by": by, "payload": payload or {}}
    envelope["sig"] = base64.b64encode(sealing.sign(private, canonical(envelope))).decode()
    return json.dumps(envelope).encode()


async def sealed_for(store, **secrets) -> str:
    _, public = await store.box()
    return base64.b64encode(sealing.box_seal(public, json.dumps(secrets).encode())).decode()


# ---- who may command ---------------------------------------------------------------------------------

async def test_nothing_is_carried_out_without_a_trusted_server_key(commands, server_key, monkeypatch, store):
    monkeypatch.setattr(settings, "MAIL_TRUSTED_SERVER_KEY", "")
    reply = await commands.handle(signed(server_key, "job.create", {"key": "x1", "label": "X", "direction": "send"}))
    assert reply["ok"] is False and "isn't set up" in reply["error"]
    assert await store.job("x1") is None


async def test_a_forged_stale_or_repeated_command_is_refused(commands, server_key, store):
    payload = {"key": "partners", "label": "Partners", "direction": "send"}
    stranger, _ = sealing.new_signing_keypair()
    forged = await commands.handle(signed(stranger, "job.create", payload))
    assert forged["ok"] is False and "Not signed" in forged["error"]

    stale = await commands.handle(signed(server_key, "job.create", payload, ts=time.time() - 3600))
    assert stale["ok"] is False and "too old" in stale["error"]

    tampered = json.loads(signed(server_key, "job.create", payload))
    tampered["payload"]["key"] = "other"                       # changed after it was signed
    assert (await commands.handle(json.dumps(tampered).encode()))["ok"] is False
    assert await store.job("partners") is None and await store.job("other") is None

    same_id = uuid.uuid4().hex
    first = await commands.handle(signed(server_key, "job.create", payload, command_id=same_id))
    again = await commands.handle(signed(server_key, "job.create", {**payload, "key": "partners2"}, command_id=same_id))
    assert first["ok"] is True and again["ok"] is False and "already received" in again["error"]
    assert await store.job("partners") is not None and await store.job("partners2") is None    # a replay did nothing

    assert await commands.handle(b"not json") is None          # nothing to answer
    unknown = await commands.handle(signed(server_key, "mailbox.delete_everything"))
    assert unknown["ok"] is False and "doesn't know" in unknown["error"]


async def test_every_command_is_logged_with_who_asked(commands, server_key, store):
    from mail.store import CommandRow
    await commands.handle(signed(server_key, "job.create", {"key": "logged", "label": "L", "direction": "send"}, by="usr_pat", command_id="c-1"))
    await commands.handle(signed(server_key, "job.delete", {"key": "documents"}, by="usr_pat", command_id="c-2"))
    async with store.session() as s:
        rows = {r.id: r for r in (await s.execute(select(CommandRow))).scalars()}
    assert rows["c-1"].ok and rows["c-1"].by == "usr_pat" and rows["c-1"].type == "job.create"
    assert not rows["c-2"].ok and "built in" in rows["c-2"].error


# ---- connecting a mailbox: the password is sealed from the browser to here and stays here -------------

async def test_a_password_arrives_sealed_and_is_never_shown_again(commands, server_key, store, fallback):
    sealed = await sealed_for(store, smtp_password=SECRET, imap_password=SECRET)
    command = signed(server_key, "account.add_imap", {
        "address": "Help@Example.com", "name": "Help", "smtp_host": "smtp.example.com", "smtp_username": "help@example.com",
        "imap_host": "imap.example.com", "sealed": sealed, "jobs": ["support", "documents"]})
    assert SECRET.encode() not in command and SECRET not in sealed       # what crosses Kafka is ciphertext

    reply = await commands.handle(command)
    assert reply["ok"] is True and set(reply["data"]["granted"]) == {"send", "read"}
    assert SECRET not in json.dumps(reply)                                # not in the answer
    async with store.session() as s:
        row = (await s.execute(select(AccountRow))).scalar_one()
    assert SECRET not in (row.secret or "") and SECRET not in json.dumps(row.config)       # not at rest
    assert sealing.unseal(row.secret) == {"smtp_password": SECRET, "imap_password": SECRET}   # but the service can use it
    assert (await store.routes()) == {"support": row.id, "documents": row.id}
    assert commands.published                                              # the directory was told

    # The directory says what exists and holds nothing secret
    sent = []

    async def keep(topic, key, value):
        sent.append((key, value))

    await Directory(store, Router(store, fallback), keep).publish_all("mail.directory")
    everything = b" ".join(v for _, v in sent if v)
    assert SECRET.encode() not in everything and b"smtp_password" not in everything and b"help@example.com" in everything.lower()
    keys = {k for k, _ in sent}
    assert {"meta", "job:support", "job:documents", "account:env", f"account:{row.id}"} <= keys


async def test_a_secret_sealed_to_another_key_is_refused_without_a_hint_about_why(commands, server_key, store):
    _, someone_elses_public = sealing.new_box_keypair()
    wrong = base64.b64encode(sealing.box_seal(someone_elses_public, json.dumps({"smtp_password": SECRET}).encode())).decode()
    reply = await commands.handle(signed(server_key, "account.add_imap", {
        "address": "a@example.com", "smtp_host": "smtp.example.com", "sealed": wrong}))
    assert reply["ok"] is False and "couldn't be opened" in reply["error"] and SECRET not in json.dumps(reply)
    assert await store.accounts() == []


async def test_a_mailbox_that_cannot_do_a_job_s_work_is_refused_before_anything_is_kept(commands, server_key, store):
    reply = await commands.handle(signed(server_key, "account.add_imap", {
        "address": "send@example.com", "smtp_host": "smtp.example.com", "jobs": ["support"]}))
    assert reply["ok"] is False and "read and send" in reply["error"]
    assert await store.accounts() == [] and await store.routes() == {}


# ---- jobs and the log ------------------------------------------------------------------------------

async def test_jobs_are_managed_and_the_directory_follows_including_a_tombstone(commands, server_key, store, fallback):
    sent = []

    async def keep(topic, key, value):
        sent.append((key, value))

    directory = Directory(store, Router(store, fallback), keep)
    assert (await commands.handle(signed(server_key, "job.create", {"key": "partners", "label": "Partners", "direction": "both",
                                                                       "settings": {"sender_name": "Partners"}})))["ok"]
    await directory.publish_all("mail.directory")
    entry = json.loads(dict(sent)["job:partners"])
    assert entry["label"] == "Partners" and entry["settings"] == {"sender_name": "Partners"} and entry["needs"] == ["read", "send"]
    assert entry["account"]["from_environment"] is True and entry["system"] is False

    assert (await commands.handle(signed(server_key, "job.update", {"key": "partners", "label": "Partner mail"})))["ok"]
    assert (await commands.handle(signed(server_key, "job.delete", {"key": "partners"})))["ok"]
    sent.clear()
    await directory.publish_all("mail.directory")
    assert ("job:partners", None) in sent                          # gone: a tombstone, so compaction drops it


async def test_the_delivery_log_and_a_retry_come_through_commands(commands, server_key, store, delivery, fallback):
    fallback.fail = None
    from mail.accounts.base import MailError
    fallback.fail = MailError("refused", permanent=True)
    from mail.worker import MailWorker
    await MailWorker(delivery).take(job(id="j-1").model_dump_json().encode())
    log = (await commands.handle(signed(server_key, "jobs.log", {"status": "dead"})))
    assert log["ok"] and [j["id"] for j in log["data"]["jobs"]] == ["j-1"] and log["data"]["jobs"][0]["subject"] == "Your code"
    assert "Your code is 123456" not in json.dumps(log)           # the log never carries the message text
    nothing = await commands.handle(signed(server_key, "job.retry", {"id": "nope"}))
    assert nothing["ok"] is False


async def test_the_directory_publishes_the_key_secrets_are_sealed_to(store, fallback):
    sent = []

    async def keep(topic, key, value):
        sent.append((key, value))

    await Directory(store, Router(store, fallback), keep).publish_all("mail.directory")
    meta = json.loads(dict(sent)["meta"])
    _, public = await store.box()
    assert base64.b64decode(meta["public_key"]) == public and meta["fingerprint"] == sealing.fingerprint(public)
    assert b"private" not in dict(sent)["meta"]


# ---- the command line ------------------------------------------------------------------------------

async def test_connect_from_the_command_line_seals_the_password_straight_into_the_database(store, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO(SECRET + "\n"))
    args = argparse.Namespace(address="no-reply@example.com", name="Syruss", smtp_host="smtp.example.com", smtp_port=587, smtp_security="starttls",
                              smtp_username="no-reply@example.com", imap_host=None, imap_port=993, imap_no_ssl=False, imap_username=None,
                              imap_folder="INBOX", jobs=None, password_stdin=True, no_test=True)
    assert await cli._connect(args) == 0
    out = capsys.readouterr()
    assert SECRET not in out.out + out.err and "notifications, accounts" in out.out
    async with store.session() as s:
        row = (await s.execute(select(AccountRow))).scalar_one()
    assert row.address == "no-reply@example.com" and SECRET not in (row.secret or "") and sealing.unseal(row.secret)["smtp_password"] == SECRET
    assert set((await store.routes())) == {"notifications", "accounts"}
