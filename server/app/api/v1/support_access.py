"""
Syruss support's access to the organization's content, as the organization sees and controls it.

Everyone in the organization sees when support has its content open (and the app shows it while it does). Owners and
admins (org.manage) end an open access at any time, and decide whether support may open the content at all: turned off,
no access opens and any open one ends at once. Turning it back on asks for a recent sign-in. Each change is in the
organization's activity. The staff side is api/v1/sysadmin/support_access.py.
"""
from datetime import datetime, timezone
from typing import Any, Dict
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.auth import AuthContext, get_current_auth, require_org_permission
from app.core.auth.dependencies import require_recent_login
from app.core.database import get_db_session
from app.models.audit import AuditLog
from app.models.platform import PlatformAccessGrant
from app.models.tenant import Tenant

router = APIRouter(prefix="/organization/support-access", tags=["Organization"])


def _grant(g: PlatformAccessGrant, now: datetime) -> Dict[str, Any]:
    open_ = g.ended_at is None and g.expires_at > now
    return {"id": g.id, "staff": g.staff_name, "reason": g.reason, "opened_at": g.created_at, "until": g.expires_at, "open": open_,
            "ended_at": g.ended_at or (None if open_ else g.expires_at), "ended_by": g.ended_by_name}


@router.get("")
async def support_access(auth: AuthContext = Depends(get_current_auth), db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    """Whether support may open the organization's content, who has it open now, and the last times it was opened."""
    if auth.user_id.startswith("apikey:"):
        raise HTTPException(status_code=403, detail="For people, not keys.")
    tenant = await db.get(Tenant, auth.tenant_id)
    now = datetime.now(timezone.utc)
    rows = (await db.execute(select(PlatformAccessGrant).where(PlatformAccessGrant.tenant_id == auth.tenant_id)
                             .order_by(PlatformAccessGrant.created_at.desc()).limit(20))).scalars().all()
    grants = [_grant(g, now) for g in rows]
    return {"allowed": tenant.support_access, "can_manage": auth.can_org("org.manage"),
            "open": [g for g in grants if g["open"]] if tenant.support_access else [], "recent": grants}


class Allowed(BaseModel):
    allowed: bool


@router.put("")
async def set_support_access(body: Allowed, auth: AuthContext = Depends(require_org_permission("org.manage")),
                             db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    """Allows Syruss support to open the content (each time for a reason and a while), or stops it, ending any open access."""
    tenant = await db.get(Tenant, auth.tenant_id)
    if body.allowed and not tenant.support_access:
        await require_recent_login(auth)  # letting someone in asks more than shutting them out
    if tenant.support_access != body.allowed:
        tenant.support_access = body.allowed
        ended = 0
        if not body.allowed:
            now = datetime.now(timezone.utc)
            ended = (await db.execute(update(PlatformAccessGrant).where(
                PlatformAccessGrant.tenant_id == tenant.id, PlatformAccessGrant.ended_at.is_(None), PlatformAccessGrant.expires_at > now,
            ).values(ended_at=now, ended_by_name=auth.username or "the organization"))).rowcount
        db.add(AuditLog(tenant_id=tenant.id, event_type="SUPPORT_ACCESS_ALLOWED" if body.allowed else "SUPPORT_ACCESS_STOPPED",
                        entity_type="Organization", entity_id=tenant.id, actor=auth.user_id, details={"ended": ended}))
        await db.commit()
    return await support_access(auth, db)


@router.post("/{grant_id}/end")
async def end_access(grant_id: str, auth: AuthContext = Depends(require_org_permission("org.manage")),
                     db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    """Ends Syruss support's open access to the organization's content now."""
    grant = await db.get(PlatformAccessGrant, grant_id)
    if grant is None or grant.tenant_id != auth.tenant_id:
        raise HTTPException(status_code=404, detail="No such access.")
    now = datetime.now(timezone.utc)
    if grant.ended_at is None and grant.expires_at > now:
        grant.ended_at, grant.ended_by_name = now, auth.username or "the organization"
        db.add(AuditLog(tenant_id=grant.tenant_id, event_type="SUPPORT_ACCESS_ENDED", entity_type="Organization", entity_id=grant.tenant_id,
                        actor=auth.user_id, details={"staff": grant.staff_name, "grant_id": grant.id}))
        await db.commit()
    return await support_access(auth, db)
