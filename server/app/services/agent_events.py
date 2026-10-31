"""
Agent events: how the core server wakes the workspace agent.

Callers add an event inside their own DB transaction with `enqueue_agent_event`;
`AgentEventRelay` publishes committed events to Kafka with a freshly minted,
template-scoped token. Events are keyed by template_id so one workspace's events
are delivered in order.

Event types (payload keys):
  template.samples_added  sample_document_ids, notes, channel_id
  knowledge.source_added  source_id, name, kind, reason (added|changed|remap), notes, channel_id
  document.submitted      document_id
  human.message           conversation_id, message_id, text, channel_id, page
  human.answered          question_id, question, answer
  review.completed        review_id, document_id, action, original_data, corrected_data, notes
  proposal.decided        proposal_id, proposal_type, decision, notes
  schedule.reflect        since, consolidated, activity
  schedule.insights       since, activity, records
  eval.item               eval_run_id, item_id, document_id, name, channel_id

A workspace whose agent budget is reached and set to pause has its events held here (they stay
unpublished, in order) until the budget allows work again (app.services.budget_service).
"""
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.agent_token import mint_agent_token
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.outbox import AgentEventOutbox

try:
    from aiokafka import AIOKafkaProducer
except ImportError:  # pragma: no cover
    AIOKafkaProducer = None

logger = logging.getLogger("core.agent_events")

EVENT_TYPES = {
    "template.samples_added",
    "knowledge.source_added",
    "document.submitted",
    "human.message",
    "human.answered",
    "review.completed",
    "proposal.decided",
    "schedule.reflect",
    "schedule.insights",
    "eval.item",
}


def enqueue_agent_event(
    db: AsyncSession,
    *,
    event_type: str,
    tenant_id: str,
    template_id: str,
    payload: Dict[str, Any],
    actor_id: str = "system",
    actor_type: str = "system",
    actor_name: Optional[str] = None,
    source: Optional[str] = None,
    conversation_id: Optional[str] = None,
) -> AgentEventOutbox:
    """
    Adds the event to the caller's transaction. It is published only after commit.

    The event's actor carries its origin, which the run log, the chat and memory all follow:
      origin  "user" (a person, or their API key, asked for it) | "system" (the platform did)
      source  "chat" | "dashboard" | "api" | "schedule" | "system"
    """
    if event_type not in EVENT_TYPES:
        raise ValueError(f"Unknown agent event type '{event_type}'")
    if actor_id.startswith("apikey:"):
        actor_type = "api_key"
    origin = "user" if actor_type in ("user", "api_key") else "system"
    if source is None:
        source = {"api_key": "api", "user": "dashboard"}.get(actor_type, "system")
    row = AgentEventOutbox(
        tenant_id=tenant_id,
        template_id=template_id,
        event_type=event_type,
        payload=payload,
        actor={"type": actor_type, "id": actor_id, "name": actor_name, "origin": origin, "source": source,
               "conversation_id": conversation_id},
        conversation_id=conversation_id or payload.get("conversation_id"),
    )
    db.add(row)
    return row


def build_envelope(row: AgentEventOutbox) -> Dict[str, Any]:
    return {
        "event_id": row.id,
        "type": row.event_type,
        "tenant_id": row.tenant_id,
        "template_id": row.template_id,
        "occurred_at": row.created_at.isoformat() if row.created_at else datetime.now(timezone.utc).isoformat(),
        "actor": row.actor,
        "payload": row.payload,
        "agent_token": mint_agent_token(row.tenant_id, row.template_id, row.id, actor_id=(row.actor or {}).get("id") or ""),
    }


class AgentEventRelay:
    """Publishes outbox rows to Kafka. Safe to run in several server processes (SKIP LOCKED)."""

    def __init__(self, poll_interval: float = 1.0, batch_size: int = 100):
        self.poll_interval = poll_interval
        self.batch_size = batch_size
        self._producer = None
        self._task: Optional[asyncio.Task] = None
        self._wakeup = asyncio.Event()

    async def start(self) -> None:
        if AIOKafkaProducer is None:
            logger.error("aiokafka is not installed; agent events will stay in the outbox.")
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
            producer = AIOKafkaProducer(bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS, acks="all")
            await producer.start()
            self._producer = producer
            logger.info("Agent event relay connected to Kafka at %s", settings.KAFKA_BOOTSTRAP_SERVERS)
            return True
        except Exception as exc:
            logger.warning("Kafka unavailable (%s); agent events are queued in the outbox.", exc)
            return False

    async def _run(self) -> None:
        while True:
            try:
                if await self._ensure_producer():
                    await self.publish_pending()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Agent event relay iteration failed")
            try:
                await asyncio.wait_for(self._wakeup.wait(), timeout=self.poll_interval)
            except asyncio.TimeoutError:
                pass
            self._wakeup.clear()

    async def publish_pending(self) -> int:
        from app.services import budget_service  # here, not at the top: budget alerts import this module's neighbours
        published = 0
        async with AsyncSessionLocal() as db:
            waiting = (await db.execute(
                select(AgentEventOutbox.template_id).where(AgentEventOutbox.published_at.is_(None)).distinct()
            )).scalars().all()
            held = [t for t in waiting if await budget_service.is_paused(db, t)]
            stmt = (
                select(AgentEventOutbox)
                .where(AgentEventOutbox.published_at.is_(None), AgentEventOutbox.template_id.not_in(held))
                .order_by(AgentEventOutbox.created_at)
                .limit(self.batch_size)
                .with_for_update(skip_locked=True)
            )
            rows = list((await db.execute(stmt)).scalars().all())
            for row in rows:
                try:
                    envelope = build_envelope(row)
                    await self._producer.send_and_wait(
                        settings.AGENT_EVENTS_TOPIC,
                        key=row.template_id.encode(),
                        value=json.dumps(envelope).encode(),
                    )
                    row.published_at = datetime.now(timezone.utc)
                    published += 1
                except Exception as exc:
                    row.attempts += 1
                    row.last_error = str(exc)[:2000]
                    logger.warning("Publishing agent event %s failed: %s", row.id, exc)
                    break  # keep per-workspace ordering; retry on next iteration
            await db.commit()
        if published:
            logger.info("Published %d agent event(s)", published)
        return published


agent_event_relay = AgentEventRelay()
