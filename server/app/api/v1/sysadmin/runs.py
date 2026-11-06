"""
Every agent run on the platform, in every organization and workspace: what is running now, what ran, how it went and
what it cost, and for one run its event, its tries and what it changed.

Facts for every staff member. What a run thought, read and wrote (its steps' content, the records and messages it
produced) only with an access grant to its organization (support_access.py), and opening it is recorded.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Float, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.v1.sysadmin.access import Staff, require_staff
from app.api.v1.sysadmin.common import names, open_tenants, run_facts, step_facts
from app.api.v1.sysadmin.support_access import open_content
from app.core.database import get_db_session
from app.models.audit import AuditLog
from app.models.conversation import ChatMessage, Conversation
from app.models.extraction_log import ExtractionLogEntry
from app.models.outbox import AgentEventOutbox
from app.models.trace import AgentExecutionTrace as Run
from app.services.document_key import labels_for

router = APIRouter()

STATUSES = ("RUNNING", "COMPLETED", "FAILED", "BUDGET_EXCEEDED", "CANCELLED", "DEAD_LETTERED", "LOST")
FAILED = ("FAILED", "DEAD_LETTERED", "LOST")


def event_dict(e: Optional[AgentEventOutbox]) -> Optional[Dict[str, Any]]:
    """An agent event as facts: what woke the agent and how it went through the queue."""
    if e is None:
        return None
    return {"id": e.id, "type": e.event_type, "tenant_id": e.tenant_id, "template_id": e.template_id, "created_at": e.created_at, "published_at": e.published_at, "attempts": e.attempts,
            "last_error": e.last_error, "cancelled_at": e.cancelled_at, "cancelled_by": e.cancelled_by,
            "actor": {k: (e.actor or {}).get(k) for k in ("type", "origin", "source")},
            "document_id": (e.payload or {}).get("document_id"), "conversation_id": e.conversation_id}


def _filters(stmt, *, tenant_id, workspace_id, trigger, status, origin, source, model, document_id, conversation_id,
             since, before, min_cost, min_duration_ms):
    if tenant_id:
        stmt = stmt.where(Run.tenant_id == tenant_id)
    if workspace_id:
        stmt = stmt.where(Run.template_id == workspace_id)
    if trigger:
        stmt = stmt.where(Run.profile.in_(trigger.split(",")))
    if status:
        wanted = [s for s in status.upper().split(",") if s]
        stmt = stmt.where(Run.status.in_(FAILED if wanted == ["FAILURES"] else wanted))
    if origin:
        stmt = stmt.where(Run.origin_kind == origin)
    if source:
        stmt = stmt.where(Run.origin_source == source)
    if model:
        stmt = stmt.where(Run.model == model)
    if document_id:
        stmt = stmt.where(Run.document_id == document_id)
    if conversation_id:
        stmt = stmt.where(Run.conversation_id == conversation_id)
    if since:
        stmt = stmt.where(Run.started_at >= since)
    if before:
        stmt = stmt.where(Run.started_at < before)
    if min_cost is not None:
        stmt = stmt.where(Run.cost_usd >= min_cost)
    if min_duration_ms is not None:
        stmt = stmt.where(Run.total_duration_ms >= min_duration_ms)
    return stmt


async def _labelled(db: AsyncSession, staff: Staff, runs: List[Run]) -> Dict[str, Any]:
    """Runs as facts with their organizations and workspaces; document names only where the staff member has content open."""
    opened = await open_tenants(db, staff)
    labels = await labels_for(db, {r.document_id for r in runs if r.document_id and r.tenant_id in opened})
    items = []
    for r in runs:
        item = run_facts(r)
        item["content_open"] = r.tenant_id in opened
        if item["content_open"] and r.document_id:
            item["document_name"] = labels.get(r.document_id)
        items.append(item)
    return {"items": items, **await names(db, {r.tenant_id for r in runs}, {r.template_id for r in runs})}


@router.get("/runs", dependencies=[Depends(require_staff())])
async def list_runs(
    tenant_id: Optional[str] = None, workspace_id: Optional[str] = None,
    trigger: Optional[str] = Query(None, description="One or more event types, comma separated"),
    status: Optional[str] = Query(None, description="One or more statuses, comma separated, or FAILURES"),
    origin: Optional[str] = None, source: Optional[str] = None, model: Optional[str] = None,
    document_id: Optional[str] = None, conversation_id: Optional[str] = None,
    since: Optional[datetime] = None, before: Optional[datetime] = None,
    min_cost: Optional[float] = Query(None, ge=0), min_duration_ms: Optional[float] = Query(None, ge=0),
    limit: int = Query(50, ge=1, le=200),
    staff: Staff = Depends(require_staff()), db: AsyncSession = Depends(get_db_session),
):
    """Newest first; `before` (the last run's started_at) pages back. Totals cover every run the filters match."""
    kw = dict(tenant_id=tenant_id, workspace_id=workspace_id, trigger=trigger, status=status, origin=origin, source=source,
              model=model, document_id=document_id, conversation_id=conversation_id, since=since, before=None,
              min_cost=min_cost, min_duration_ms=min_duration_ms)
    page = list((await db.execute(_filters(select(Run), **{**kw, "before": before}).order_by(Run.started_at.desc()).limit(limit + 1))).scalars().all())
    totals = (await db.execute(_filters(select(
        func.count(Run.id), func.count(Run.id).filter(Run.status.in_(FAILED)), func.count(Run.id).filter(Run.status == "RUNNING"),
        func.coalesce(func.sum(Run.cost_usd), 0), func.coalesce(func.sum(Run.input_tokens + Run.output_tokens), 0),
    ), **kw))).one()
    out = await _labelled(db, staff, page[:limit])
    return {**out, "next_before": page[limit - 1].started_at if len(page) > limit else None,
            "totals": {"runs": totals[0], "failed": totals[1], "running": totals[2], "cost_usd": float(totals[3]), "tokens": int(totals[4])},
            "triggers": sorted((await db.execute(select(Run.profile).distinct())).scalars().all()),
            "models": sorted(m for m in (await db.execute(select(Run.model).distinct())).scalars().all() if m)}


@router.get("/runs/running", dependencies=[Depends(require_staff())])
async def running(staff: Staff = Depends(require_staff()), db: AsyncSession = Depends(get_db_session)):
    """Every run under way now, oldest first."""
    runs = list((await db.execute(select(Run).where(Run.status == "RUNNING").order_by(Run.started_at).limit(500))).scalars().all())
    return await _labelled(db, staff, runs)


@router.get("/runs/stats", dependencies=[Depends(require_staff())])
async def stats(hours: int = Query(24, ge=1, le=24 * 30), tenant_id: Optional[str] = None,
                db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    """Runs, failures, cost and duration per hour (per day beyond three days), and the busiest triggers, models and organizations."""
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    unit = "hour" if hours <= 72 else "day"
    where = [Run.started_at >= since, *([Run.tenant_id == tenant_id] if tenant_id else [])]
    bucket = func.date_trunc(unit, Run.started_at)
    duration = Run.total_duration_ms.cast(Float)
    rows = (await db.execute(select(
        bucket, func.count(Run.id), func.count(Run.id).filter(Run.status.in_(FAILED)), func.coalesce(func.sum(Run.cost_usd), 0),
        func.percentile_cont(0.5).within_group(duration), func.percentile_cont(0.95).within_group(duration),
    ).where(*where, Run.status != "RUNNING").group_by(bucket).order_by(bucket))).all()

    async def top(col, n=8):
        return [{"key": k, "runs": c, "failed": f, "cost_usd": float(cost or 0)} for k, c, f, cost in (await db.execute(
            select(col, func.count(Run.id), func.count(Run.id).filter(Run.status.in_(FAILED)), func.sum(Run.cost_usd))
            .where(*where).group_by(col).order_by(func.count(Run.id).desc()).limit(n))).all()]

    by_tenant = await top(Run.tenant_id)
    # Every hour (or day) of the period, quiet ones too, so the chart reads as time
    found = {at.astimezone(timezone.utc).replace(tzinfo=None): (n, f, c, p50, p95) for at, n, f, c, p50, p95 in rows}
    step = timedelta(hours=1) if unit == "hour" else timedelta(days=1)
    at = since.replace(minute=0, second=0, microsecond=0, tzinfo=None)
    if unit == "day":
        at = at.replace(hour=0)
    series = []
    while at <= datetime.now(timezone.utc).replace(tzinfo=None):
        n, f, c, p50, p95 = found.get(at, (0, 0, 0, None, None))
        series.append({"at": at.replace(tzinfo=timezone.utc), "runs": n, "failed": f, "cost_usd": float(c), "p50_ms": p50, "p95_ms": p95})
        at += step
    return {
        "since": since, "unit": unit, "series": series,
        "by_trigger": await top(Run.profile), "by_model": await top(Run.model), "by_status": await top(Run.status, 10),
        "by_organization": by_tenant, **await names(db, [t["key"] for t in by_tenant]),
    }


async def changes(db: AsyncSession, event_ids: List[str], *, content: bool) -> List[Dict[str, Any]]:
    """What runs of these events changed, from the entries they left (log, audit, messages, conversations they opened)."""
    if not event_ids:
        return []
    out: List[Dict[str, Any]] = []
    for e in (await db.execute(select(ExtractionLogEntry).where(ExtractionLogEntry.event_id.in_(event_ids)))).scalars().all():
        out.append({"at": e.created_at, "kind": "log", "what": e.stage, "level": e.level, "document_id": e.document_id,
                    **({"text": e.message} if content else {})})
    for a in (await db.execute(select(AuditLog).where(AuditLog.event_id.in_(event_ids)))).scalars().all():
        out.append({"at": a.created_at, "kind": "change", "what": a.event_type, "target": f"{a.entity_type} {a.entity_id}",
                    **({"details": a.details} if content else {})})
    messages = (await db.execute(select(ChatMessage).where(ChatMessage.agent_trace["event_id"].as_string().in_(event_ids)))).scalars().all()
    for m in messages:
        out.append({"at": m.created_at, "kind": "message", "what": m.role, "conversation_id": m.conversation_id,
                    **({"text": m.content} if content else {})})
    opened = (await db.execute(select(Conversation).where(
        Conversation.context_metadata["opened_by_event"].as_string().in_(event_ids)))).scalars().all()
    for c in opened:
        out.append({"at": c.created_at, "kind": "conversation", "what": c.kind, "conversation_id": c.id,
                    **({"text": c.title} if content else {})})
    return sorted(out, key=lambda x: x["at"])


@router.get("/runs/{trace_id}")
async def get_run(trace_id: str, staff: Staff = Depends(require_staff()), db: AsyncSession = Depends(get_db_session)):
    """One run: its facts, its event and every try of it, what it changed, and with content open its steps in full."""
    r = await db.get(Run, trace_id)
    if r is None:
        r = (await db.execute(select(Run).where(Run.run_id == trace_id).limit(1))).scalar_one_or_none()
    if r is None:
        raise HTTPException(status_code=404, detail="No such run.")
    grant = await open_content(db, staff, r.tenant_id, "run", r.id)
    event = await db.get(AgentEventOutbox, r.event_id) if r.event_id else None
    tries = list((await db.execute(select(Run).where(Run.event_id == r.event_id).order_by(Run.started_at))).scalars().all()) if r.event_id else [r]
    labels = await labels_for(db, {r.document_id} if r.document_id and grant else set())
    steps = r.steps or []
    return {
        "run": {**run_facts(r), "content_open": grant is not None, "document_name": labels.get(r.document_id or "")},
        "event": event_dict(event),
        "tries": [run_facts(t) for t in tries],
        "changes": await changes(db, [r.event_id] if r.event_id else [], content=grant is not None),
        "steps": steps if grant else [step_facts(s) for s in steps],
        "final_thought": r.final_thought if grant else None,
        # The error is the platform's own (an exception, a timeout): shown to all staff, shortened without content open
        "error": r.error_message if grant or not r.error_message else r.error_message[:300],
        "usage": r.usage,
        "grant": {"id": grant.id, "expires_at": grant.expires_at, "reason": grant.reason} if grant else None,
        **await names(db, [r.tenant_id], [r.template_id]),
    }
