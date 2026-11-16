"""
No mail credential comes from the environment: before a mailbox is connected, mail waits (without using up its tries); a job's
account is a column of the job, and an install that kept it in the old mail_routes table has it copied over once.
"""
import json

from sqlalchemy import text

from mail.accounts import NotConnectedAccount
from mail.delivery import Delivery
from mail.directory import Directory
from mail.router import Router
from mail.store import JobRow
from mail.tests.conftest import job
from mail.tests.test_mail import add_account, route


async def test_mail_waits_for_a_mailbox_and_is_sent_once_one_is_connected(store):
    router = Router(store, NotConnectedAccount(id="env"))
    delivery = Delivery(store, router)
    assert await delivery.accept(job("first-mail"))
    event = await delivery.attempt("first-mail")
    assert event["status"] == "retrying" and event["attempts"] == 0 and "No mailbox is connected" in event["error"]   # tries are not used up
    for _ in range(10):
        await delivery.attempt("first-mail")           # (not due again yet, or waiting again: either way still not dead)
    async with store.session() as s:
        row = await s.get(JobRow, "first-mail")
        assert row.status == "retrying" and row.attempts == 0 and row.payload is not None
        row.next_attempt_at = row.created_at          # the wait is over
        await s.commit()
    await add_account(store, "acc-1", granted=("send",))
    await route(store, "notifications", "acc-1")
    assert await store.routes() == {"notifications": "acc-1"}


async def test_the_directory_has_no_account_that_isnt_there(store):
    sent = []

    async def send(topic, key, value):
        sent.append((key, json.loads(value) if value else None))

    directory = Directory(store, Router(store, NotConnectedAccount(id="env")), send)
    state = await directory.snapshot()
    assert not any(k.startswith("account:") for k in state)
    assert state["job:notifications"]["account"] is None and state["job:notifications"]["routed"] is False


async def test_an_older_installs_routes_are_copied_into_its_jobs_once(store):
    await add_account(store, "acc-old", granted=("send", "read"))
    async with store._engine.begin() as conn:
        await conn.execute(text("CREATE TABLE IF NOT EXISTS mail_routes (purpose VARCHAR(40) PRIMARY KEY, account_id VARCHAR(64) NOT NULL, updated_at TIMESTAMPTZ)"))
        await conn.execute(text("INSERT INTO mail_routes (purpose, account_id) VALUES ('support', 'acc-old') ON CONFLICT DO NOTHING"))
        await store._fold_routes(conn)
        await store._fold_routes(conn)                  # running it again changes nothing
        assert await conn.scalar(text("SELECT to_regclass('public.mail_routes') IS NULL"))
    assert await store.routes() == {"support": "acc-old"}


async def test_a_problem_shows_only_while_it_is_still_true(store, fallback):
    """An old failure through a mailbox that has since been replaced, or one a later message got past, isn't shown."""
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    await add_account(store, "acc-new", granted=("send", "read"), imap_host="imap.example.com")
    async with store.session() as s:
        s.add(JobRow(id="old-fail", purpose="support", to="a@b.c", subject="x", status="dead", attempts=6, last_error="The account env wasn't given read.",
                     account_id="env", created_at=now - timedelta(hours=2)))
        await s.commit()
    directory = Directory(store, Router(store, fallback), lambda *a: None)
    await route(store, "support", "acc-new")                      # the job now goes through another mailbox
    assert (await directory.snapshot())["job:support"]["last_error"] is None
    await route(store, "support", None)                           # back on the one that failed: still true
    assert "wasn't given read" in (await directory.snapshot())["job:support"]["last_error"]
    async with store.session() as s:
        s.add(JobRow(id="later-ok", purpose="support", to="a@b.c", subject="y", status="sent", attempts=1, account_id="env", created_at=now))
        await s.commit()
    assert (await directory.snapshot())["job:support"]["last_error"] is None   # a later message went out


async def test_the_directory_vouches_for_the_key_people_seal_secrets_to(store, fallback):
    """The server is given the signing key, so a key someone else puts on the directory topic is not believed."""
    from mail import sealing
    meta = (await Directory(store, Router(store, fallback), lambda *a: None).snapshot())["meta"]
    message = sealing.directory_message(meta["public_key"], meta["fingerprint"])
    assert sealing.verify(meta["signer"], message, __import__("base64").b64decode(meta["signature"]))
    swapped = sealing.directory_message("c3dhcHBlZC1rZXk=", meta["fingerprint"])
    assert not sealing.verify(meta["signer"], swapped, __import__("base64").b64decode(meta["signature"]))   # another key, same signature: refused
