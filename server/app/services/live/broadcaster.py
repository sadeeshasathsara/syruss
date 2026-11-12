import asyncio
import logging
from collections import OrderedDict
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Late joiners get a channel's last events; only the most recently used channels keep theirs, so a long-running
# server does not keep every document's and chat's history forever.
HISTORY_PER_CHANNEL = 100
HISTORY_CHANNELS = 2000


class EventBroadcaster:
    """
    In-memory pub/sub for live events (agent progress, chat replies, notifications). An event goes to the channel it
    was sent to and nowhere else: who may open a channel is decided in services/channels.py, so a copy sent anywhere
    wider would reach people that check never let in.
    """
    def __init__(self):
        self._subscribers: Dict[str, List[asyncio.Queue]] = {}
        self._history: "OrderedDict[str, List[Dict[str, Any]]]" = OrderedDict()

    def subscribe(self, channel_id: str, replay: bool = True) -> asyncio.Queue:
        queue = asyncio.Queue()
        self._subscribers.setdefault(channel_id, []).append(queue)
        if replay and channel_id in self._history:
            for event in self._history[channel_id]:
                queue.put_nowait(event)
        logger.debug("Subscribed to '%s'. Active subscribers: %s", channel_id, len(self._subscribers[channel_id]))
        return queue

    def unsubscribe(self, channel_id: str, queue: asyncio.Queue) -> None:
        if channel_id in self._subscribers:
            try:
                self._subscribers[channel_id].remove(queue)
                if not self._subscribers[channel_id]:
                    del self._subscribers[channel_id]
            except ValueError:
                pass

    def _remember(self, channel_id: str, payload: Dict[str, Any]) -> None:
        history = self._history.pop(channel_id, [])
        history.append(payload)
        self._history[channel_id] = history[-HISTORY_PER_CHANNEL:]
        while len(self._history) > HISTORY_CHANNELS:
            self._history.popitem(last=False)

    async def broadcast_to_channel(self, channel_id: str, payload: Dict[str, Any]) -> None:
        """Sends an event to the listeners of exactly this channel, and keeps it for those who join later."""
        self._remember(channel_id, payload)
        for q in list(self._subscribers.get(channel_id, [])):
            try:
                await q.put(payload)
            except Exception as ex:
                logger.warning("Error putting event to subscriber queue for %s: %s", channel_id, ex)

    async def broadcast(self, document_id: str, event_type: str, data: Dict[str, Any]) -> None:
        """Emits a structured event to the listeners of document_id and records it in the history buffer."""
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

    def clear_history(self, channel_id: Optional[str] = None) -> None:
        if channel_id:
            self._history.pop(channel_id, None)
        else:
            self._history.clear()


broadcaster = EventBroadcaster()
