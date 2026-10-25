"""
Deliveries: every approved record on its way out, where it is, and what happened on each try. People with `integrations.manage` can look
into one that failed, and send it again once the problem is fixed.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db_session
from app.core.permissions import Access, require
from app.models.audit import AuditLog
from app.models.delivery import Delivery, DeliveryAttempt, Destination
from app.models.document import Document, DocumentStatus
from app.services import delivery_service
from app.services.destinations import ADAPTERS
from app.services.ui_events import publish_ui_event

router = APIRouter(prefix="/deliveries", tags=["Connections: publish"])

STATUSES = ("held", "pending", "sending", "retrying", "delivered", "dead", "cancelled")


def _row(d: Delivery, dest: Optional[Destination], doc: Optional[Document]) -> Dict[str, Any]:
    return {
        "id": d.id, "status": d.status, "hold_reason": d.hold_reason, "revision": d.revision, "event": d.event, "attempts": d.attempts,
        "next_attempt_at": d.next_attempt_at, "last_error": d.last_error, "last_status_code": d.last_status_code, "delivered_at": d.delivered_at,
        "external_ref": d.external_ref, "created_at": d.created_at, "updated_at": d.updated_at,
        "destination": {"id": dest.id, "name": dest.name, "kind": dest.kind, "status": dest.status} if dest else None,
        "document": {"id": doc.id, "filename": doc.original_filename, "external_id": doc.external_id, "is_test": doc.is_test} if doc else None,
    }


@router.get("")
async def list_deliveries(
    status: Optional[str] = Query(None, description="One or more of held, pending, sending, retrying, delivered, dead, cancelled, separated by commas"),
    destination_id: Optional[str] = None,
    document_id: Optional[str] = None,
    q: Optional[str] = Query(None, description="Part of a file name, your own reference or an error"),
    limit: int = Query(30, ge=1, le=100),
    offset: int = Query(0, ge=0),
    access: Access = Depends(require("integrations.manage")), db: AsyncSession = Depends(get_db_session),
):
    stmt = select(Delivery, Destination, Document).join(Destination, Destination.id == Delivery.destination_id).join(Document, Document.id == Delivery.document_id) \
        .where(Delivery.template_id == access.scope.template_id)
    if status:
        wanted = [s for s in status.split(",") if s in STATUSES]
        if wanted:
            stmt = stmt.where(Delivery.status.in_(wanted))
    if destination_id:
        stmt = stmt.where(Delivery.destination_id == destination_id)
    if document_id:
        stmt = stmt.where(Delivery.document_id == document_id)
    if q and q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(Document.original_filename.ilike(like), Document.external_id.ilike(like), Delivery.last_error.ilike(like), Delivery.external_ref.ilike(like)))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (await db.execute(stmt.order_by(Delivery.created_at.desc()).limit(limit).offset(offset))).all()
    return {"items": [_row(d, dest, doc) for d, dest, doc in rows], "total": total}


@router.get("/stats")
async def stats(access: Access = Depends(require("integrations.manage")), db: AsyncSession = Depends(get_db_session)):
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    t = access.scope.template_id
    by_status = dict((await db.execute(select(Delivery.status, func.count(Delivery.id)).where(Delivery.template_id == t).group_by(Delivery.status))).all())
    delivered_24h = (await db.execute(select(func.count(Delivery.id)).where(Delivery.template_id == t, Delivery.status == "delivered", Delivery.delivered_at >= since))).scalar_one()
    return {"by_status": {s: by_status.get(s, 0) for s in STATUSES}, "delivered_24h": delivered_24h,
            "waiting": by_status.get("pending", 0) + by_status.get("retrying", 0) + by_status.get("sending", 0)}


async def _load(db: AsyncSession, access: Access, delivery_id: str) -> Delivery:
    d = await db.get(Delivery, delivery_id)
    if d is None or d.template_id != access.scope.template_id:
        raise HTTPException(status_code=404, detail="Delivery not found.")
    return d


@router.get("/{delivery_id}")
async def delivery_detail(delivery_id: str, access: Access = Depends(require("integrations.manage")), db: AsyncSession = Depends(get_db_session)):
    d = await _load(db, access, delivery_id)
    dest, doc = await db.get(Destination, d.destination_id), await db.get(Document, d.document_id)
    attempts = (await db.execute(select(DeliveryAttempt).where(DeliveryAttempt.delivery_id == d.id).order_by(DeliveryAttempt.attempt_no.desc(), DeliveryAttempt.id.desc()))).scalars().all()
    envelope, problems = (None, [])
    if dest is not None and dest.kind in ADAPTERS:
        envelope, problems = delivery_service.build_envelope(d, dest, {"id": access.scope.template_id, "name": access.scope.template.name})
    return {
        **_row(d, dest, doc), "idempotency_key": d.idempotency_key, "message": envelope, "message_problems": problems,
        "attempt_log": [{"no": a.attempt_no, "ok": a.ok, "status_code": a.status_code, "error": a.error, "duration_ms": a.duration_ms, "request": a.request,
                         "response": a.response, "at": a.created_at} for a in attempts],
    }


def _audit(db: AsyncSession, access: Access, event: str, d: Delivery, **details: Any) -> None:
    db.add(AuditLog(tenant_id=access.scope.tenant_id, template_id=access.scope.template_id, event_type=event, entity_type="Delivery", entity_id=d.id,
                    actor=access.auth.user_id, details={"destination_id": d.destination_id, "document_id": d.document_id, **details}))


async def _announce(access: Access, **data: Any) -> None:
    delivery_service.delivery_worker.notify()
    await publish_ui_event(f"template:{access.scope.template_id}", "delivery_changed", data, tenant_id=access.scope.tenant_id, template_id=access.scope.template_id)


@router.post("/{delivery_id}/replay")
async def replay_one(delivery_id: str, access: Access = Depends(require("integrations.manage")), db: AsyncSession = Depends(get_db_session)):
    """Sends it again from the start. The receiver sees the same delivery id and Idempotency-Key as before."""
    d = await _load(db, access, delivery_id)
    if d.status in ("sending",):
        raise HTTPException(status_code=409, detail="It is being sent right now.")
    await delivery_service.replay(db, d)
    _audit(db, access, "DELIVERY_REPLAYED", d)
    await db.commit()
    await _announce(access, delivery_id=d.id)
    return {"id": d.id, "status": d.status}


@router.post("/{delivery_id}/release")
async def release_one(delivery_id: str, access: Access = Depends(require("integrations.manage")), db: AsyncSession = Depends(get_db_session)):
    """
    Sends a held delivery even though something is still open about the document (a question, a conversation, a review). A person takes
    responsibility for that; it is recorded. A document that is not approved can't be sent this way.
    """
    d = await _load(db, access, delivery_id)
    if d.status != "held":
        raise HTTPException(status_code=409, detail="Only a held delivery can be released.")
    document = await db.get(Document, d.document_id)
    if document is None or document.status != DocumentStatus.COMPLETED:
        raise HTTPException(status_code=409, detail="This document is not approved, so it can't be sent. Approve it first.")
    was = d.hold_reason
    delivery_service.override(d, access.auth.username or access.auth.user_id)
    if (await db.get(Destination, d.destination_id)).status == "active":
        d.status, d.hold_reason, d.next_attempt_at = "pending", None, datetime.now(timezone.utc)
    _audit(db, access, "DELIVERY_RELEASED", d, was=was)
    await db.commit()
    await _announce(access, delivery_id=d.id)
    return {"id": d.id, "status": d.status}


@router.post("/{delivery_id}/cancel")
async def cancel_one(delivery_id: str, access: Access = Depends(require("integrations.manage")), db: AsyncSession = Depends(get_db_session)):
    """Gives up on a delivery that has not gone out. It stays in the list as cancelled, and can be replayed."""
    d = await _load(db, access, delivery_id)
    if d.status in ("delivered", "sending"):
        raise HTTPException(status_code=409, detail="It has already been sent or is being sent.")
    d.status, d.next_attempt_at, d.hold_reason = "cancelled", None, None
    d.last_error = d.last_error or f"Cancelled by {access.auth.username or 'a person'}."
    _audit(db, access, "DELIVERY_CANCELLED", d)
    await db.commit()
    await _announce(access, delivery_id=d.id)
    return {"id": d.id, "status": d.status}


class BulkIn(BaseModel):
    ids: List[str] = Field(default_factory=list)
    destination_id: Optional[str] = None
    status: str = Field("dead", description="Which deliveries to replay when no ids are given: dead or cancelled")


@router.post("/replay")
async def replay_many(body: BulkIn, access: Access = Depends(require("integrations.manage")), db: AsyncSession = Depends(get_db_session)):
    """Replays the named deliveries, or every dead one (optionally of one destination). At most 500 at a time."""
    stmt = select(Delivery).where(Delivery.template_id == access.scope.template_id)
    if body.ids:
        stmt = stmt.where(Delivery.id.in_(body.ids[:500]), Delivery.status != "sending")
    else:
        if body.status not in ("dead", "cancelled"):
            raise HTTPException(status_code=422, detail="Choose dead or cancelled deliveries to replay.")
        stmt = stmt.where(Delivery.status == body.status)
        if body.destination_id:
            stmt = stmt.where(Delivery.destination_id == body.destination_id)
    rows = (await db.execute(stmt.order_by(Delivery.created_at).limit(500))).scalars().all()
    for d in rows:
        await delivery_service.replay(db, d)
    if rows:
        db.add(AuditLog(tenant_id=access.scope.tenant_id, template_id=access.scope.template_id, event_type="DELIVERIES_REPLAYED", entity_type="Delivery",
                        entity_id=rows[0].id, actor=access.auth.user_id, details={"count": len(rows), "destination_id": body.destination_id}))
    await db.commit()
    await _announce(access, replayed=len(rows))
    return {"replayed": len(rows)}
