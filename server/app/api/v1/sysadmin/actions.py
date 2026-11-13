"""
What platform staff can do about a run or a document that has gone wrong: stop a run, read a document again, send an
event again. Each needs operations.manage and a reason, and goes in the platform audit log and in the organization's
own activity.
"""
from datetime import datetime, timezone
from typing import Any, Dict
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.v1.sysadmin.access import Staff, record, require_staff
from app.api.v1.sysadmin.common import tell_organization
from app.core.database import get_db_session
from app.models.document import Document, DocumentStatus
from app.models.outbox import AgentEventOutbox
from app.models.template import DocumentTemplate
from app.models.trace import AgentExecutionTrace as Run
from app.services import extraction_log
from app.services.live import platform_feed
from app.services.agent_events import agent_event_relay, enqueue_agent_event
from app.services.document_key import document_label

router = APIRouter()


class Reason(BaseModel):
    reason: str = Field(..., min_length=5, max_length=1000)


@router.post("/runs/{trace_id}/stop")
async def stop_run(trace_id: str, body: Reason, staff: Staff = Depends(require_staff("operations.manage")),
                   db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    """Stops a run under way at its next check (between steps, and while the model thinks during a chat reply)."""
    r = await db.get(Run, trace_id)
    if r is None:
        raise HTTPException(status_code=404, detail="No such run.")
    if r.status != "RUNNING" or not r.event_id:
        raise HTTPException(status_code=409, detail="This run isn't running.")
    event = await db.get(AgentEventOutbox, r.event_id)
    if event is None:
        raise HTTPException(status_code=409, detail="This run's event is gone, so it can't be told to stop.")
    if event.cancelled_at is None:
        event.cancelled_at, event.cancelled_by = datetime.now(timezone.utc), f"platform:{staff.user_id}"
    record(db, staff, "run.stopped", "run", r.id, tenant_id=r.tenant_id, reason=body.reason, details={"trigger": r.profile, "event_id": r.event_id})
    tell_organization(db, staff, r.tenant_id, f"stopped a run of the agent ({r.profile})", body.reason, run_id=r.id)
    await db.commit()
    await platform_feed.publish_fact("run.stop_asked", tenant_id=r.tenant_id, template_id=r.template_id, run_id=r.run_id, trace_id=r.id)
    return {"stopping": True}


@router.post("/processes/{document_id}/retry")
async def retry_document(document_id: str, body: Reason, staff: Staff = Depends(require_staff("operations.manage")),
                         db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    """Sends a failed or stuck document to the agent again, as a new event."""
    d = await db.get(Document, document_id)
    if d is None:
        raise HTTPException(status_code=404, detail="No such document.")
    running = (await db.execute(select(Run.id).where(Run.document_id == d.id, Run.status == "RUNNING").limit(1))).first()
    if running:
        raise HTTPException(status_code=409, detail="The agent is reading this document right now. Stop that run first.")
    if d.status not in (DocumentStatus.FAILED, DocumentStatus.QUEUED, DocumentStatus.PROCESSING, DocumentStatus.PENDING):
        raise HTTPException(status_code=409, detail=f"Only a failed or stuck document is read again; this one is {d.status.value.lower()}.")
    template = await db.get(DocumentTemplate, d.template_id)
    if template is None or not template.active_version_id:
        raise HTTPException(status_code=409, detail="Its workspace has no published template, so the agent can't read it.")
    previous = d.status.value
    d.status, d.error_message = DocumentStatus.QUEUED, None
    event = enqueue_agent_event(db, event_type="document.submitted", tenant_id=d.tenant_id, template_id=d.template_id,
                                payload={"document_id": d.id}, actor_id=f"platform:{staff.user_id}", actor_type="system",
                                actor_name="Syruss support", source="system")
    await db.flush()
    record(db, staff, "document.retried", "document", d.id, tenant_id=d.tenant_id, reason=body.reason,
           details={"from": previous, "event_id": event.id})
    tell_organization(db, staff, d.tenant_id, "sent a document to the agent to be read again", body.reason, document_id=d.id)
    name = document_label(d)
    await db.commit()
    agent_event_relay.notify()
    await extraction_log.record(tenant_id=d.tenant_id, template_id=d.template_id, stage="queued", document_id=d.id, document_name=name,
                                actor="Syruss support", message="Sent to the agent to be read again by Syruss support.")
    return {"status": d.status.value, "event_id": event.id}


@router.post("/events/{event_id}/resend")
async def resend_event(event_id: str, body: Reason, staff: Staff = Depends(require_staff("operations.manage")),
                       db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    """
    Sends an event to the agent service again: one that kept failing to send, or one sent that no run ever picked up.
    The agent service skips an event it has already handled, so this never makes the same work twice.
    """
    e = await db.get(AgentEventOutbox, event_id)
    if e is None:
        raise HTTPException(status_code=404, detail="No such event.")
    if e.cancelled_at:
        raise HTTPException(status_code=409, detail="This event was stopped; it isn't sent again.")
    ran = (await db.execute(select(Run.id).where(Run.event_id == e.id).limit(1))).first()
    if ran:
        raise HTTPException(status_code=409, detail="The agent already picked this event up. Look at its runs instead.")
    was = {"published_at": e.published_at, "attempts": e.attempts, "last_error": e.last_error}
    e.published_at, e.attempts, e.last_error = None, 0, None
    record(db, staff, "event.resent", "event", e.id, tenant_id=e.tenant_id, reason=body.reason,
           details={"type": e.event_type, "was_sent": bool(was["published_at"]), "attempts": was["attempts"]})
    tell_organization(db, staff, e.tenant_id, f"sent a piece of waiting work ({e.event_type}) to the agent again", body.reason, event_id=e.id)
    await db.commit()
    agent_event_relay.notify()
    return {"resent": True}
