"""
Staff access to a customer's content: documents and their records, chats, and what the agent thought and did.

Without a grant, staff see facts only (what ran, where, when, how long, what it cost, how it ended). A grant opens one
organization's content to one staff member, for a stated reason, until a set time. Opening and closing it go in the
platform audit log and in the organization's own activity, so the customer can see that Syruss support looked, and why.
Each piece of content opened under a grant (a run, a document) is written to the platform audit log once.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.v1.sysadmin.access import Staff, record, require_staff
from app.core.database import get_db_session
from app.models.audit import AuditLog
from app.models.platform import PlatformAccessGrant, PlatformAuditEntry
from app.models.tenant import Tenant

router = APIRouter()

MIN_MINUTES, MAX_MINUTES = 15, 240


def grant_dict(g: PlatformAccessGrant, tenant: Optional[Tenant] = None) -> Dict[str, Any]:
    return {"id": g.id, "tenant_id": g.tenant_id, "organization": tenant.name if tenant else None, "reason": g.reason,
            "created_at": g.created_at, "expires_at": g.expires_at, "ended_at": g.ended_at}


async def active_grant(db: AsyncSession, staff: Staff, tenant_id: str) -> Optional[PlatformAccessGrant]:
    """The staff member's open grant to this organization's content, if any (only someone with support.access has one)."""
    if not staff.can("support.access"):
        return None
    now = datetime.now(timezone.utc)
    return (await db.execute(select(PlatformAccessGrant).where(
        PlatformAccessGrant.staff_id == staff.user_id, PlatformAccessGrant.tenant_id == tenant_id,
        PlatformAccessGrant.expires_at > now, PlatformAccessGrant.ended_at.is_(None),
    ).order_by(PlatformAccessGrant.expires_at.desc()).limit(1))).scalar_one_or_none()


async def open_content(db: AsyncSession, staff: Staff, tenant_id: str, target_type: str, target_id: str) -> Optional[PlatformAccessGrant]:
    """
    The grant under which this content may be shown, writing down once per grant that it was opened; None without one
    (the caller then shows facts only).
    """
    grant = await active_grant(db, staff, tenant_id)
    if grant is None:
        return None
    seen = (await db.execute(select(PlatformAuditEntry.id).where(
        PlatformAuditEntry.actor_id == staff.user_id, PlatformAuditEntry.action == "content.viewed",
        PlatformAuditEntry.target_type == target_type, PlatformAuditEntry.target_id == target_id,
        PlatformAuditEntry.created_at >= grant.created_at).limit(1))).first()
    if seen is None:
        record(db, staff, "content.viewed", target_type, target_id, tenant_id=tenant_id, reason=grant.reason,
               details={"grant_id": grant.id})
        await db.commit()
    return grant


def content_refused() -> HTTPException:
    return HTTPException(status_code=403, detail={
        "code": "content_access_required",
        "message": "Open access to this organization's content first, with a reason. Without it you see facts only."})


class GrantIn(BaseModel):
    tenant_id: str
    reason: str = Field(..., min_length=5, max_length=1000)
    minutes: int = Field(60, ge=MIN_MINUTES, le=MAX_MINUTES)


@router.get("/access")
async def my_grants(staff: Staff = Depends(require_staff()), db: AsyncSession = Depends(get_db_session)):
    """Your open grants, newest first."""
    now = datetime.now(timezone.utc)
    rows = (await db.execute(select(PlatformAccessGrant, Tenant).join(Tenant, Tenant.id == PlatformAccessGrant.tenant_id).where(
        PlatformAccessGrant.staff_id == staff.user_id, PlatformAccessGrant.expires_at > now, PlatformAccessGrant.ended_at.is_(None),
    ).order_by(PlatformAccessGrant.created_at.desc()))).all()
    return {"items": [grant_dict(g, t) for g, t in rows] if staff.can("support.access") else [],
            "may_open": staff.can("support.access"), "minutes": {"min": MIN_MINUTES, "max": MAX_MINUTES, "default": 60}}


@router.post("/access", status_code=201)
async def open_grant(body: GrantIn, staff: Staff = Depends(require_staff("support.access", recent=True)),
                     db: AsyncSession = Depends(get_db_session)):
    tenant = await db.get(Tenant, body.tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="No such organization.")
    now = datetime.now(timezone.utc)
    previous = await active_grant(db, staff, tenant.id)
    if previous is not None:  # a new grant replaces the open one: one reason, one end time
        previous.ended_at = now
    grant = PlatformAccessGrant(staff_id=staff.user_id, staff_name=staff.name, tenant_id=tenant.id, reason=body.reason.strip(),
                                created_at=now, expires_at=now + timedelta(minutes=body.minutes))
    db.add(grant)
    await db.flush()
    until = f"{grant.expires_at:%d %b %H:%M} UTC"
    record(db, staff, "access.opened", "tenant", tenant.id, tenant_id=tenant.id, reason=grant.reason,
           details={"grant_id": grant.id, "minutes": body.minutes, "organization": tenant.name})
    db.add(AuditLog(tenant_id=tenant.id, event_type="PLATFORM_ACCESS_OPENED", entity_type="Organization", entity_id=tenant.id,
                    actor="platform", details={"staff": staff.name, "reason": grant.reason, "until": until, "grant_id": grant.id}))
    await db.commit()
    return grant_dict(grant, tenant)


@router.delete("/access/{grant_id}", status_code=204)
async def end_grant(grant_id: str, staff: Staff = Depends(require_staff()), db: AsyncSession = Depends(get_db_session)):
    grant = await db.get(PlatformAccessGrant, grant_id)
    if grant is None or grant.staff_id != staff.user_id:
        raise HTTPException(status_code=404, detail="No such access.")
    if grant.ended_at is None and grant.expires_at > datetime.now(timezone.utc):
        grant.ended_at = datetime.now(timezone.utc)
        record(db, staff, "access.ended", "tenant", grant.tenant_id, tenant_id=grant.tenant_id, details={"grant_id": grant.id})
        db.add(AuditLog(tenant_id=grant.tenant_id, event_type="PLATFORM_ACCESS_ENDED", entity_type="Organization",
                        entity_id=grant.tenant_id, actor="platform", details={"staff": staff.name, "grant_id": grant.id}))
        await db.commit()
