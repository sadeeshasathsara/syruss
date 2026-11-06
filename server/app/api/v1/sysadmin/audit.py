"""What platform staff did, newest first, a page at a time. Read-only: the log can't be changed by anyone."""
from datetime import datetime
from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.v1.sysadmin.access import require_staff
from app.core.database import get_db_session
from app.models.platform import PlatformAuditEntry

router = APIRouter(prefix="/audit")


@router.get("", dependencies=[Depends(require_staff())])
async def staff_actions(
    limit: int = Query(50, ge=1, le=200), before: Optional[datetime] = None,
    action: Optional[str] = Query(None, description="Actions or their groups, comma separated: 'staff' matches staff.added, staff.removed…"),
    actor_id: Optional[str] = None, tenant_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db_session),
) -> Dict[str, Any]:
    stmt = select(PlatformAuditEntry)
    if before:
        stmt = stmt.where(PlatformAuditEntry.created_at < before)
    if action:
        stmt = stmt.where(or_(*((PlatformAuditEntry.action == a) | PlatformAuditEntry.action.startswith(f"{a}.")
                                for a in action.split(",") if a)))
    if actor_id:
        stmt = stmt.where(PlatformAuditEntry.actor_id == actor_id)
    if tenant_id:
        stmt = stmt.where(PlatformAuditEntry.tenant_id == tenant_id)
    rows = (await db.execute(stmt.order_by(PlatformAuditEntry.created_at.desc()).limit(limit + 1))).scalars().all()
    items = [{
        "id": r.id, "created_at": r.created_at, "actor_id": r.actor_id, "actor_name": r.actor_name, "action": r.action,
        "target_type": r.target_type, "target_id": r.target_id, "tenant_id": r.tenant_id, "reason": r.reason,
        "details": r.details, "ip": r.ip,
    } for r in rows[:limit]]
    return {"items": items, "next_before": items[-1]["created_at"] if len(rows) > limit else None}
