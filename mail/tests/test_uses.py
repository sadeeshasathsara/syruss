"""
Functions of the application that use mail (an email destination, support, invitations...) and the job each one uses: a message names
its function, this service looks up the job, then the job's mailbox. Until an owner chooses, a function uses its default job.
"""
import base64
import json
import time

import pytest
from sqlalchemy import select

from mail import sealing
from mail.commands import Commands, canonical
from mail.config import settings
from mail.delivery import Delivery
from mail.directory import Directory
from mail.operations import OperationError, Operations
from mail.router import Router
from mail.store import JobRow
from mail.tests.conftest import job
from mail.tests.test_mail import add_account, route


@pytest.fixture(autouse=True)
async def reset_uses(store):
    yield
    for use in await store.uses():
        await store.set_use_job(use.key, None)


async def test_the_built_in_functions_use_their_default_jobs(store):
    uses = {u.key: u for u in await store.uses()}
    assert {"app.notifications", "destinations.email", "support.mail", "documents.inbound"} <= set(uses)
    assert await store.resolve("destinations.email") == "notifications" and await store.resolve("support.mail") == "support"
    assert await store.resolve("promotions") == "promotions"            # a job's own key is used as it is


async def test_a_message_goes_through_the_job_its_function_uses(store, fallback):
    ops = Operations(store, Router(store, fallback))
    await ops.create_job("records", "Records", "send")
    sender = await _account(store, "acc-records", ("send",))
    await route(store, "records", sender)
    await ops.assign_use("destinations.email", "records")
    delivery = Delivery(store, Router(store, fallback))
    assert await delivery.accept(job("rec-1", purpose="destinations.email"))
    async with store.session() as s:
        row = (await s.execute(select(JobRow).where(JobRow.id == "rec-1"))).scalar_one()
        assert row.purpose == "records" and row.meta["use"] == "destinations.email"   # the log is by job; the function is kept
    event = await delivery.attempt("rec-1")
    assert event["account_id"] == "acc-records"                           # it went out through the job's mailbox (a made-up server: it will not get through)
    await ops.assign_use("destinations.email", None)                      # back to the default
    assert await store.resolve("destinations.email") == "notifications"


async def _account(store, id, granted):
    await add_account(store, id, granted=granted)
    return id


async def test_a_function_can_only_use_a_job_that_can_do_what_it_does(store, fallback):
    ops = Operations(store, Router(store, fallback))
    with pytest.raises(OperationError, match="needs a job that can read"):
        await ops.assign_use("documents.inbound", "promotions")              # promotions only sends
    with pytest.raises(OperationError, match="No such job"):
        await ops.assign_use("destinations.email", "nothing")
    with pytest.raises(OperationError, match="No such function"):
        await ops.assign_use("made.up", "promotions")
    await ops.assign_use("destinations.email", "promotions")                 # a sending job fits a sending function


async def test_removing_a_job_returns_its_functions_to_their_defaults(store, fallback):
    ops = Operations(store, Router(store, fallback))
    await ops.create_job("temp_out", "Temporary", "send")
    await ops.assign_use("app.notifications", "temp_out")
    assert await store.resolve("app.notifications") == "temp_out"
    await ops.delete_job("temp_out")
    assert await store.resolve("app.notifications") == "notifications"


async def test_the_application_registers_its_functions_and_a_choice_survives_it(store, fallback, monkeypatch):
    private, public = sealing.new_signing_keypair()
    monkeypatch.setattr(settings, "MAIL_TRUSTED_SERVER_KEY", base64.b64encode(public).decode())
    replies = []

    async def reply(r):
        replies.append(r)

    commands = Commands(store, Operations(store, Router(store, fallback)), reply, None)

    async def send(kind, payload):
        envelope = {"id": f"{kind}-{time.time_ns()}", "type": kind, "ts": time.time(), "by": "system:server", "payload": payload}
        envelope["sig"] = base64.b64encode(sealing.sign(private, canonical(envelope))).decode()
        return await commands.handle(json.dumps(envelope).encode())

    new = {"key": "billing.reminders", "label": "Payment reminders", "description": "Reminders about unpaid invoices.", "direction": "send", "default_job": "notifications"}
    assert (await send("use.register", {"uses": [new]}))["ok"]
    assert await store.resolve("billing.reminders") == "notifications"
    await Operations(store, Router(store, fallback)).create_job("billing_out", "Billing", "send")
    assert (await send("use.assign", {"key": "billing.reminders", "job": "billing_out"}))["ok"]
    assert (await send("use.register", {"uses": [{**new, "label": "Reminders"}]}))["ok"]       # registered again at the next start
    use = await store.use("billing.reminders")
    assert use.label == "Reminders" and use.job == "billing_out"                              # the label refreshed, the choice kept
    bad = await send("use.register", {"uses": [{**new, "key": "notadot"}]})
    assert bad["ok"] is False and "dots" in bad["error"]
    await store.set_use_job("billing.reminders", None)


async def test_the_directory_tells_the_server_which_job_each_function_uses(store, fallback):
    ops = Operations(store, Router(store, fallback))
    await ops.create_job("records2", "Records", "send")
    await ops.assign_use("destinations.email", "records2")
    state = await Directory(store, Router(store, fallback), lambda *a: None).snapshot()
    assert state["use:destinations.email"] == {**state["use:destinations.email"], "job": "records2", "assigned": True, "default_job": "notifications",
                                              "direction": "send", "needs": ["send"]}
    assert state["use:app.notifications"]["job"] == "notifications" and state["use:app.notifications"]["assigned"] is False
