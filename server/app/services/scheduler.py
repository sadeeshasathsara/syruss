"""
The platform's own recurring work for each workspace. Each server process runs one loop; a job's
row is claimed with SKIP LOCKED, so several processes never run the same job at once.

  graph    builds a workspace's graph once: sightings for records that predate it, then the AGE
           mirror where AGE is installed (again whenever AGE appears or the mirror fell behind)
  insights on the workspace's insights schedule (Settings > Agent, off by default): wakes the agent to look through the saved
           records and suggest analytics screens, when there are enough records and something new since the last look
  reflect  on the workspace's reflection schedule (Settings > Agent > Reflection): consolidates
           memory, then, if anything happened since the last reflection, wakes the agent to look
           back over it (a schedule.reflect event). Quiet workspaces cost nothing.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.audit import AuditLog
from app.models.document import Document, DocumentRole, DocumentStatus
from app.models.jobs import WorkspaceJob
from app.models.screen import WorkspaceScreen
from app.models.outbox import AgentEventOutbox
from app.models.template import DocumentTemplate
from app.services import graph_service, memory_service, settings_service, workspace_events
from app.services.agent_events import agent_event_relay, enqueue_agent_event

logger = logging.getLogger("core.scheduler")

PERIODS = {"daily": timedelta(days=1), "weekly": timedelta(days=7)}
# What counts as "something happened" for a reflection: work and people's feedback, not the
# platform's own reflections or evaluations.
ACTIVITY_EVENTS = ("document.submitted", "review.completed", "human.answered", "proposal.decided", "human.message",
                   "knowledge.source_added", "template.samples_added")


async def _claim(db: AsyncSession, template: DocumentTemplate, job: str) -> Optional[WorkspaceJob]:
    """The job's row, locked for this transaction; None while another process holds it."""
    await db.execute(insert(WorkspaceJob).values(
        template_id=template.id, job=job, tenant_id=template.tenant_id, state={}, updated_at=datetime.now(timezone.utc),
    ).on_conflict_do_nothing())
    return (await db.execute(select(WorkspaceJob).where(
        WorkspaceJob.template_id == template.id, WorkspaceJob.job == job,
    ).with_for_update(skip_locked=True))).scalar_one_or_none()


# ---------------------------------------------------------------------------
# graph
# ---------------------------------------------------------------------------

async def sync_graph(db: AsyncSession, template: DocumentTemplate) -> Optional[Dict[str, Any]]:
    job = await _claim(db, template, "graph")
    if job is None:
        return None
    age = await graph_service.has_age(db)
    state = dict(job.state or {})
    if state.get("built") and (state.get("age_synced") or not age):
        return state
    if not state.get("built"):
        state["pairs"] = await graph_service.rebuild_sightings(
            db, tenant_id=template.tenant_id, template_id=template.id, schema=template.schema_definition,
        )
        state["built"] = True
    state["age_synced"] = False
    if age:
        try:
            async with db.begin_nested():
                state["edges"] = await graph_service.rebuild_graph(db, template.id)
            state["age_synced"] = True
        except Exception as exc:
            logger.warning("Building the AGE graph of workspace %s failed; graph questions use SQL: %s", template.id, exc)
            state["age_error"] = str(exc)[:500]
    job.state = state
    job.last_run_at = datetime.now(timezone.utc)
    job.last_status = "age" if state["age_synced"] else "sql"
    return state


# ---------------------------------------------------------------------------
# reflect
# ---------------------------------------------------------------------------

async def _activity(db: AsyncSession, template_id: str, since: datetime) -> Dict[str, int]:
    rows = await db.execute(
        select(AgentEventOutbox.event_type, func.count()).where(
            AgentEventOutbox.template_id == template_id, AgentEventOutbox.created_at >= since,
            AgentEventOutbox.event_type.in_(ACTIVITY_EVENTS),
        ).group_by(AgentEventOutbox.event_type)
    )
    return {t: n for t, n in rows.all()}


async def reflect(db: AsyncSession, template: DocumentTemplate, *, now: Optional[datetime] = None,
                  requested_by: Optional[Dict[str, str]] = None) -> Optional[Dict[str, Any]]:
    """
    Runs the reflection job if it is due (or now, when a person asks: `requested_by` {id, name}).
    Returns what it did, or None when it wasn't due or another process holds it.
    """
    now = now or datetime.now(timezone.utc)
    job = await _claim(db, template, "reflect")
    if job is None:
        return None
    cadence = (await settings_service.get_settings(db, template.id)).agent.reflection
    period = PERIODS.get(cadence)
    if requested_by is None:
        if period is None:
            job.next_run_at = None
            return None
        if job.next_run_at and now < job.next_run_at:
            return None

    since = job.last_run_at or now - (period or PERIODS["weekly"])
    consolidated = await memory_service.consolidate(db, template.id, now)
    activity = await _activity(db, template.id, since)
    woke = bool(requested_by) or bool(activity)
    if woke:
        enqueue_agent_event(
            db, event_type="schedule.reflect", tenant_id=template.tenant_id, template_id=template.id,
            payload={"since": since.isoformat(), "consolidated": consolidated, "activity": activity,
                     "channel_id": f"template:{template.id}"},
            **({"actor_id": requested_by["id"], "actor_type": "user", "actor_name": requested_by.get("name"), "source": "dashboard"}
               if requested_by else {"source": "schedule"}),
        )
    if consolidated["retired"] or consolidated["merged"]:
        db.add(AuditLog(
            tenant_id=template.tenant_id, template_id=template.id, event_type="MEMORY_CONSOLIDATED",
            entity_type="Workspace", entity_id=template.id, actor="system",
            details={k: consolidated[k] for k in ("retired", "merged", "merged_items")},
        ))
    await workspace_events.emit(
        db, tenant_id=template.tenant_id, template_id=template.id, type="agent.reflection",
        title="Syruss reflected on recent work" if woke else "Nothing new to reflect on",
        body=f"Retired {consolidated['retired']} and merged {consolidated['merged']} memories.",
        path="knowledge?tab=memory", data={"activity": activity, "retired": consolidated["retired"], "merged": consolidated["merged"]},
    )
    job.last_run_at = now
    job.next_run_at = now + period if period else None
    job.last_status = "woke_agent" if woke else "nothing_new"
    job.state = {"since": since.isoformat(), "activity": activity, "retired": consolidated["retired"],
                 "merged": consolidated["merged"], "requested_by": (requested_by or {}).get("name")}
    return {"status": job.last_status, "since": since, "activity": activity, **consolidated}


