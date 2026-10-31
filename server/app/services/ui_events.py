"""
Live events for the frontend.

Server-originated UI events go through the same Kafka telemetry topic as the agent's
own telemetry, so every server process (and every WebSocket client) receives them.
Falls back to the local broadcaster when Kafka is unavailable.
"""
import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional
from app.core.config import settings
from app.services.agent_events import agent_event_relay
from app.services.event_broadcaster import broadcaster

logger = logging.getLogger("core.ui_events")


async def publish_ui_event(
    channel_id: str,
    event_type: str,
    data: Dict[str, Any],
    *,
    tenant_id: str,
    template_id: Optional[str] = None,
    private_to: Optional[str] = None,
) -> None:
    """`private_to`: the one person it is for (a private chat). It never goes to the shared workspace channel."""
    envelope = {
        **data,
        "channel_id": channel_id,
        "document_id": data.get("document_id", channel_id),
        "session_id": channel_id,
        "tenant_id": tenant_id,
        "template_id": template_id,
        "event_type": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "core",
        "data": data,
    }
    if private_to:
        envelope["private_to"] = private_to
    producer = agent_event_relay._producer
    if producer:
        try:
            await producer.send_and_wait(
                settings.AGENT_TELEMETRY_TOPIC,
                key=(template_id or channel_id).encode(),
                value=json.dumps(envelope, default=str).encode(),
            )
            return
        except Exception as exc:
            logger.warning("UI event via Kafka failed (%s); broadcasting locally.", exc)
    await broadcaster.broadcast_to_channel(channel_id, envelope)
    if template_id and not private_to:
        await broadcaster.broadcast_to_channel(f"template:{template_id}", envelope)


@asynccontextmanager
async def listen_channel(channel_id: str):
    """
    Subscribe to a channel before triggering the agent, then wait for its reply:

        async with listen_channel(conv_id) as wait:
            ...enqueue event and commit...
            reply = await wait(lambda e: e["event_type"] == "agent_message", timeout=90)

    `on_event` passed to `wait` sees every non-matching event (used to stream thoughts).
    """
    queue = broadcaster.subscribe(channel_id, replay=False)

    async def wait(
        predicate: Callable[[Dict[str, Any]], bool],
        timeout: float,
        on_event: Optional[Callable[[Dict[str, Any]], Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                return None
            try:
                event = await asyncio.wait_for(queue.get(), timeout=remaining)
            except asyncio.TimeoutError:
                return None
            if predicate(event):
                return event
            if on_event:
                result = on_event(event)
                if asyncio.iscoroutine(result):
                    await result

    try:
        yield wait
    finally:
        broadcaster.unsubscribe(channel_id, queue)
