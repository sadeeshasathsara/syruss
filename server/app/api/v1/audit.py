from typing import List, Optional
from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.database import get_db_session
from app.core.auth import AuthContext, get_current_auth
from app.models.audit import AuditLog
from app.schemas.audit import AuditLogResponse
from fastapi import HTTPException
from app.core.permissions import check_permission
from app.core.scope import resolve_template_scope

router = APIRouter(prefix="/audit", tags=["Audit Logs"])


@router.get("", response_model=List[AuditLogResponse])
async def list_audit_logs(
    event_type: Optional[str] = None,
    x_template_id: Optional[str] = Header(None, alias="X-Template-ID"),
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db_session),
    auth: AuthContext = Depends(get_current_auth)
):
    """List audit events for the tenant to ensure full traceability and compliance."""
    stmt = select(AuditLog).where(AuditLog.tenant_id == auth.tenant_id)
    if x_template_id:
        await check_permission(db, await resolve_template_scope(db, auth, x_template_id), auth, "audit.view")
        stmt = stmt.where(AuditLog.template_id == x_template_id)
    elif not auth.can_org("org.audit.view"):
        raise HTTPException(status_code=403, detail="Your organization role can't read the organization-wide audit log.")
    if event_type:
        stmt = stmt.where(AuditLog.event_type == event_type)
    stmt = stmt.order_by(AuditLog.created_at.desc()).offset(offset).limit(limit)
    result = await db.execute(stmt)
    return list(result.scalars().all())
