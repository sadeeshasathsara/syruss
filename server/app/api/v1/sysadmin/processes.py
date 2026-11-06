"""
A document's whole journey across the platform, from the moment it was sent in to the moment its record left: received,
queued, picked up, every run and try, records saved, rule checks, review, questions and answers, deliveries and their
attempts. Each step from the table that holds it, in one timeline, with how long each part took.

Facts for every staff member; names, records, messages and the file itself only with content open (support_access.py).
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.v1.sysadmin.access import Staff, require_staff
from app.api.v1.sysadmin.common import names, open_tenants, run_facts
from app.api.v1.sysadmin.runs import changes, event_dict
from app.api.v1.sysadmin.support_access import content_refused, open_content
from app.core.database import get_db_session
from app.models.audit import AuditLog
from app.models.delivery import Delivery, DeliveryAttempt, Destination
from app.models.document import Document, DocumentRole, DocumentStatus
from app.models.extraction import ExtractionResult
from app.models.extraction_log import ExtractionLogEntry
from app.models.intake import IntakeEvent
from app.models.outbox import AgentEventOutbox
from app.models.question import AgentQuestion
from app.models.review import ReviewTask
from app.models.trace import AgentExecutionTrace as Run
from app.services.document_key import labels_for
from app.services.storage_service import storage_service

router = APIRouter()

STUCK_QUEUED = timedelta(minutes=30)
STUCK_WAITING = timedelta(hours=24)
MAX_FILE_BYTES = 25 * 1024 * 1024


def _stuck_conditions(now: datetime):
    running = select(Run.document_id).where(Run.status == "RUNNING", Run.document_id.is_not(None))
    return or_(
        (Document.status == DocumentStatus.QUEUED) & (Document.updated_at < now - STUCK_QUEUED),
        (Document.status == DocumentStatus.PROCESSING) & Document.id.not_in(running) & (Document.updated_at < now - timedelta(minutes=5)),
        (Document.status == DocumentStatus.AWAITING_INPUT) & (Document.updated_at < now - STUCK_WAITING),
    )


def doc_facts(d: Document) -> Dict[str, Any]:
    return {"id": d.id, "tenant_id": d.tenant_id, "template_id": d.template_id, "status": d.status.value, "role": d.role.value,
            "is_test": d.is_test, "via": (d.source or {}).get("via") or ("upload" if not d.channel_id else "channel"),
            "content_type": d.content_type, "size_bytes": d.file_size_bytes, "created_at": d.created_at, "updated_at": d.updated_at}


@router.get("/processes")
async def list_processes(
    show: Literal["all", "running", "stuck", "failed", "waiting", "review", "done"] = "all",
    tenant_id: Optional[str] = None, workspace_id: Optional[str] = None, include_samples: bool = False,
    before: Optional[datetime] = None, limit: int = Query(50, ge=1, le=200),
    staff: Staff = Depends(require_staff()), db: AsyncSession = Depends(get_db_session),
):
    """Documents across the platform, newest first, with where each one is and its latest run."""
    now = datetime.now(timezone.utc)
    stmt = select(Document)
    if not include_samples:
        stmt = stmt.where(Document.role == DocumentRole.DOCUMENT)
    if tenant_id:
        stmt = stmt.where(Document.tenant_id == tenant_id)
    if workspace_id:
        stmt = stmt.where(Document.template_id == workspace_id)
    stmt = stmt.where({
        "all": true(), "stuck": _stuck_conditions(now),
        "running": Document.status.in_((DocumentStatus.QUEUED, DocumentStatus.PROCESSING)),
        "failed": Document.status == DocumentStatus.FAILED, "waiting": Document.status == DocumentStatus.AWAITING_INPUT,
        "review": Document.status == DocumentStatus.NEEDS_REVIEW, "done": Document.status == DocumentStatus.COMPLETED,
    }[show])
    if before:
        stmt = stmt.where(Document.created_at < before)
    docs = list((await db.execute(stmt.order_by(Document.created_at.desc()).limit(limit + 1))).scalars().all())
    page = docs[:limit]
    ids = [d.id for d in page]
    last: Dict[str, Run] = {}
    runs_per: Dict[str, int] = {}
    if ids:
        for r in (await db.execute(select(Run).where(Run.document_id.in_(ids)).order_by(Run.started_at))).scalars().all():
            last[r.document_id] = r
            runs_per[r.document_id] = runs_per.get(r.document_id, 0) + 1
    opened = await open_tenants(db, staff)
    labels = await labels_for(db, {d.id for d in page if d.tenant_id in opened})
    stuck = set((await db.execute(select(Document.id).where(Document.id.in_(ids), _stuck_conditions(now))))
                .scalars().all()) if ids else set()
    items = [{**doc_facts(d), "stuck": d.id in stuck, "runs": runs_per.get(d.id, 0),
              "last_run": run_facts(last[d.id]) if d.id in last else None, "content_open": d.tenant_id in opened,
              **({"name": labels.get(d.id) or d.original_filename} if d.tenant_id in opened else {})} for d in page]
    totals = dict((await db.execute(select(Document.status, func.count(Document.id)).where(
        Document.role == DocumentRole.DOCUMENT, *([Document.tenant_id == tenant_id] if tenant_id else []),
        *([Document.template_id == workspace_id] if workspace_id else [])).group_by(Document.status))).all())
    stuck_total = (await db.execute(select(func.count(Document.id)).where(
        Document.role == DocumentRole.DOCUMENT, _stuck_conditions(now), *([Document.tenant_id == tenant_id] if tenant_id else []),
        *([Document.template_id == workspace_id] if workspace_id else [])))).scalar()
    return {"items": items, "next_before": page[-1].created_at if len(docs) > limit else None,
            "totals": {**{s.value: n for s, n in totals.items()}, "stuck": stuck_total},
            **await names(db, {d.tenant_id for d in page}, {d.template_id for d in page})}


def _span(start: Optional[datetime], end: Optional[datetime]) -> Optional[float]:
    return (end - start).total_seconds() * 1000 if start and end and end >= start else None


@router.get("/processes/{document_id}")
async def process(document_id: str, staff: Staff = Depends(require_staff()), db: AsyncSession = Depends(get_db_session)):
    """One document's journey, step by step, from every table that holds a part of it."""
    d = await db.get(Document, document_id)
    if d is None:
        raise HTTPException(status_code=404, detail="No such document.")
    grant = await open_content(db, staff, d.tenant_id, "document", d.id)
    content = grant is not None
    now = datetime.now(timezone.utc)
    items: List[Dict[str, Any]] = []

    def add(at, stage: str, title: str, level: str = "info", **extra):
        if at:
            items.append({"at": at, "stage": stage, "title": title, "level": level, **{k: v for k, v in extra.items() if v is not None}})

    for e in (await db.execute(select(IntakeEvent).where(IntakeEvent.document_id == d.id))).scalars().all():
        add(e.created_at, "received", f"Sent in by {e.via}" + (f" from {e.sender}" if content and e.sender else ""),
            "warning" if e.outcome == "duplicate" else "info", detail=(e.ip or None) if content else None)
    add(d.created_at, "received", f"Stored ({d.content_type}, {round(d.file_size_bytes / 1024)} KB)" + (" as a test" if d.is_test else ""))

    events = list((await db.execute(select(AgentEventOutbox).where(
        AgentEventOutbox.payload["document_id"].as_string() == d.id).order_by(AgentEventOutbox.created_at))).scalars().all())
    for e in events:
        add(e.created_at, "queued", f"Event {e.event_type} queued", event_id=e.id)
        add(e.published_at, "queued", "Sent to the agent service", event_id=e.id)
        add(e.cancelled_at, "queued", f"Stopped by {e.cancelled_by or 'someone'}", "warning", event_id=e.id)
        if e.last_error and not e.published_at:
            add(e.created_at, "queued", f"Couldn't be sent ({e.attempts} tries)", "error", event_id=e.id, detail=e.last_error[:300])
    event_ids = [e.id for e in events]
    runs = list((await db.execute(select(Run).where(or_(Run.document_id == d.id, Run.event_id.in_(event_ids or [""])))
                                  .order_by(Run.started_at))).scalars().all())
    for r in runs:
        add(r.started_at, "run", f"Run started: {r.profile}" + (f" (try {r.attempt})" if r.attempt > 1 else ""), run=r.id)
        if r.status != "RUNNING":
            level = "error" if r.status in ("FAILED", "DEAD_LETTERED", "LOST") else "warning" if r.status in ("BUDGET_EXCEEDED", "CANCELLED") else "success"
            add(r.completed_at or r.started_at, "run", f"Run ended: {r.status.lower().replace('_', ' ')}", level, run=r.id,
                detail=f"{len(r.steps or [])} steps, {r.model_calls} model calls" + (f", ${r.cost_usd:.4f}" if r.cost_usd else ""))

    for e in (await db.execute(select(ExtractionLogEntry).where(ExtractionLogEntry.document_id == d.id))).scalars().all():
        add(e.created_at, e.stage, e.message if content else f"Log: {e.stage}", e.level,
            by=e.actor if e.actor in ("Agent", "System", "Syruss support") or content else "A person")

    review = (await db.execute(select(ReviewTask).where(ReviewTask.document_id == d.id))).scalar_one_or_none()
    if review:
        add(review.created_at, "review", "Sent to review", "warning", detail=review.reason if content else None)
        add(review.decided_at, "review", f"Review: {review.status.value.lower()}", "success", detail=review.reviewer_notes if content else None)
    questions = (await db.execute(select(AgentQuestion).where(AgentQuestion.related_document_id == d.id))).scalars().all()
    for q in questions:
        add(q.created_at, "question", "The agent asked a question" + (f": {q.question}" if content else ""), "warning", priority=q.priority)
        add(q.answered_at, "question", "Answered" + (f": {q.answer}" if content and q.answer else ""), "success")

    deliveries = list((await db.execute(select(Delivery, Destination.name, Destination.kind).join(
        Destination, Destination.id == Delivery.destination_id).where(Delivery.document_id == d.id))).all())
    attempts = (await db.execute(select(DeliveryAttempt).where(DeliveryAttempt.delivery_id.in_([x.id for x, _, _ in deliveries] or [""])))).scalars().all()
    for x, name, kind in deliveries:
        add(x.created_at, "delivery", f"Delivery {x.revision} to {name} ({kind}) {'held' if x.status == 'held' else 'queued'}",
            detail=x.hold_reason if content else None)
        add(x.delivered_at, "delivery", f"Delivered to {name}", "success")
        if x.status == "dead":
            add(x.updated_at, "delivery", f"Delivery to {name} gave up after {x.attempts} tries", "error", detail=(x.last_error or "")[:300] or None)
    for a in attempts:
        add(a.created_at, "delivery", f"Delivery attempt {a.attempt_no}: {'sent' if a.ok else 'failed'}" + (f" ({a.status_code})" if a.status_code else ""),
            "success" if a.ok else "error", duration_ms=a.duration_ms, detail=(a.error or "")[:300] or None if not a.ok else None)

    for a in (await db.execute(select(AuditLog).where(AuditLog.entity_id == d.id))).scalars().all():
        add(a.created_at, "change", a.event_type.replace("_", " ").capitalize(), by="Agent" if a.actor == "agent" else "System" if a.actor == "system" else "A person")
    items.sort(key=lambda i: i["at"])

    # How long each part took
    first_event = events[0].created_at if events else None
    first_run = runs[0].started_at if runs else None
    waits = [(review.created_at, review.decided_at or now)] if review else []
    waits += [(q.created_at, q.answered_at or now) for q in questions]
    delivered = [x.delivered_at for x, _, _ in deliveries if x.delivered_at]
    timings = {
        "total_ms": _span(d.created_at, max([i["at"] for i in items] or [d.created_at])),
        "queue_wait_ms": _span(first_event, first_run),
        "agent_ms": sum(r.total_duration_ms or 0 for r in runs) or None,
        "waiting_for_people_ms": sum(_span(a, b) or 0 for a, b in waits) or None,
        "delivery_ms": _span(min((x.created_at for x, _, _ in deliveries), default=None), max(delivered, default=None)),
        "cost_usd": sum(r.cost_usd or 0 for r in runs) or None,
    }
    record = None
    if content:
        result = (await db.execute(select(ExtractionResult).where(ExtractionResult.document_id == d.id))).scalar_one_or_none()
        if result:
            record = {"data": result.extracted_data, "confidence": result.overall_confidence, "validation": result.validation,
                      "notes": result.agent_notes, "revision": result.revision, "updated_at": result.updated_at}
    labels = await labels_for(db, {d.id}) if content else {}
    return {
        # Why it failed can carry a reviewer's words or the record's values: only with content open
        "document": {**doc_facts(d), "error": d.error_message if content else None, "has_error": bool(d.error_message),
                     **({"name": labels.get(d.id) or d.original_filename, "filename": d.original_filename,
                         "external_id": d.external_id} if content else {})},
        "content_open": content, "grant": {"id": grant.id, "expires_at": grant.expires_at, "reason": grant.reason} if grant else None,
        "timeline": items, "timings": timings,
        "events": [event_dict(e) for e in events], "runs": [run_facts(r) for r in runs],
        "changes": await changes(db, event_ids, content=content),
        "record": record,
        **await names(db, [d.tenant_id], [d.template_id]),
    }


@router.get("/processes/{document_id}/file")
async def process_file(document_id: str, staff: Staff = Depends(require_staff()), db: AsyncSession = Depends(get_db_session)):
    """The document's file itself, with content open."""
    d = await db.get(Document, document_id)
    if d is None:
        raise HTTPException(status_code=404, detail="No such document.")
    if await open_content(db, staff, d.tenant_id, "document", d.id) is None:
        raise content_refused()
    import asyncio
    data = await asyncio.to_thread(storage_service.read_object, d.minio_object_key, MAX_FILE_BYTES)
    return Response(content=data, media_type=d.content_type, headers={
        "Content-Disposition": "inline", "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; sandbox"})
