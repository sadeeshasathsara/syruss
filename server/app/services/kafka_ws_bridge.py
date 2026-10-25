import asyncio
import json
import logging
import uuid
from app.core.config import settings
from app.services.event_broadcaster import broadcaster

try:
    from aiokafka import AIOKafkaConsumer
except ImportError:  # pragma: no cover
    AIOKafkaConsumer = None

logger = logging.getLogger("core.kafka_ws_bridge")


class KafkaWebSocketBridge:
    """
    Relays agent telemetry from Kafka to connected WebSocket clients.
    Each server process uses its own consumer group, so every process sees every
    event and can serve the clients connected to it.
    """

    def __init__(self):
        self.consumer = None
        self._task = None

    async def start(self):
        if AIOKafkaConsumer is None:
            logger.error("aiokafka is not installed; live agent telemetry is disabled.")
            return
        try:
            self.consumer = AIOKafkaConsumer(
                settings.AGENT_TELEMETRY_TOPIC,
                bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
                group_id=f"core-ws-bridge-{uuid.uuid4().hex[:8]}",
                auto_offset_reset="latest",
            )
            await self.consumer.start()
            self._task = asyncio.create_task(self._consume_loop())
            logger.info("Kafka WebSocket bridge listening on '%s'", settings.AGENT_TELEMETRY_TOPIC)
        except Exception as exc:
            logger.warning("Could not start Kafka WebSocket bridge: %s", exc)
            self.consumer = None

    async def stop(self):
        if self._task:
            self._task.cancel()
        if self.consumer:
            await self.consumer.stop()
            self.consumer = None

    @staticmethod
    async def deliver(payload: dict) -> None:
        """
        Sends a telemetry event to its channel and to the workspace channel. A private chat's events
        (`private_to`) go only to that chat's own channel (which only its person may open), never to the workspace.
        """
        channel_id = payload.get("channel_id")
        if channel_id:
            await broadcaster.broadcast_to_channel(channel_id, payload)
        template_id = payload.get("template_id")
        private_to = payload.get("private_to")
        if not private_to and template_id and channel_id != f"template:{template_id}":
            await broadcaster.broadcast_to_channel(f"template:{template_id}", payload)

    async def _consume_loop(self):
        try:
            async for msg in self.consumer:
                try:
                    await self.deliver(json.loads(msg.value.decode("utf-8")))
                except Exception as exc:
                    logger.error("Bad telemetry message: %s", exc)
        except asyncio.CancelledError:
            pass


kafka_ws_bridge = KafkaWebSocketBridge()
