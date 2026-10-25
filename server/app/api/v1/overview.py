"""
The workspace overview: how work is flowing, in numbers a person can read at a glance. Any member may see it; it
holds counts and averages, never document contents.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict
from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db_session
from app.core.scope import TemplateScope, get_template_scope
from app.models.document import Document, DocumentRole, DocumentStatus
from app.models.review import ReviewTask
from app.services import settings_service

router = APIRouter(prefix="/overview", tags=["Workspace overview"])

WORKING = (DocumentStatus.PENDING, DocumentStatus.QUEUED, DocumentStatus.PROCESSING)


@router.get("")
async def overview(days: int = Query(14, ge=7, le=60), db: AsyncSession = Depends(get_db_session), scope: TemplateScope = Depends(get_template_scope)) -> Dict[str, Any]:
    real = (Document.template_id == scope.template_id, Document.role == DocumentRole.DOCUMENT)
    today = datetime.now(timezone.utc).date()
    first = today - timedelta(days=days - 1)

    # Received per day, split by where each document stands now.
    day = func.date(Document.created_at)
    rows = (await db.execute(
        select(day, Document.status, func.count(Document.id))
        .where(*real, Document.created_at >= datetime.combine(first, datetime.min.time(), tzinfo=timezone.utc))
        .group_by(day, Document.status)
    )).all()
    series = {first + timedelta(days=i): {"saved": 0, "review": 0, "failed": 0, "working": 0} for i in range(days)}
    for d, status, n in rows:
        bucket = series.get(d)
        if bucket is None:
            continue
        key = "saved" if status == DocumentStatus.COMPLETED else "failed" if status == DocumentStatus.FAILED else \
            "review" if status in (DocumentStatus.NEEDS_REVIEW, DocumentStatus.AWAITING_INPUT) else "working"
        bucket[key] += n

    by_status = dict((await db.execute(select(Document.status, func.count(Document.id)).where(*real).group_by(Document.status))).all())
    count = lambda *s: sum(by_status.get(x, 0) for x in s)  # noqa: E731
    totals = {
        "received": sum(by_status.values()), "saved": count(DocumentStatus.COMPLETED), "needs_review": count(DocumentStatus.NEEDS_REVIEW),
        "waiting": count(DocumentStatus.AWAITING_INPUT), "failed": count(DocumentStatus.FAILED), "working": count(*WORKING),
    }

    # Of the documents saved, how many never needed a person; and how long the agent takes.
    saved_stats = (await db.execute(
        select(
            func.count(Document.id),
            func.count(case((ReviewTask.id.is_(None), 1))),
            func.avg(func.extract("epoch", Document.updated_at - Document.created_at)),
        ).select_from(Document).outerjoin(ReviewTask, ReviewTask.document_id == Document.id)
        .where(*real, Document.status == DocumentStatus.COMPLETED, Document.created_at >= datetime.now(timezone.utc) - timedelta(days=30))
    )).one()
    saved_recent, hands_free, avg_seconds = saved_stats
    last = (await db.execute(select(func.max(Document.updated_at)).where(*real))).scalar()

    usage = await settings_service.usage(db, scope.template_id)
    plan = settings_service.PLANS[(await settings_service.get_settings(db, scope.template_id)).plan]
    return {
        "days": [{"date": d.isoformat(), **v, "received": sum(v.values())} for d, v in sorted(series.items())],
        "totals": totals,
        "hands_free_rate": round(hands_free / saved_recent, 3) if saved_recent else None,
        "avg_seconds_to_save": round(float(avg_seconds), 1) if avg_seconds is not None and saved_recent else None,
        "last_activity_at": last,
        "usage": {"documents_this_month": usage["documents_this_month"], "documents_limit": plan["documents_per_month"],
                  "members": usage["members"], "members_limit": plan["members"], "plan": plan["name"]},
    }
