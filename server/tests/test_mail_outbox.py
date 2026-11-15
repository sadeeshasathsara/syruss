"""
Mail goes to the mail service: sending queues the message (sealed) in the outbox, the relay publishes it on Kafka once
and drops it, and the mail service's report of how it went is recorded.
"""
import base64
import json
import pytest
from sqlalchemy import delete, select
from app.core.config import settings
from app.models.mail import MailOutbox
from app.services.notifications import transport
from app.services.notifications.mail_relay import MailRelay, record
from tests.conftest import AsyncSessionLocal

# What the mail service reads (mail/accounts/base.Outbound)
JOB_KEYS = {"id", "purpose", "to", "subject", "text", "html", "sender_name", "reply_to", "message_id", "in_reply_to", "references",
            "automatic", "attachments", "meta"}


class FakeProducer:
    def __init__(self):
        self.sent = []

    async def send_and_wait(self, topic, key=None, value=None):
        self.sent.append((topic, key.decode(), json.loads(value)))


@pytest.fixture
async def service(monkeypatch):
    monkeypatch.setattr(settings, "MAIL_BACKEND", "service")
    async with AsyncSessionLocal() as db:
        await db.execute(delete(MailOutbox))
        await db.commit()


async def test_mail_is_queued_sealed_published_once_and_dropped(service):
    await transport.send(transport.EmailMessage(
        to="pat@example.com", subject="Your sign-in code", text="Your code is 482913", html="<p>Your code is 482913</p>",
        attachments=[("notes.csv", b"a,b\n1,2\n", "text/csv")], meta={"invitation": "inv_1"}))
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(MailOutbox))).scalar_one()
    assert row.status == "queued" and row.purpose == "app.notifications" and "482913" not in (row.payload or "")

    relay, producer = MailRelay(), FakeProducer()
    relay._producer = producer
    assert await relay.publish_pending() == 1
    assert await relay.publish_pending() == 0  # never published twice
    topic, key, job = producer.sent[0]
    assert topic == settings.MAIL_OUTBOUND_TOPIC and key == row.id and set(job) == JOB_KEYS
    assert job["id"] == row.id and job["text"] == "Your code is 482913" and job["sender_name"] is None and job["reply_to"] is None  # the mail service fills in its own
    assert base64.b64decode(job["attachments"][0]["data_b64"]) == b"a,b\n1,2\n" and job["meta"] == {"invitation": "inv_1"}
    async with AsyncSessionLocal() as db:
        row = await db.get(MailOutbox, row.id)
    assert row.status == "published" and row.payload is None and row.published_at is not None


async def test_how_it_went_is_recorded(service):
    await transport.send(transport.EmailMessage(to="pat@example.com", subject="Hello", text="Hi", html="<p>Hi</p>", purpose="support"))
    async with AsyncSessionLocal() as db:
        row_id = (await db.execute(select(MailOutbox.id))).scalar_one()
    assert await record({"id": row_id, "status": "retrying", "attempts": 1, "error": "The mail server couldn't be reached"})
    assert await record({"id": row_id, "status": "sent", "attempts": 2, "error": None})
    assert await record({"id": row_id, "status": "retrying", "attempts": 1, "error": "late"})  # arrived out of order: ignored
    async with AsyncSessionLocal() as db:
        row = await db.get(MailOutbox, row_id)
    assert row.status == "sent" and row.attempts == 2 and row.last_error is None and row.done_at is not None
    assert not await record({"id": "not-ours", "status": "sent"})
    assert not await record({"id": row_id, "status": "exploded"})


async def test_what_cant_be_queued_says_why(service):
    with pytest.raises(transport.EmailError, match="too large"):
        await transport.send(transport.EmailMessage(to="a@b.c", subject="Big", text="x", html="x",
                                                    attachments=[("big.pdf", b"x" * 800_000, "application/pdf")]))
    with pytest.raises(transport.EmailError, match="Unknown mail purpose"):
        await transport.send(transport.EmailMessage(to="a@b.c", subject="s", text="x", html="x", purpose="marketing"))
