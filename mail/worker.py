"""
The service's loops: jobs from mail.outbound are recorded and sent, a retry loop sends the ones due again, and every
outcome is published on mail.events for the server.
"""
import asyncio
import json
import logging
from typing import Any, Dict, Optional
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from pydantic import ValidationError
from mail.accounts.base import Outbound
from mail.config import check_config, settings
from mail.delivery import Delivery
from mail.relay import Relay
from mail.router import PURPOSES, Router
from mail.store import Store

logger = logging.getLogger("mail.worker")


class MailWorker:
    def __init__(self, delivery: Delivery, producer: Optional[AIOKafkaProducer] = None):
        self.delivery, self.producer = delivery, producer

    async def publish(self, event: Optional[Dict[str, Any]]) -> None:
        if event is None or self.producer is None:
            return
        try:
            await self.producer.send_and_wait(settings.MAIL_EVENTS_TOPIC, key=event["id"].encode(), value=json.dumps(event).encode())
        except Exception:  # noqa: BLE001  (the delivery record stands; the server's view can lag)
            logger.exception("Couldn't publish mail event for %s", event.get("id"))

    async def take(self, raw: bytes) -> None:
        """One message from mail.outbound: recorded, then sent. A malformed one is logged and skipped (never retried)."""
        try:
            job = Outbound.model_validate_json(raw)
        except ValidationError as e:
            logger.error("Skipped a malformed mail job: %s", e.errors()[:3])
            return
        if job.purpose not in PURPOSES:
            logger.error("Skipped mail job %s: unknown purpose '%s'", job.id, job.purpose)
            await self.publish({"id": job.id, "purpose": job.purpose, "status": "dead", "attempts": 0,
                                "error": f"Unknown purpose '{job.purpose}'", "meta": job.meta})
            return
        await self.send(job)

    async def send(self, job: Outbound) -> None:
        """A job from any door (Kafka, or the relay for Keycloak): recorded once, then sent."""
        if await self.delivery.accept(job):
            await self.publish(await self.delivery.attempt(job.id))

    async def consume(self, consumer: AIOKafkaConsumer) -> None:
        async for message in consumer:
            await self.take(message.value)
            await consumer.commit()  # after it is recorded: a crash before this only brings it back once more

    async def retry_forever(self) -> None:
        while True:
            try:
                for job_id in await self.delivery.due():
                    await self.publish(await self.delivery.attempt(job_id))
            except Exception:  # noqa: BLE001
                logger.exception("The mail retry loop failed; trying again shortly")
            await asyncio.sleep(settings.RETRY_POLL_SECONDS)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    check_config(settings)
    store = Store()
    await store.init()
    router = Router(store)
    producer = AIOKafkaProducer(bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS)
    await producer.start()
    consumer = AIOKafkaConsumer(settings.MAIL_OUTBOUND_TOPIC, bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
                                group_id=settings.CONSUMER_GROUP, enable_auto_commit=False, auto_offset_reset="earliest")
    await consumer.start()
    worker = MailWorker(Delivery(store, router), producer)
    logger.info("Mail service started (fallback account %s via %s), consuming '%s'", router.fallback.address,
                router.fallback.kind, settings.MAIL_OUTBOUND_TOPIC)
    retry = asyncio.create_task(worker.retry_forever())
    relay = Relay(worker.send)
    await relay.start()
    try:
        await worker.consume(consumer)
    finally:
        await relay.stop()
        retry.cancel()
        await consumer.stop()
        await producer.stop()
        await store.close()
