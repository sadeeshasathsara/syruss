"""The Connections overview: documents coming in, the records on their way out, what is held back and why, and what needs attention."""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List
from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db_session
from app.core.permissions import Access, require
from app.models.delivery import Delivery, Destination
from app.models.document import Document
from app.models.intake import IntakeChannel, IntakeEvent

router = APIRouter(prefix="/connections", tags=["Connections"])


@router.get("/overview")
async def overview(access: Access = Depends(require("integrations.manage")), db: AsyncSession = Depends(get_db_session)):
    t = access.scope.template_id
    since = datetime.now(timezone.utc) - timedelta(hours=24)

    channels = list((await db.execute(select(IntakeChannel).where(IntakeChannel.template_id == t))).scalars().all())
    intake = dict((await db.execute(select(IntakeEvent.outcome, func.count(IntakeEvent.id)).where(IntakeEvent.template_id == t, IntakeEvent.created_at >= since)
                                    .group_by(IntakeEvent.outcome))).all())
    destinations = list((await db.execute(select(Destination).where(Destination.template_id == t))).scalars().all())
    by_status = dict((await db.execute(select(Delivery.status, func.count(Delivery.id)).where(Delivery.template_id == t).group_by(Delivery.status))).all())
    delivered_24h = (await db.execute(select(func.count(Delivery.id)).where(Delivery.template_id == t, Delivery.status == "delivered", Delivery.delivered_at >= since))).scalar_one()

    held_rows = (await db.execute(
        select(Delivery, Destination, Document).join(Destination, Destination.id == Delivery.destination_id).join(Document, Document.id == Delivery.document_id)
        .where(Delivery.template_id == t, Delivery.status == "held").order_by(Delivery.created_at.desc()).limit(8)
    )).all()
    reasons: Dict[str, int] = {}
    for reason, n in (await db.execute(select(Delivery.hold_reason, func.count(Delivery.id)).where(Delivery.template_id == t, Delivery.status == "held").group_by(Delivery.hold_reason))).all():
        reasons[reason or "Waiting"] = n

    problems: List[Dict[str, Any]] = []
    for d in destinations:
        if d.status == "paused":
            problems.append({"kind": "destination_paused", "title": f"{d.name} is paused", "detail": d.paused_reason, "destination_id": d.id, "automatic": d.paused_by == "system"})
    dead = (await db.execute(
        select(Delivery, Destination, Document).join(Destination, Destination.id == Delivery.destination_id).join(Document, Document.id == Delivery.document_id)
        .where(Delivery.template_id == t, Delivery.status == "dead").order_by(Delivery.updated_at.desc()).limit(5)
    )).all()
    for d, dest, doc in dead:
        problems.append({"kind": "delivery_dead", "title": f"{doc.original_filename} could not be sent to {dest.name}", "detail": d.last_error, "delivery_id": d.id, "destination_id": dest.id})
    for c in channels:
        if c.status == "paused":
            problems.append({"kind": "channel_paused", "title": f"{c.name} is paused", "detail": "Documents sent through it are refused.", "channel_id": c.id, "slug": c.slug})

    rejected = intake.get("rejected", 0)
    state = "problem" if any(p["kind"] in ("delivery_dead", "destination_paused") for p in problems) else "attention" if problems or rejected else "ok"
    return {
        "state": state,
        "receive": {"channels": len(channels), "active": sum(1 for c in channels if c.status == "active"), "accepted_24h": intake.get("accepted", 0), "rejected_24h": rejected},
        "publish": {"destinations": len(destinations), "active": sum(1 for d in destinations if d.status == "active"), "paused": sum(1 for d in destinations if d.status == "paused"),
                    "delivered_24h": delivered_24h, "waiting": by_status.get("pending", 0) + by_status.get("retrying", 0) + by_status.get("sending", 0),
                    "held": by_status.get("held", 0), "dead": by_status.get("dead", 0)},
        "held": [{"delivery_id": d.id, "reason": d.hold_reason, "destination": dest.name, "document": {"id": doc.id, "filename": doc.original_filename, "external_id": doc.external_id}, "since": d.created_at}
                 for d, dest, doc in held_rows],
        "held_reasons": [{"reason": r, "count": n} for r, n in sorted(reasons.items(), key=lambda x: -x[1])],
        "problems": problems,
        "failing": by_status.get("dead", 0) + sum(1 for d in destinations if d.status == "paused"),
    }
