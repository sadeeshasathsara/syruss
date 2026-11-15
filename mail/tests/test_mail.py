"""
The mail interface and delivery: an account opens only for what it was given, purposes route to accounts (or the
fallback), a job is sent once, failures are tried again later and given up in the end, and nothing of the message is
kept once it is done.
"""
from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy import update
from mail.accounts import MemoryAccount, SmtpImapAccount, SmtpServer, ImapServer, account_from_row
from mail.accounts.base import Capability, MailError, MailNotPermitted, Outbound
from mail.accounts.mime import build
from mail.config import MailSettings, check_config
from mail.delivery import backoff
from mail.router import Router
from mail.store import AccountRow, JobRow, RouteRow, seal, unseal
from mail.worker import MailWorker
from tests.conftest import job


# ---- the interface -----------------------------------------------------------------------------------

async def test_an_account_opens_only_for_what_it_was_given():
    sender = MemoryAccount(granted=Capability.SEND)
    handle = sender.open(Capability.SEND)
    await handle.send(job())
    with pytest.raises(MailNotPermitted):
        sender.open(Capability.READ)
    with pytest.raises(MailNotPermitted):
        sender.open(Capability.READ | Capability.SEND)
    # Opened to send, it can't read, even though the account could
    both = MemoryAccount()
    with pytest.raises(MailNotPermitted):
        await both.open(Capability.SEND).fetch()
    # What an account is given is limited to what its kind can do, and to what it was configured with
    smtp_only = SmtpImapAccount(id="a", address="a@example.com", name="A", smtp=SmtpServer("smtp.example.com"))
    assert smtp_only.granted == Capability.SEND
    full = SmtpImapAccount(id="b", address="b@example.com", name="B", smtp=SmtpServer("smtp.example.com"), imap=ImapServer("imap.example.com"))
    assert full.granted == Capability.READ | Capability.SEND


async def test_reading_starts_from_now():
    box = MemoryAccount()
    box.inbox.append(b"old")
    handle = box.open(Capability.READ)
    cursor, first = await handle.fetch(None)
    assert first == []                       # what was there already is left alone
    box.inbox.append(b"new")
    cursor, fresh = await handle.fetch(cursor)
    assert fresh == [b"new"]
    assert (await handle.fetch(cursor))[1] == []


def test_the_message_is_from_the_account_and_threads():
    mime = build(job(sender_name="Syruss Support", message_id="<t-1042.ab12cd34@syruss.test>", in_reply_to="<x@mail>",
                     references=["<x@mail>"], automatic=False), "help@syruss.test", "Syruss")
    assert mime["From"] == "Syruss Support <help@syruss.test>" and mime["Message-ID"] == "<t-1042.ab12cd34@syruss.test>"
    assert mime["In-Reply-To"] == "<x@mail>" and mime["Auto-Submitted"] is None
    assert build(job(), "no-reply@syruss.test", "Syruss")["Auto-Submitted"] == "auto-generated"


def test_secrets_are_sealed():
    sealed = seal({"smtp_password": "hunter2"})
    assert "hunter2" not in sealed and unseal(sealed) == {"smtp_password": "hunter2"}


def test_a_real_server_refuses_development_values():
    dev = dict(_env_file=None, MAIL_BACKEND="smtp", POSTGRES_PASSWORD="postgrespassword")
    check_config(MailSettings(**dev))
    for env in ("test", "production"):
        with pytest.raises(RuntimeError, match=f"as {env}") as e:
            check_config(MailSettings(**dev, ENV=env))
        for name in ("POSTGRES_PASSWORD", "MAIL_SECRETS_KEY", "MAIL_SMTP_HOST", "MAIL_FROM", "MAIL_SERVICE_TOKEN"):
            assert name in str(e.value)
    with pytest.raises(RuntimeError, match="MAIL_BACKEND must be smtp"):
        check_config(MailSettings(_env_file=None, ENV="test", MAIL_BACKEND="console"))
    ready = dict(_env_file=None, ENV="production", MAIL_BACKEND="smtp", POSTGRES_PASSWORD="real", MAIL_SECRETS_KEY="k",
                 MAIL_SMTP_HOST="smtp.example.com", MAIL_FROM="no-reply@example.com", MAIL_RELAY_PASSWORD="a-long-random-relay-password",
                 MAIL_SERVICE_TOKEN="a-long-random-service-token-123")
    check_config(MailSettings(**ready))
    with pytest.raises(RuntimeError, match="MAIL_RELAY_PASSWORD"):
        check_config(MailSettings(**{**ready, "MAIL_RELAY_PASSWORD": "short"}))
    check_config(MailSettings(**{**ready, "MAIL_RELAY_PASSWORD": "", "MAIL_RELAY_PORT": 0}))  # no relay, no password needed
    with pytest.raises(RuntimeError, match="ENV must be one of"):
        check_config(MailSettings(_env_file=None, ENV="prod"))


