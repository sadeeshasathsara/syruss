"""
The service's loops: jobs from mail.outbound are recorded and sent, a retry loop sends the ones due again, every
outcome is published on mail.events for the server, each reading job has a poller (inbound.py), the server's signed
commands are carried out and answered (commands.py, bus.py), the directory of public state is published (directory.py),
the server's API answers (api.py, until the server speaks Kafka only) and Keycloak's SMTP door listens (relay.py).
"""
import asyncio
import json
import logging
from typing import Any, Dict, Optional
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from pydantic import ValidationError
from mail.accounts.base import Outbound
from mail.config import check_config, settings
import uvicorn
from mail.api import build_api
from mail import bus
from mail.commands import Commands
from mail.delivery import Delivery
from mail.directory import Directory
from mail.inbound import Pollers, Reader, kafka_announcer
from mail.operations import Operations
from mail.relay import Relay
from mail.router import Router
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
        if await self.delivery.store.job(job.purpose) is None:
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


async def directory_forever(directory: Directory) -> None:
    """The directory again every DIRECTORY_REFRESH_SECONDS (the delivery times move on their own), and once at start."""
    while True:
        try:
            await directory.publish_all(settings.MAIL_DIRECTORY_TOPIC)
        except Exception:  # noqa: BLE001
            logger.exception("Couldn't publish the directory; trying again shortly")
        await asyncio.sleep(settings.DIRECTORY_REFRESH_SECONDS)


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    check_config(settings)
    store = Store()
    await store.init()
    router = Router(store)
    await bus.ensure_topics()
    producer = AIOKafkaProducer(bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS, max_request_size=settings.MAIL_MAX_MESSAGE_BYTES + 1_000_000)
    await producer.start()
    transport = bus.Bus(producer)
    directory = Directory(store, router, transport.send)
    commands = Commands(store, Operations(store, router), transport.reply, lambda: directory.publish_all(settings.MAIL_DIRECTORY_TOPIC))
    _, public = await store.box()
    from mail import sealing
    logger.info("Secrets are sealed to key %s: the admin app shows the same fingerprint (compare them once).", sealing.fingerprint(public))
    if not settings.MAIL_TRUSTED_SERVER_KEY:
        logger.warning("MAIL_TRUSTED_SERVER_KEY isn't set: commands from the server will be refused until it is.")
    consumer = AIOKafkaConsumer(settings.MAIL_OUTBOUND_TOPIC, bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
                                group_id=settings.CONSUMER_GROUP, enable_auto_commit=False, auto_offset_reset="earliest")
    await consumer.start()
    worker = MailWorker(Delivery(store, router), producer)
    logger.info("Mail service started (fallback account %s via %s), consuming '%s'", router.fallback.address,
                router.fallback.kind, settings.MAIL_OUTBOUND_TOPIC)
    retry = asyncio.create_task(worker.retry_forever())
    reading = asyncio.create_task(Pollers(Reader(store, router, kafka_announcer(producer))).forever())
    serving_commands = asyncio.create_task(bus.consume_commands(commands))
    publishing = asyncio.create_task(directory_forever(directory))
    api = uvicorn.Server(uvicorn.Config(build_api(store, router), host=settings.MAIL_API_HOST, port=settings.MAIL_API_PORT,
                                        log_level="warning", access_log=False))
    api.install_signal_handlers = lambda: None  # this loop owns the process
    serving = asyncio.create_task(api.serve())
    relay = Relay(worker.send)
    await relay.start()
    try:
        await worker.consume(consumer)
    finally:
        await relay.stop()
        api.should_exit = True
        for task in (retry, reading, serving, serving_commands, publishing):
            task.cancel()
        await consumer.stop()
        await producer.stop()
        await store.close()
