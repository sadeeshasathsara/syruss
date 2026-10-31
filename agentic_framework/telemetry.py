"""
Live telemetry: what the agent is thinking and doing, published to Kafka for the UI.
Large values are truncated; images never go over Kafka.
"""
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from agentic_framework.config import settings

logger = logging.getLogger("agent.telemetry")

MAX_FIELD_CHARS = 2000


def _shorten(value: Any) -> Any:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= MAX_FIELD_CHARS else text[:MAX_FIELD_CHARS] + "…"


class Telemetry:
    def __init__(self, producer, *, tenant_id: str, template_id: str, channel_id: str, run_id: str,
                 private_to: Optional[str] = None):
        self._producer = producer
        # A private chat's run: only that person may see what the agent thinks and does for it. The core
        # delivers such events to the chat's own channel and the person's, never to the shared workspace channel.
        self.private_to = private_to
        self.tenant_id = tenant_id
        self.template_id = template_id
        self.channel_id = channel_id
        self.run_id = run_id

    async def emit(self, event_type: str, data: Dict[str, Any], channel_id: Optional[str] = None) -> None:
        channel = channel_id or self.channel_id
        envelope = {
            **data,
            "event_type": event_type,
            "channel_id": channel,
            "document_id": data.get("document_id", channel),
            "session_id": channel,
            "tenant_id": self.tenant_id,
            "template_id": self.template_id,
            "run_id": self.run_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source": "agent",
            "data": data,
        }
        if self.private_to:
            envelope["private_to"] = self.private_to
        if self._producer is None:
            logger.debug("telemetry %s %s", event_type, data)
            return
        try:
            await self._producer.send_and_wait(
                settings.AGENT_TELEMETRY_TOPIC,
                key=self.template_id.encode(),
                value=json.dumps(envelope, default=str).encode(),
            )
        except Exception as exc:  # telemetry must never break the agent
            logger.warning("Telemetry publish failed: %s", exc)

    async def thought(self, step: int, text: str) -> None:
        await self.emit("agent_thought", {"step": step, "thought": _shorten(text)})

    async def tool_called(self, step: int, name: str, args: Dict[str, Any]) -> None:
        await self.emit("tool_called", {"step": step, "tool_name": name, "arguments": _shorten(args), "tool_args": _shorten(args)})

    async def tool_result(self, step: int, name: str, result: Any) -> None:
        await self.emit("tool_result", {"step": step, "tool_name": name, "result": _shorten(result)})
