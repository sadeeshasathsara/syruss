"""
The platform staff: who they are and what role each has. Only someone who may manage staff (an owner) changes it, after
a fresh sign-in, and every change is in the platform audit log. Owners named in PLATFORM_ADMIN_USER_IDS are listed but
can't be changed here: the deployment sets them, so the platform can never be left without an owner.
"""
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.v1.sysadmin.access import Staff, record, require_staff
from app.core.config import settings
from app.core.database import get_db_session
from app.core.platform_permissions import PLATFORM_GRANTS, PLATFORM_ROLES
from app.models.identity import User
from app.models.platform import PlatformStaff

router = APIRouter(prefix="/staff")

Role = Literal["owner", "operator", "support", "billing", "viewer"]


class StaffMember(BaseModel):
    user_id: str
    name: str
    email: Optional[str] = None
    role: str
    permissions: List[str]
    from_config: bool
    added_by: Optional[str] = None
    added_at: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None
    status: str = "active"


class AddStaff(BaseModel):
    email: str = Field(..., max_length=255, description="The account to add: they must have signed up to Syruss already")
    role: Role
    reason: Optional[str] = Field(None, max_length=500)


class ChangeRole(BaseModel):
    role: Role
    reason: Optional[str] = Field(None, max_length=500)


def _member(user: Optional[User], user_id: str, role: str, *, from_config: bool, row: Optional[PlatformStaff] = None) -> StaffMember:
    return StaffMember(
        user_id=user_id, name=(user.display_name if user else None) or user_id, email=user.email if user else None, role=role,
        permissions=sorted(PLATFORM_GRANTS[role]), from_config=from_config, added_by=row.added_by if row else None,
        added_at=row.created_at if row else None, last_seen_at=user.last_seen_at if user else None,
        status=user.status if user else "no account yet",
    )


def _who(user: Optional[User]) -> Dict[str, Any]:
    """Who an entry is about, kept in the entry: the log outlives accounts."""
    return {"name": user.display_name, "email": user.email} if user else {}


async def _owners_left(db: AsyncSession, without: str) -> int:
    """Owners the platform would still have without this person: the configured ones count too."""
    stored = await db.scalar(select(func.count()).select_from(PlatformStaff).where(PlatformStaff.role == "owner", PlatformStaff.user_id != without))
    return stored + len(settings.platform_admin_ids - {without})


async def _changeable(db: AsyncSession, staff: Staff, user_id: str) -> PlatformStaff:
    if user_id in settings.platform_admin_ids:
        raise HTTPException(status_code=409, detail="This owner is set by the deployment (PLATFORM_ADMIN_USER_IDS) and can't be changed here.")
    if user_id == staff.user_id:
        raise HTTPException(status_code=409, detail="You can't change your own place on the staff: ask another owner.")
    row = await db.get(PlatformStaff, user_id)
    if row is None:
        raise HTTPException(status_code=404, detail="They're not on the platform staff.")
    return row


@router.get("", dependencies=[Depends(require_staff())])
async def list_staff(db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    rows = (await db.execute(select(PlatformStaff, User).join(User, User.id == PlatformStaff.user_id).order_by(PlatformStaff.created_at))).all()
    configured = sorted(settings.platform_admin_ids - {r.user_id for r, _ in rows})
    users = {u.id: u for u in (await db.execute(select(User).where(User.id.in_(configured)))).scalars()} if configured else {}
    items = [_member(users.get(uid), uid, "owner", from_config=True) for uid in configured]
    items += [_member(u, r.user_id, r.role, from_config=False, row=r) for r, u in rows]
    return {"items": items, "roles": PLATFORM_ROLES}


@router.post("", status_code=201)
async def add_staff(body: AddStaff, staff: Staff = Depends(require_staff("staff.manage", recent=True)),
                    db: AsyncSession = Depends(get_db_session)) -> StaffMember:
    user = (await db.execute(select(User).where(func.lower(User.email) == body.email.strip().lower()))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="No Syruss account has that email. They need to sign up first.")
    if user.status != "active":
        raise HTTPException(status_code=409, detail="That account is disabled.")
    if user.id in settings.platform_admin_ids or await db.get(PlatformStaff, user.id):
        raise HTTPException(status_code=409, detail="They're already on the platform staff.")
    row = PlatformStaff(user_id=user.id, role=body.role, added_by=staff.user_id)
    db.add(row)
    record(db, staff, "staff.added", "user", user.id, reason=body.reason, details={**_who(user), "role": body.role})
    await db.commit()
    return _member(user, user.id, body.role, from_config=False, row=row)


@router.patch("/{user_id}")
async def change_role(user_id: str, body: ChangeRole, staff: Staff = Depends(require_staff("staff.manage", recent=True)),
                      db: AsyncSession = Depends(get_db_session)) -> StaffMember:
    row = await _changeable(db, staff, user_id)
    if row.role == "owner" and body.role != "owner" and await _owners_left(db, user_id) == 0:
        raise HTTPException(status_code=409, detail="The platform needs at least one owner.")
    before, row.role = row.role, body.role
    user = await db.get(User, user_id)
    record(db, staff, "staff.role_changed", "user", user_id, reason=body.reason, details={**_who(user), "from": before, "to": body.role})
    await db.commit()
    return _member(user, user_id, row.role, from_config=False, row=row)


@router.delete("/{user_id}", status_code=204)
async def remove_staff(user_id: str, reason: Optional[str] = None, staff: Staff = Depends(require_staff("staff.manage", recent=True)),
                       db: AsyncSession = Depends(get_db_session)) -> None:
    row = await _changeable(db, staff, user_id)
    if row.role == "owner" and await _owners_left(db, user_id) == 0:
        raise HTTPException(status_code=409, detail="The platform needs at least one owner.")
    record(db, staff, "staff.removed", "user", user_id, reason=reason, details={**_who(await db.get(User, user_id)), "role": row.role})
    await db.delete(row)
    await db.commit()
