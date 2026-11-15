"""
The server's side of the mail service (mail/):

    MailRelay   publishes queued mail (mail_outbox) on Kafka (mail.outbound), oldest first, and drops each message once
                it is published. Safe in several server processes (SKIP LOCKED). While Kafka is down mail waits here.
    MailEvents  reads how each message went (mail.events) and records it on its outbox row: sent, retrying (with why),
                or dead. One consumer group across server processes, so each outcome is recorded once.

Mail that was given up shows in the admin app's overview (sysadmin/overview: mail health), never only by email.
"""
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from sqlalchemy import select
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.secrets import unseal
from app.models.mail import MailOutbox

try:
    from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
except ImportError:  # pragma: no cover
    AIOKafkaConsumer = AIOKafkaProducer = None

logger = logging.getLogger("core.mail_relay")
STATUSES = ("sent", "retrying", "dead")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class MailRelay:
    def __init__(self, poll_interval: float = 2.0, batch_size: int = 50):
        self.poll_interval, self.batch_size = poll_interval, batch_size
        self._producer = None
        self._task: Optional[asyncio.Task] = None
        self._wakeup = asyncio.Event()

    async def start(self) -> None:
        if settings.MAIL_BACKEND.lower() != "service":
            return
        if AIOKafkaProducer is None:
            logger.error("aiokafka is not installed; mail stays in the outbox.")
            return
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._producer:
            await self._producer.stop()
            self._producer = None

    def notify(self) -> None:
        """Publish soon instead of waiting for the next poll."""
        self._wakeup.set()

    async def _ensure_producer(self) -> bool:
        if self._producer:
            return True
        try:
            producer = AIOKafkaProducer(bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS, acks="all", max_request_size=1_048_576)
            await producer.start()
            self._producer = producer
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Kafka unavailable (%s); mail is queued in the outbox.", exc)
            return False

    async def _run(self) -> None:
        while True:
            try:
                if await self._ensure_producer():
                    await self.publish_pending()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("Mail relay iteration failed")
            try:
                await asyncio.wait_for(self._wakeup.wait(), timeout=self.poll_interval)
            except asyncio.TimeoutError:
                pass
            self._wakeup.clear()

    async def publish_pending(self) -> int:
        published = 0
        async with AsyncSessionLocal() as db:
            rows = list((await db.execute(
                select(MailOutbox).where(MailOutbox.status == "queued").order_by(MailOutbox.created_at)
                .limit(self.batch_size).with_for_update(skip_locked=True))).scalars().all())
            for row in rows:
                job = unseal(row.payload)
                await self._producer.send_and_wait(settings.MAIL_OUTBOUND_TOPIC, key=row.id.encode(), value=json.dumps(job).encode())
                row.status, row.payload, row.published_at = "published", None, _now()
                published += 1
                await db.commit()  # one at a time: a message published is never published again as new
        return published


async def record(event: Dict[str, Any]) -> bool:
    """Records how one message went on its outbox row. False for an event about mail this server didn't queue."""
    status = event.get("status")
    if status not in STATUSES or not event.get("id"):
        return False
    async with AsyncSessionLocal() as db:
        row = await db.get(MailOutbox, str(event["id"]))
        if row is None:
            return False
        if row.status in ("sent", "dead") and status == "retrying":
            return True  # an old event arriving late
        row.status, row.attempts, row.last_error = status, int(event.get("attempts") or row.attempts), event.get("error")
        if status in ("sent", "dead"):
            row.done_at = _now()
        await db.commit()
    if status == "dead":
        logger.error("Mail to %s (%s) was given up: %s", row.to, row.purpose, row.last_error)
    return True


class MailEvents:
    def __init__(self):
        self._consumer = None
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        if settings.MAIL_BACKEND.lower() != "service" or AIOKafkaConsumer is None:
            return
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._consumer:
            await self._consumer.stop()
            self._consumer = None

    async def _run(self) -> None:
        while True:
            try:
                self._consumer = AIOKafkaConsumer(settings.MAIL_EVENTS_TOPIC, bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
                                                  group_id="core-mail-events", enable_auto_commit=False, auto_offset_reset="earliest")
                await self._consumer.start()
                async for message in self._consumer:
                    try:
                        await record(json.loads(message.value))
                    except Exception:  # noqa: BLE001  (one bad event must not stop the rest)
                        logger.exception("Couldn't record a mail event")
                    await self._consumer.commit()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("Mail events unavailable (%s); trying again shortly", exc)
                if self._consumer:
                    try:
                        await self._consumer.stop()
                    except Exception:  # noqa: BLE001
                        pass
                    self._consumer = None
                await asyncio.sleep(10)


# ---- mail read from mailboxes -------------------------------------------------------------------------

async def collect(item_id: str, purpose: str) -> bool:
    """Takes in one message the mail service read (support: added to its ticket, or a new one), then lets it go there.
    False when it couldn't be taken now (it stays waiting and is tried again)."""
    from app.services import mail_service
    from app.services.support import mailbox as support_mailbox
    try:
        raw = await mail_service.inbound_raw(item_id)
    except mail_service.MailServiceError as e:
        if e.status == 404:
            return True  # already taken in by another server process
        logger.warning("Couldn't collect inbound mail %s: %s", item_id, e)
        return False
    if purpose == "support":
        async with AsyncSessionLocal() as db:
            await support_mailbox.handle(db, raw)
    else:
        logger.warning("Inbound mail %s for '%s': nothing here reads that purpose; dropped", item_id, purpose)
    await mail_service.inbound_done(item_id)
    return True


async def collect_waiting(db=None, now=None) -> int:
    """The scheduler's sweep: anything read but not announced (or announced while no server listened)."""
    if settings.MAIL_BACKEND.lower() != "service":
        return 0
    from app.services import mail_service
    try:
        waiting = await mail_service.inbound()
    except mail_service.MailServiceError:
        return 0
    taken = 0
    for item in waiting:
        try:
            taken += int(await collect(item["id"], item["purpose"]))
        except Exception:  # noqa: BLE001
            logger.exception("Couldn't take in inbound mail %s", item.get("id"))
    return taken


class MailInbound(MailEvents):
    """Takes in each message as soon as the mail service announces it (mail.inbound)."""

    async def _run(self) -> None:
        while True:
            try:
                self._consumer = AIOKafkaConsumer(settings.MAIL_INBOUND_TOPIC, bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
                                                  group_id="core-mail-inbound", enable_auto_commit=False, auto_offset_reset="earliest")
                await self._consumer.start()
                async for message in self._consumer:
                    try:
                        event = json.loads(message.value)
                        await collect(event["id"], event.get("purpose", ""))
                    except Exception:  # noqa: BLE001  (the sweep tries it again later)
                        logger.exception("Couldn't take in announced inbound mail")
                    await self._consumer.commit()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("Inbound mail announcements unavailable (%s); trying again shortly", exc)
                if self._consumer:
                    try:
                        await self._consumer.stop()
                    except Exception:  # noqa: BLE001
                        pass
                    self._consumer = None
                await asyncio.sleep(10)


mail_relay = MailRelay()
mail_events = MailEvents()
mail_inbound = MailInbound()
