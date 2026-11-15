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
