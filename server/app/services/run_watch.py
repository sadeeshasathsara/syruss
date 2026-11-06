"""
The platform's watch over agent runs, on every scheduler pass:

  lost     a run still RUNNING that has reported no step for RUN_LOST_AFTER_SECONDS (its worker died or was cut off) is
           marked LOST, written in its document's log and told to the platform's live view. If its event is delivered
           again, the next try is a run of its own.
  retain   step records (thoughts, tool inputs and results) older than RUN_STEPS_RETENTION_DAYS are dropped; the run's
           summary (when, why, outcome, cost) stays.

Several server processes may run this at once: each change is a single UPDATE on rows still in the old state.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from sqlalchemy import String, cast, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.models.trace import AgentExecutionTrace as Run
from app.services import extraction_log, platform_feed
from app.services.document_key import labels_for

logger = logging.getLogger("core.run_watch")

PRUNE_BATCH = 500


async def mark_lost(db: AsyncSession, now: Optional[datetime] = None) -> int:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=settings.RUN_LOST_AFTER_SECONDS)
    lost = (await db.execute(
        update(Run).where(Run.status == "RUNNING", func.coalesce(Run.last_step_at, Run.started_at) < cutoff)
        .values(status="LOST", completed_at=now, current_tool=None,
                total_duration_ms=func.extract("epoch", now - Run.started_at) * 1000,
                error_message=f"The run stopped reporting: no step for over {settings.RUN_LOST_AFTER_SECONDS // 60} minutes.")
        .returning(Run.id, Run.run_id, Run.tenant_id, Run.template_id, Run.document_id, Run.event_id, Run.profile,
                   Run.cost_usd, Run.total_duration_ms)
    )).mappings().all()
    lost = [dict(r) for r in lost]
    await db.commit()
    if not lost:
        return 0
    names = await labels_for(db, {r["document_id"] for r in lost if r["document_id"]})
    for r in lost:
        logger.warning("Run %s (%s) in workspace %s is lost", r["run_id"], r["profile"], r["template_id"])
        if r["document_id"]:
            await extraction_log.record(
                tenant_id=r["tenant_id"], template_id=r["template_id"], stage="failed", level="error", document_id=r["document_id"],
                document_name=names.get(r["document_id"]), event_id=r["event_id"],
                message="The agent stopped partway through reading this document. It is read again if its work is sent again.")
        await platform_feed.publish_fact(
            "run.finished", tenant_id=r["tenant_id"], template_id=r["template_id"], run_id=r["run_id"], trace_id=r["id"],
            trigger=r["profile"], status="LOST", document_id=r["document_id"], cost_usd=r["cost_usd"],
            duration_ms=float(r["total_duration_ms"]) if r["total_duration_ms"] is not None else None)
    return len(lost)


async def prune_steps(db: AsyncSession, now: Optional[datetime] = None) -> int:
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=settings.RUN_STEPS_RETENTION_DAYS)
    ids = (await db.execute(select(Run.id).where(Run.started_at < cutoff, cast(Run.steps, String) != "[]").limit(PRUNE_BATCH))).scalars().all()
    if ids:
        await db.execute(update(Run).where(Run.id.in_(ids)).values(steps=[]))
        await db.commit()
    return len(ids)
