"""
Jobs: the built-in ones are always there, more can be added, each is routed to a mailbox and has settings of its own, and
every job that reads has a poller of its own.
"""
import asyncio

import pytest
from mail.accounts import MemoryAccount
from mail.accounts.base import Capability, MailNotPermitted
from mail.delivery import with_defaults
from mail.inbound import Pollers, Reader
from mail.operations import OperationError, Operations
from mail.router import Router
from mail.tests.conftest import job


async def test_the_built_in_jobs_are_there_and_cannot_be_removed(store, fallback):
    keys = {j.key: j for j in await store.jobs()}
    assert set(keys) == {"notifications", "documents", "promotions", "support", "accounts"}
    assert keys["support"].label == "Help" and keys["support"].direction == "both" and keys["documents"].direction == "read"
    assert all(j.system for j in keys.values())
    ops = Operations(store, Router(store, fallback))
    with pytest.raises(OperationError, match="built in"):
        await ops.delete_job("documents")
    await store.seed_jobs()                                   # a second start changes nothing
    assert len(await store.jobs()) == 5


async def test_an_owner_adds_changes_and_removes_a_job(store, fallback):
    ops = Operations(store, Router(store, fallback))
    made = await ops.create_job("partners", "Partner mail", "both", "Mail with partners", {"sender_name": "Partners", "poll_seconds": "120"})
    assert made.settings == {"sender_name": "Partners", "poll_seconds": 120} and not made.system
    assert (await Router(store, fallback).needs("partners")) == Capability.READ | Capability.SEND

    with pytest.raises(OperationError, match="already"):
        await ops.create_job("partners", "Again", "send")
    for bad in (("Partners", "Name", "send"), ("p", "Name", "send"), ("ok_key", "", "send"), ("ok_key", "Name", "sideways")):
        with pytest.raises(OperationError):
            await ops.create_job(*bad)
    with pytest.raises(OperationError, match="isn't a setting"):
        await ops.update_job("partners", settings={"colour": "red"})
    with pytest.raises(OperationError, match="under 15"):
        await ops.update_job("partners", settings={"poll_seconds": 5})

    changed = await ops.update_job("partners", label="Partner mail (EU)", settings={"reply_to": "team@example.com"})
    assert changed.label == "Partner mail (EU)" and changed.settings == {"reply_to": "team@example.com"}

    await ops.delete_job("partners")
    assert await store.job("partners") is None
    with pytest.raises(MailNotPermitted):
        await Router(store, fallback).account_for("partners")


async def test_a_job_is_routed_to_an_account_that_can_do_its_work(store, fallback):
    ops = Operations(store, Router(store, fallback))
    sender = await ops.add_imap(by="t", address="out@example.com", name="Syruss", config={"smtp_host": "smtp.example.com"},
                                secret={"smtp_password": "pw", "imap_password": ""}, jobs=["promotions"])
    assert (await store.routes()) == {"promotions": sender.id}
    with pytest.raises(OperationError, match="read and send"):
        await ops.route("support", sender.id)             # help needs a mailbox that reads too
    await ops.route("promotions", None)                   # back to the environment's account
    assert (await store.routes()) == {}
    with pytest.raises(OperationError, match="no job called"):
        await ops.add_imap(by="t", address="x@example.com", name="X", config={"smtp_host": "s"}, secret={}, jobs=["nonsense"])


async def test_removing_a_job_takes_its_route_and_read_position_with_it(store, fallback):
    ops = Operations(store, Router(store, fallback))
    await ops.create_job("extra", "Extra", "send")
    acct = await ops.add_imap(by="t", address="a@example.com", name="A", config={"smtp_host": "s"}, secret={}, jobs=["extra"])
    assert (await store.routes()) == {"extra": acct.id}
    await ops.delete_job("extra")
    assert await store.routes() == {}


async def test_a_job_s_own_settings_beat_the_service_s_defaults():
    plain = with_defaults(job())
    mine = with_defaults(job(), {"sender_name": "Billing team", "reply_to": "billing@example.com"})
    assert plain.sender_name == "Syruss" and mine.sender_name == "Billing team" and mine.reply_to == "billing@example.com"
    # What the message itself says still wins
    assert with_defaults(job(sender_name="Pat"), {"sender_name": "Billing team"}).sender_name == "Pat"


async def test_each_reading_job_has_its_own_poller_and_a_removed_job_loses_it(store):
    reader_account = MemoryAccount(id="env", address="in@syruss.test", granted=Capability.READ | Capability.SEND)
    router = Router(store, reader_account)
    pollers = Pollers(Reader(store, router))
    ops = Operations(store, router)
    await ops.create_job("partners", "Partners", "read")
    await ops.create_job("outgoing", "Outgoing only", "send")
    await pollers.sync()
    try:
        assert set(pollers.tasks) == {"support", "documents", "partners"}            # the ones that read; not the send-only
        await ops.delete_job("partners")
        await pollers.sync()
        assert set(pollers.tasks) == {"support", "documents"}
    finally:
        for task in pollers.tasks.values():
            task.cancel()
        await asyncio.gather(*pollers.tasks.values(), return_exceptions=True)


async def test_a_failing_mailbox_does_not_stop_the_others(store):
    router = Router(store, MemoryAccount(id="env", address="in@syruss.test", granted=Capability.READ | Capability.SEND))
    calls = []

    class Spy(Reader):
        async def read(self, purpose):
            calls.append(purpose)
            if purpose == "documents":
                return await super().read(purpose)
            raise RuntimeError("this one breaks")

    pollers = Pollers(Spy(store, router))
    await pollers.sync()
    await asyncio.sleep(0.4)
    try:
        assert {"support", "documents"} <= set(calls)             # both were tried: one failing didn't stop the other
        assert not any(t.done() for t in pollers.tasks.values())  # and the failing one is still polling
    finally:
        for task in pollers.tasks.values():
            task.cancel()
        await asyncio.gather(*pollers.tasks.values(), return_exceptions=True)