# ---- routing -----------------------------------------------------------------------------------------

async def add_account(store, id="acc-1", granted=("send", "read"), status="active", **config):
    async with store.session() as s:
        s.add(AccountRow(id=id, kind="smtp_imap", address=f"{id}@example.com", name="Support", status=status, granted=list(granted),
                         config={"smtp_host": "smtp.example.com", **config}, secret=seal({"smtp_password": "x"})))
        await s.commit()


async def route(store, purpose, account_id):
    async with store.session() as s:
        await s.merge(RouteRow(purpose=purpose, account_id=account_id))
        await s.commit()


async def test_purposes_use_their_account_or_the_fallback(store, fallback):
    router = Router(store, fallback)
    account, routed = await router.account_for("notifications")
    assert account is fallback and not routed
    await add_account(store, "acc-1", imap_host="imap.example.com")
    await route(store, "support", "acc-1")
    account, routed = await router.account_for("support")
    assert routed and account.address == "acc-1@example.com" and account.granted == Capability.READ | Capability.SEND
    # Needing reconnecting, or unable to do all the purpose needs: the fallback steps in
    await add_account(store, "acc-2")  # sends, can't read
    await route(store, "support", "acc-2")
    assert (await router.account_for("support"))[0] is fallback
    with pytest.raises(MailNotPermitted):
        Router.check(account_from_row(await store.account("acc-2"), {}), "support")
    with pytest.raises(MailNotPermitted):
        await router.account_for("marketing")


# ---- delivery ----------------------------------------------------------------------------------------

async def test_a_job_is_sent_once_and_not_kept(store, delivery, fallback):
    worker = MailWorker(delivery)
    await worker.take(job().model_dump_json().encode())
    await worker.take(job().model_dump_json().encode())  # Kafka delivered it twice
    assert len(fallback.sent) == 1 and fallback.sent[0].subject == "Your code"
    async with store.session() as s:
        row = await s.get(JobRow, "job-1")
    assert row.status == "sent" and row.attempts == 1 and row.payload is None and row.account_id == "env"


async def test_failures_are_tried_again_later_then_given_up(store, delivery, fallback, monkeypatch):
    from mail.config import settings
    monkeypatch.setattr(settings, "MAX_ATTEMPTS", 3)
    fallback.fail = MailError("The mail server couldn't be reached or didn't accept the message.")
    assert await delivery.accept(job())
    event = await delivery.attempt("job-1")
    assert event["status"] == "retrying" and event["attempts"] == 1
    assert await delivery.attempt("job-1") is None             # not due yet
    assert await delivery.due() == []
    for expected in ("retrying", "dead"):
        async with store.session() as s:
            await s.execute(update(JobRow).values(next_attempt_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
            await s.commit()
        assert await delivery.due() == ["job-1"]
        assert (await delivery.attempt("job-1"))["status"] == expected
    async with store.session() as s:
        row = await s.get(JobRow, "job-1")
    assert row.status == "dead" and row.payload is None and "couldn't be reached" in row.last_error
    assert backoff(1) == timedelta(seconds=30) and backoff(2) == timedelta(seconds=60) and backoff(30) == timedelta(seconds=3600)


async def test_a_refused_address_is_given_up_at_once(delivery, fallback):
    fallback.fail = MailError("The mail server refused that address.", permanent=True)
    await delivery.accept(job())
    assert (await delivery.attempt("job-1"))["status"] == "dead"


async def test_a_job_whose_sender_died_is_taken_again(store, delivery, fallback):
    await delivery.accept(job())
    async with store.session() as s:  # claimed by a process that died mid-send: its lease has run out
        await s.execute(update(JobRow).values(status="sending", next_attempt_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
        await s.commit()
    assert await delivery.due() == ["job-1"]
    assert (await delivery.attempt("job-1"))["status"] == "sent" and len(fallback.sent) == 1


async def test_a_purpose_nothing_can_serve_waits_for_an_admin(store, fallback):
    reader_only = MemoryAccount(id="env", granted=Capability.READ)
    from mail.delivery import Delivery
    d = Delivery(store, Router(store, reader_only))
    await d.accept(job())
    event = await d.attempt("job-1")
    assert event["status"] == "retrying" and "wasn't given send" in event["error"]


async def test_malformed_or_unknown_jobs_are_skipped(delivery, fallback):
    events = []
    worker = MailWorker(delivery)
    worker.publish = lambda e: events.append(e) or _done()  # type: ignore[assignment]
    await worker.take(b"{not json")
    await worker.take(Outbound(id="x", purpose="marketing", to="a@b.c", subject="s", text="t").model_dump_json().encode())
    assert fallback.sent == [] and events[-1]["status"] == "dead" and "marketing" in events[-1]["error"]


async def _done():
    return None
