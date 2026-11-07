import asyncio
import logging
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class EventBroadcaster:
    """
    In-memory async pub/sub event broadcaster for streaming real-time agent
    cognition, tool calls, logs, and state transitions via WebSockets.
    """
    def __init__(self):
        # document_id -> list of asyncio.Queue
        self._subscribers: Dict[str, List[asyncio.Queue]] = {}
        # document_id -> list of past events (replay buffer for late joiners)
        self._history: Dict[str, List[Dict[str, Any]]] = {}

    def subscribe(self, document_id: str, replay: bool = True) -> asyncio.Queue:
        queue = asyncio.Queue()
        if document_id not in self._subscribers:
            self._subscribers[document_id] = []
        self._subscribers[document_id].append(queue)

        # Replay past events to this subscriber immediately
        if replay and document_id in self._history:
            for event in self._history[document_id]:
                queue.put_nowait(event)

        logger.debug(f"Subscribed queue to document '{document_id}'. Active subscribers: {len(self._subscribers[document_id])}")
        return queue

    def unsubscribe(self, document_id: str, queue: asyncio.Queue) -> None:
        if document_id in self._subscribers:
            try:
                self._subscribers[document_id].remove(queue)
                if not self._subscribers[document_id]:
                    del self._subscribers[document_id]
            except ValueError:
                pass

    async def broadcast_to_channel(
        self,
        channel_id: str,
        payload: Dict[str, Any]
    ) -> None:
        """
        Broadcasts pre-formatted telemetry payload to:
        1. Specific channel/document/session subscribers
        2. Global tenant subscribers (tenant:{tenant_id})
        """
        tenant_id = payload.get("tenant_id") or "tenant_default"
        tenant_channel = f"tenant:{tenant_id}"

        # Store in replay history for the channel
        if channel_id not in self._history:
            self._history[channel_id] = []
        self._history[channel_id].append(payload)
        if len(self._history[channel_id]) > 100:
            self._history[channel_id].pop(0)

        # Notify channel subscribers and tenant subscribers
        targets = set()
        if channel_id in self._subscribers:
            for q in list(self._subscribers[channel_id]):
                targets.add(q)

        if tenant_channel in self._subscribers:
            for q in list(self._subscribers[tenant_channel]):
                targets.add(q)

        for q in targets:
            try:
                await q.put(payload)
            except Exception as ex:
                logger.warning(f"Error putting event to subscriber queue for {channel_id}: {ex}")

    async def broadcast(
        self,
        document_id: str,
        event_type: str,
        data: Dict[str, Any]
    ) -> None:
        """
        Emits a structured event to all active WebSocket listeners on document_id
        and records it in the history buffer.
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        payload = {
            "document_id": document_id,
            "session_id": data.get("session_id") or document_id,
            "channel_id": document_id,
            "tenant_id": data.get("tenant_id", "tenant_default"),
            "event_type": event_type,
            "timestamp": timestamp,
            **data,
            "data": data
        }
        await self.broadcast_to_channel(document_id, payload)

    def clear_history(self, document_id: Optional[str] = None) -> None:
        if document_id:
            self._history.pop(document_id, None)
        else:
            self._history.clear()


broadcaster = EventBroadcaster()