# ---------------------------------------------------------------------------
# insights
# ---------------------------------------------------------------------------

MIN_RECORDS_FOR_INSIGHTS = 3
MAX_WAITING_SUGGESTIONS = 5


async def insights(db: AsyncSession, template: DocumentTemplate, *, now: Optional[datetime] = None,
                   requested_by: Optional[Dict[str, str]] = None) -> Optional[Dict[str, Any]]:
    """
    Wakes the agent to look through the saved records and suggest analytics screens, when it is due (or when a person asks).
    It stays quiet when there are too few records to see a pattern, when suggestions are still waiting, and (on a schedule)
    when nothing new was read since the last look. Returns what it did, or None when it wasn't due or is held elsewhere.
    """
    now = now or datetime.now(timezone.utc)
    job = await _claim(db, template, "insights")
    if job is None:
        return None
    cadence = (await settings_service.get_settings(db, template.id)).agent.insights
    period = PERIODS["weekly"] if cadence == "weekly" else None
    if requested_by is None:
        if period is None:
            job.next_run_at = None
            return None
        if job.next_run_at and now < job.next_run_at:
            return None
    since = job.last_run_at or now - PERIODS["weekly"]
    activity = await _activity(db, template.id, since)
    saved = (await db.execute(select(func.count()).select_from(Document).where(
        Document.template_id == template.id, Document.role == DocumentRole.DOCUMENT, Document.status == DocumentStatus.COMPLETED))).scalar_one()
    waiting = (await db.execute(select(func.count()).select_from(WorkspaceScreen).where(
        WorkspaceScreen.template_id == template.id, WorkspaceScreen.status == "suggested"))).scalar_one()
    if saved < MIN_RECORDS_FOR_INSIGHTS:
        status = "too_few_records"
    elif waiting >= MAX_WAITING_SUGGESTIONS:
        status = "suggestions_waiting"
    elif requested_by is None and not activity.get("document.submitted"):
        status = "nothing_new"
    else:
        status = "woke_agent"
        enqueue_agent_event(
            db, event_type="schedule.insights", tenant_id=template.tenant_id, template_id=template.id,
            payload={"since": since.isoformat(), "activity": activity, "records": saved, "channel_id": f"template:{template.id}"},
            **({"actor_id": requested_by["id"], "actor_type": "user", "actor_name": requested_by.get("name"), "source": "dashboard"}
               if requested_by else {"source": "schedule"}),
        )
    job.last_run_at = now
    job.next_run_at = now + period if period else None
    job.last_status = status
    job.state = {"since": since.isoformat(), "activity": activity, "records": saved, "waiting": waiting, "requested_by": (requested_by or {}).get("name")}
    return {"status": status, "records": saved, "waiting": waiting}


def job_dict(job: Optional[WorkspaceJob]) -> Optional[Dict[str, Any]]:
    if job is None:
        return None
    return {"job": job.job, "last_run_at": job.last_run_at, "next_run_at": job.next_run_at,
            "last_status": job.last_status, "state": job.state}


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------

async def tick(now: Optional[datetime] = None) -> None:
    """One pass over every published workspace; each workspace's jobs commit on their own."""
    async with AsyncSessionLocal() as db:
        ids = (await db.execute(select(DocumentTemplate.id).where(DocumentTemplate.active_version_id.is_not(None)))).scalars().all()
    for template_id in ids:
        for job in (sync_graph, reflect, insights):
            async with AsyncSessionLocal() as db:
                try:
                    template = await db.get(DocumentTemplate, template_id)
                    if template is None:
                        break
                    result = await (job(db, template) if job is sync_graph else job(db, template, now=now))
                    await db.commit()
                    if job in (reflect, insights) and result and result["status"] == "woke_agent":
                        agent_event_relay.notify()
                    await workspace_events.dispatch(db)
                except Exception:
                    await db.rollback()
                    logger.exception("Scheduled job %s failed for workspace %s", job.__name__, template_id)


class Scheduler:
    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        if settings.SCHEDULER_ENABLED:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _run(self) -> None:
        while True:
            try:
                await tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Scheduler pass failed")
            await asyncio.sleep(settings.SCHEDULER_INTERVAL_SECONDS)


scheduler = Scheduler()
