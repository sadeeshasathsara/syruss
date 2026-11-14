"""
Organizations and people, for platform staff: finding them, and the two switches that stop harm quickly. Suspending an
organization shuts it for everyone in it until it is reactivated (nothing is removed); disabling an account stops one
person signing in to anything and forgets their browsers. Both need a reason, which goes in the platform audit log, and
a suspension shows in the organization's own activity. Neither opens any content.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.v1.sysadmin.access import Staff, record, require_staff
from app.api.v1.sysadmin.common import tell_organization
from app.core.config import settings
from app.core.database import get_db_session
from app.models.identity import OrgMember, TrustedDevice, User
from app.models.platform import PlatformStaff
from app.models.tenant import Tenant
from app.models.workspace import Workspace

router = APIRouter()


class Reason(BaseModel):
    reason: str = Field(..., min_length=5, max_length=1000)


def _like(q: str) -> str:
    return "%" + q.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


# ---- organizations ------------------------------------------------------------------------------

async def _organization(db: AsyncSession, tenant: Tenant) -> Dict[str, Any]:
    members = (await db.execute(select(func.count(OrgMember.id)).where(OrgMember.tenant_id == tenant.id))).scalar() or 0
    workspaces = (await db.execute(select(func.count(Workspace.id)).where(Workspace.tenant_id == tenant.id))).scalar() or 0
    return {"id": tenant.id, "name": tenant.name, "slug": tenant.slug, "status": tenant.status, "status_reason": tenant.status_reason,
            "status_changed_at": tenant.status_changed_at, "purged_at": tenant.purged_at, "created_at": tenant.created_at,
            "members": members, "workspaces": workspaces}


@router.get("/organizations", dependencies=[Depends(require_staff())])
async def organizations(q: Optional[str] = Query(None, max_length=100), status: Optional[str] = Query(None, pattern="^(active|suspended|closed)$"),
                        limit: int = Query(50, ge=1, le=200), db: AsyncSession = Depends(get_db_session)) -> List[Dict[str, Any]]:
    """Organizations by name or address, newest first: whether each is open, and how many people and workspaces it has."""
    stmt = select(Tenant)
    if q and q.strip():
        stmt = stmt.where(or_(func.lower(Tenant.name).like(_like(q), escape="\\"), func.lower(Tenant.slug).like(_like(q), escape="\\")))
    if status:
        stmt = stmt.where(Tenant.status == status)
    rows = (await db.execute(stmt.order_by(Tenant.created_at.desc()).limit(limit))).scalars().all()
    return [await _organization(db, t) for t in rows]


async def _set_status(db: AsyncSession, staff: Staff, tenant_id: str, status: str, reason: str, said: str) -> Dict[str, Any]:
    tenant = await db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="No such organization.")
    if tenant.status == "closed":
        raise HTTPException(status_code=409, detail="Its owner closed this organization. It can't be suspended or reopened from here.")
    if tenant.status == status:
        raise HTTPException(status_code=409, detail=f"The organization is already {status}.")
    before = tenant.status
    tenant.set_status(status, reason.strip() if status == "suspended" else None)
    record(db, staff, f"organization.{'suspended' if status == 'suspended' else 'reactivated'}", "tenant", tenant.id, tenant_id=tenant.id,
           reason=reason, details={"organization": tenant.name, "from": before, "to": status})
    tell_organization(db, staff, tenant.id, said, reason.strip())
    await db.commit()
    return await _organization(db, tenant)


@router.post("/organizations/{tenant_id}/suspend")
async def suspend(tenant_id: str, body: Reason, staff: Staff = Depends(require_staff("tenants.manage", recent=True)),
                  db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    """Shuts the organization for everyone in it, and for its keys and channels, until it is reactivated. Nothing is removed."""
    return await _set_status(db, staff, tenant_id, "suspended", body.reason, "suspended the organization")


@router.post("/organizations/{tenant_id}/reactivate")
async def reactivate(tenant_id: str, body: Reason, staff: Staff = Depends(require_staff("tenants.manage", recent=True)),
                     db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    return await _set_status(db, staff, tenant_id, "active", body.reason, "reactivated the organization")


# ---- people -------------------------------------------------------------------------------------

async def _person(db: AsyncSession, user: User) -> Dict[str, Any]:
    orgs = (await db.execute(select(Tenant.id, Tenant.name, OrgMember.role).join(OrgMember, OrgMember.tenant_id == Tenant.id)
                             .where(OrgMember.user_id == user.id).order_by(Tenant.name))).all()
    return {"id": user.id, "name": user.display_name, "email": user.email, "status": user.status, "last_seen_at": user.last_seen_at,
            "created_at": user.created_at, "organizations": [{"id": i, "name": n, "role": r} for i, n, r in orgs],
            "is_staff": user.id in settings.platform_admin_ids or await db.get(PlatformStaff, user.id) is not None}


@router.get("/people", dependencies=[Depends(require_staff())])
async def people(q: str = Query(..., min_length=2, max_length=100), limit: int = Query(25, ge=1, le=100),
                 db: AsyncSession = Depends(get_db_session)) -> List[Dict[str, Any]]:
    """Accounts by name or email (at least two letters: this is a lookup, not a list of everyone)."""
    like = _like(q)
    rows = (await db.execute(select(User).where(or_(
        func.lower(User.email).like(like, escape="\\"), func.lower(User.first_name + " " + User.last_name).like(like, escape="\\")))
        .order_by(User.created_at.desc()).limit(limit))).scalars().all()
    return [await _person(db, u) for u in rows]


async def _set_account(db: AsyncSession, staff: Staff, user_id: str, status: str, reason: str) -> Dict[str, Any]:
    user = await db.get(User, user_id)
    if user is None or user.status == "deleted":
        raise HTTPException(status_code=404, detail="No such account.")
    if user.id == staff.user_id:
        raise HTTPException(status_code=409, detail="You can't disable your own account.")
    if status == "disabled" and user.id in settings.platform_admin_ids:
        raise HTTPException(status_code=409, detail="This platform owner is set by the deployment and can't be disabled here.")
    if user.status == status:
        raise HTTPException(status_code=409, detail=f"The account is already {status}.")
    user.status = status
    if status == "disabled":  # a browser they confirmed is no longer a way back in once the account is enabled again
        await db.execute(update(TrustedDevice).where(TrustedDevice.user_id == user.id, TrustedDevice.status == "trusted")
                         .values(status="revoked", code_hash=""))
    record(db, staff, f"account.{'disabled' if status == 'disabled' else 'enabled'}", "user", user.id, reason=reason,
           details={"name": user.display_name, "email": user.email, "at": datetime.now(timezone.utc).isoformat()})
    await db.commit()
    return await _person(db, user)


@router.post("/people/{user_id}/disable")
async def disable(user_id: str, body: Reason, staff: Staff = Depends(require_staff("users.manage", recent=True)),
                  db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    """Stops the person using Syruss at all (every organization, and the admin app) and forgets their browsers."""
    return await _set_account(db, staff, user_id, "disabled", body.reason)


@router.post("/people/{user_id}/enable")
async def enable(user_id: str, body: Reason, staff: Staff = Depends(require_staff("users.manage", recent=True)),
                 db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    return await _set_account(db, staff, user_id, "active", body.reason)
