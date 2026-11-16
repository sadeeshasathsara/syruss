"""
Kafka for the control plane: the topics this service needs, made at start-up with the settings each one has to have (the
broker would otherwise make them on first use with defaults: 1 MB messages, seven days, never compacted), and the loop
that carries out the server's commands.

    mail.outbound       messages to send (from the server)              default
    mail.events         what happened to each (to the server)           default
    mail.commands       signed commands (from the server)               kept one day
    mail.replies        the answers (to the server)                     kept one hour
    mail.directory      public state (to the server)                    COMPACTED: the latest value per key is kept
    mail.inbound        mail read from mailboxes (to the server)       messages up to MAIL_MAX_MESSAGE_BYTES
    mail.inbound.acks   "took it in" (from the server)                 kept one day
"""
import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from aiokafka.errors import TopicAlreadyExistsError

from mail.commands import Commands
from mail.config import settings
from mail.kafka_security import kafka_security

logger = logging.getLogger("mail.bus")
HOUR_MS = 3_600_000
DAY_MS = 24 * HOUR_MS


def topic_specs() -> List[NewTopic]:
    big = settings.MAIL_MAX_MESSAGE_BYTES + 1_000_000   # headers and framing on top of the email itself
    replicas = settings.MAIL_TOPIC_REPLICAS
    return [
        NewTopic(settings.MAIL_OUTBOUND_TOPIC, 3, replicas),
        NewTopic(settings.MAIL_EVENTS_TOPIC, 3, replicas),
        NewTopic(settings.MAIL_COMMANDS_TOPIC, 1, replicas, topic_configs={"retention.ms": str(DAY_MS)}),
        NewTopic(settings.MAIL_REPLIES_TOPIC, 1, replicas, topic_configs={"retention.ms": str(HOUR_MS)}),
        NewTopic(settings.MAIL_DIRECTORY_TOPIC, 1, replicas, topic_configs={"cleanup.policy": "compact", "min.cleanable.dirty.ratio": "0.1",
                                                                            "segment.ms": str(HOUR_MS)}),
        NewTopic(settings.MAIL_INBOUND_TOPIC, 3, replicas, topic_configs={"max.message.bytes": str(big), "retention.ms": str(2 * DAY_MS)}),
        NewTopic(settings.MAIL_INBOUND_ACKS_TOPIC, 1, replicas, topic_configs={"retention.ms": str(DAY_MS)}),
    ]


async def ensure_topics() -> None:
    """Creates what is missing. A topic that exists keeps its settings (the broker's own limits are in bridge/kafka)."""
    admin = AIOKafkaAdminClient(bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS, **kafka_security())
    await admin.start()
    try:
        for spec in topic_specs():
            try:
                await admin.create_topics([spec])
                logger.info("Created topic %s", spec.name)
            except TopicAlreadyExistsError:
                pass
    except Exception:  # noqa: BLE001  (the broker may make them itself; the service still runs)
        logger.exception("Couldn't make the mail topics")
    finally:
        await admin.close()


class Bus:
    """The service's producer, with the three things it publishes: replies, directory entries and (as before) events."""

    def __init__(self, producer: Optional[AIOKafkaProducer] = None):
        self.producer = producer

    async def send(self, topic: str, key: str, value: Optional[bytes]) -> None:
        if self.producer is None:
            return
        await self.producer.send_and_wait(topic, key=key.encode(), value=value)

    async def reply(self, reply: Dict[str, Any]) -> None:
        try:
            await self.send(settings.MAIL_REPLIES_TOPIC, reply["id"], json.dumps(reply).encode())
        except Exception:  # noqa: BLE001  (the command was carried out; the asker times out and looks at the directory)
            logger.exception("Couldn't publish the reply to %s", reply.get("id"))


    async def inbound(self, event: Dict[str, Any], raw: bytes) -> None:
        """One email on mail.inbound: its own bytes as the message, what it is in the headers (an email too big to carry: no body, too_large)."""
        headers = [("id", event["id"].encode()), ("job", event["purpose"].encode()), ("account", event["account_id"].encode()),
                   ("size", str(event["size"]).encode())]
        if event.get("too_large"):
            headers.append(("too_large", b"1"))
        try:
            await self.producer.send_and_wait(settings.MAIL_INBOUND_TOPIC, key=event["id"].encode(), value=raw, headers=headers)
        except Exception:  # noqa: BLE001  (it stays here; it is published again until the server acknowledges it)
            logger.exception("Couldn't publish inbound mail %s", event["id"])


async def consume_acks(reader) -> None:
    """The server says it took a message in (mail.inbound.acks): it is deleted here."""
    consumer = AIOKafkaConsumer(settings.MAIL_INBOUND_ACKS_TOPIC, bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS, **kafka_security(),
                                group_id="mail-acks", enable_auto_commit=False, auto_offset_reset="earliest")
    await consumer.start()
    try:
        async for message in consumer:
            try:
                await reader.acknowledged(json.loads(message.value)["id"])
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("Couldn't take an acknowledgement")
            await consumer.commit()
    finally:
        await consumer.stop()


async def consume_commands(commands: Commands) -> None:
    """Carries out each command on mail.commands, once (committed after it is handled). A new group reads from the start: a
    command too old is refused as stale and one seen before as a replay, so reading from the start can only be safe."""
    consumer = AIOKafkaConsumer(settings.MAIL_COMMANDS_TOPIC, bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS, **kafka_security(),
                                group_id="mail-commands", enable_auto_commit=False, auto_offset_reset="earliest")
    await consumer.start()
    try:
        async for message in consumer:
            try:
                await commands.handle(message.value)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("A command crashed the handler")
            await consumer.commit()
    finally:
        await consumer.stop()
