"""
Where work is waiting or has gone wrong, across the platform: events not yet sent to the agent service (and why), events
it has not picked up, how far its Kafka consumers are behind, runs under way and lost, failures of the last day, stuck
documents and deliveries that gave up. Facts only.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.v1.sysadmin.access import require_staff
from app.api.v1.sysadmin.common import names, run_facts
from app.api.v1.sysadmin.processes import _stuck_conditions, doc_facts
from app.api.v1.sysadmin.runs import FAILED, event_dict
from app.core.config import settings
from app.core.database import get_db_session
from app.models.delivery import Delivery
from app.models.document import Document, DocumentRole
from app.models.outbox import AgentEventOutbox as Event
from app.models.trace import AgentExecutionTrace as Run
from app.services import budget_service
from app.services.agent_events import agent_event_relay

try:
    from aiokafka import AIOKafkaConsumer, TopicPartition
    from aiokafka.admin import AIOKafkaAdminClient
except ImportError:  # pragma: no cover
    AIOKafkaAdminClient = None

logger = logging.getLogger("core.sysadmin.queues")

router = APIRouter()

NOT_PICKED_UP_AFTER = timedelta(minutes=5)
LIST = 25


async def consumer_lag() -> Dict[str, Any]:
    """How many agent events each partition holds that the agent service hasn't handled yet."""
    if AIOKafkaAdminClient is None:
        return {"status": "unknown", "detail": "aiokafka isn't installed"}
    admin = AIOKafkaAdminClient(bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS, request_timeout_ms=4000)
    consumer = AIOKafkaConsumer(bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS, request_timeout_ms=4000)
    try:
        await asyncio.wait_for(admin.start(), 5)
        await asyncio.wait_for(consumer.start(), 5)
        committed = await asyncio.wait_for(admin.list_consumer_group_offsets(settings.AGENT_CONSUMER_GROUP), 5)
        # The topic's partitions, from its description (a consumer only knows those of topics it subscribes to)
        parts = {tp.partition for tp in committed if tp.topic == settings.AGENT_EVENTS_TOPIC}
        try:
            described = await asyncio.wait_for(admin.describe_topics([settings.AGENT_EVENTS_TOPIC]), 5)
            for topic in described or []:
                parts |= {p["partition"] for p in topic.get("partitions", [])}
        except Exception:  # an older broker or client: the committed partitions will do
            pass
        tps = [TopicPartition(settings.AGENT_EVENTS_TOPIC, p) for p in sorted(parts)]
        ends = await asyncio.wait_for(consumer.end_offsets(tps), 5) if tps else {}
        partitions = []
        for tp in tps:
            done = committed.get(tp)
            at = done.offset if done and done.offset >= 0 else 0
            partitions.append({"partition": tp.partition, "end": ends.get(tp, 0), "committed": at, "lag": max(0, ends.get(tp, 0) - at)})
        return {"status": "ok", "group": settings.AGENT_CONSUMER_GROUP, "topic": settings.AGENT_EVENTS_TOPIC,
                "lag": sum(p["lag"] for p in partitions), "partitions": partitions}
    except Exception as exc:  # Kafka down or unreachable: say so, the rest of the page still answers
        return {"status": "down", "detail": str(exc)[:200] or type(exc).__name__}
    finally:
        for client in (consumer, admin):
            try:
                await client.close() if client is admin else await client.stop()
            except Exception:
                pass


@router.get("/queues", dependencies=[Depends(require_staff())])
async def queues(db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    unsent = (Event.published_at.is_(None), Event.cancelled_at.is_(None))
    waiting_templates = (await db.execute(select(Event.template_id).where(*unsent).distinct())).scalars().all()
    held = [t for t in waiting_templates if await budget_service.is_paused(db, t)]
    has_run = select(Run.id).where(Run.event_id == Event.id).exists()
    not_picked = (Event.published_at.is_not(None), Event.published_at < now - NOT_PICKED_UP_AFTER, Event.cancelled_at.is_(None),
                  Event.created_at > now - timedelta(days=1), ~has_run)
    day = Run.started_at >= now - timedelta(days=1)

    unsent_rows = list((await db.execute(select(Event).where(*unsent).order_by(Event.created_at).limit(LIST))).scalars().all())
    retrying = list((await db.execute(select(Event).where(*unsent, Event.attempts > 0).order_by(Event.created_at).limit(LIST))).scalars().all())
    not_picked_rows = list((await db.execute(select(Event).where(*not_picked).order_by(Event.published_at).limit(LIST))).scalars().all())
    running = list((await db.execute(select(Run).where(Run.status == "RUNNING").order_by(Run.started_at).limit(LIST))).scalars().all())
    failures = list((await db.execute(select(Run).where(day, Run.status.in_(FAILED)).order_by(Run.started_at.desc()).limit(LIST))).scalars().all())
    stuck = list((await db.execute(select(Document).where(Document.role == DocumentRole.DOCUMENT, _stuck_conditions(now))
                                   .order_by(Document.updated_at).limit(LIST))).scalars().all())
    count = lambda *where: db.scalar(select(func.count()).where(*where))  # noqa: E731
    tenants = {e.tenant_id for e in unsent_rows + retrying + not_picked_rows} | {r.tenant_id for r in running + failures} | {d.tenant_id for d in stuck}
    workspaces = {e.template_id for e in unsent_rows + retrying + not_picked_rows} | {r.template_id for r in running + failures} | {d.template_id for d in stuck}
    oldest = (await db.execute(select(func.min(Event.created_at)).where(*unsent))).scalar()
    return {
        "checked_at": now,
        "outbox": {
            "kafka_connected": bool(agent_event_relay._producer),
            "unsent": await count(Event.id.isnot(None), *unsent), "oldest_unsent_at": oldest,
            "held_by_budget": await count(*unsent, Event.template_id.in_(held or [""])), "held_workspaces": len(held),
            "retrying": await count(*unsent, Event.attempts > 0),
            "items": [event_dict(e) for e in unsent_rows], "retrying_items": [event_dict(e) for e in retrying],
        },
        "not_picked_up": {"count": await count(*not_picked), "items": [event_dict(e) for e in not_picked_rows]},
        "consumers": await consumer_lag(),
        "runs": {
            "running": await count(Run.status == "RUNNING"),
            "lost_today": await count(day, Run.status == "LOST"),
            "failed_today": await count(day, Run.status.in_(FAILED)),
            "running_items": [run_facts(r) for r in running], "failed_items": [run_facts(r) for r in failures],
        },
        "documents": {"stuck": await count(Document.role == DocumentRole.DOCUMENT, _stuck_conditions(now)),
                      "items": [doc_facts(d) for d in stuck]},
        "deliveries": {"dead": await count(Delivery.status == "dead"), "retrying": await count(Delivery.status == "retrying"),
                       "held": await count(Delivery.status == "held")},
        **await names(db, tenants, workspaces),
    }
