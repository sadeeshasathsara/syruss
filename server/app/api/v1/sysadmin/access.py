"""
Who may use the platform tools, and the record of what they did.

Checks, all on the server: the token was issued to the system admin app, its sign-in included a one-time code and is
no older than a working day (STAFF_SESSION_HOURS), the person is on the platform staff, and their role grants the
permission. Changes that would hurt in the wrong hands also need a sign-in from the last few minutes, and some a second
owner's approval (approvals.py). Every change is written to the platform audit log, which only grows.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional
from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.auth import get_identity
from app.core.auth.context import Identity
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.platform_permissions import PLATFORM_GRANTS
from app.models.identity import User
from app.models.platform import PlatformAuditEntry, PlatformStaff


class Staff(BaseModel):
    user_id: str
    name: str
    email: Optional[str] = None
    role: str
    permissions: List[str]
    # Owners named in PLATFORM_ADMIN_USER_IDS: their role is set by the deployment, not in the app
    from_config: bool = False
    auth_time: Optional[int] = None
    ip: Optional[str] = None

    def can(self, permission: str) -> bool:
        return permission in self.permissions


def _forbidden(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=403, detail={"code": code, "message": message})


def _ip(request: Request) -> Optional[str]:
    """The address the admin app reports for the browser (it forwards it), else the caller's own: for the record only."""
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[-1].strip()
    ip = forwarded or (request.client.host if request.client else "")
    return ip[:64] or None


async def staff_role(db: AsyncSession, user_id: str) -> tuple:
    """(role, from_config) for someone on the platform staff, or (None, False)."""
    from app.services.staff_service import staff_role as role_of
    return await role_of(db, user_id)


ACTIVE_EVERY = timedelta(minutes=5)  # how often a staff member's last use of the admin app is written down


def check_sign_in(identity: Identity) -> None:
    """The sign-in itself must be strong and recent enough for the platform tools, whoever it is."""
    if settings.STAFF_REQUIRE_TWO_STEP and "otp" not in identity.amr:
        raise _forbidden("two_step_required", "Platform tools need a sign-in with a one-time code. Sign in again with your code. (If you "
                                              "did use one, the sign-in server doesn't report it yet: run bridge/keycloak/harden.py.)")
    age = datetime.now(timezone.utc).timestamp() - (identity.auth_time or 0)
    if age > settings.STAFF_SESSION_HOURS * 3600:
        raise HTTPException(status_code=401, detail={
            "code": "session_expired", "message": f"Staff sessions last {settings.STAFF_SESSION_HOURS} hours. Sign in again."})


async def current_staff(request: Request, identity: Identity = Depends(get_identity)) -> Staff:
    if settings.OIDC_ADMIN_CLIENT and identity.client_id != settings.OIDC_ADMIN_CLIENT:
        raise _forbidden("admin_app_required", "Platform tools open only from the system admin app.")
    check_sign_in(identity)
    async with AsyncSessionLocal() as db:
        role, from_config = await staff_role(db, identity.user_id)
        user = await db.get(User, identity.user_id)
        if role is not None and not from_config:  # used now: the offboarding clock starts again
            now = datetime.now(timezone.utc)
            await db.execute(update(PlatformStaff).where(
                PlatformStaff.user_id == identity.user_id,
                (PlatformStaff.last_active_at.is_(None)) | (PlatformStaff.last_active_at < now - ACTIVE_EVERY),
            ).values(last_active_at=now, offboard_warned_at=None, updated_at=PlatformStaff.updated_at))
            await db.commit()
    if role is None:
        raise _forbidden("platform_staff_required", "You're not on the platform staff.")
    if user is not None and user.status != "active":
        raise _forbidden("account_disabled", "This account is disabled.")
    return Staff(
        user_id=identity.user_id, name=(user.display_name if user else None) or identity.username or identity.user_id,
        email=user.email if user else identity.email, role=role, permissions=sorted(PLATFORM_GRANTS[role]),
        from_config=from_config, auth_time=identity.auth_time, ip=_ip(request),
    )


async def staff_for(db: AsyncSession, user_id: str) -> Optional[Staff]:
    """Someone on the staff as they are now (to carry out what they asked, once approved); None if they no longer are."""
    role, from_config = await staff_role(db, user_id)
    user = await db.get(User, user_id)
    if role is None or (user is not None and user.status != "active"):
        return None
    return Staff(user_id=user_id, name=(user.display_name if user else None) or user_id, email=user.email if user else None, role=role,
                 permissions=sorted(PLATFORM_GRANTS[role]), from_config=from_config)


def require_staff(permission: str = "platform.view", *, recent: bool = False) -> Callable:
    """The caller must be platform staff whose role grants `permission`; `recent` also asks for a fresh sign-in."""
    async def dependency(staff: Staff = Depends(current_staff)) -> Staff:
        if not staff.can(permission):
            raise _forbidden("platform_permission_denied", "Your platform role doesn't allow this.")
        if recent and settings.REQUIRE_RECENT_LOGIN:
            age = datetime.now(timezone.utc).timestamp() - (staff.auth_time or 0)
            if age > settings.RECENT_LOGIN_MINUTES * 60:
                raise _forbidden("reauth_required", "Sign in again to do this. Platform changes need a fresh sign-in.")
        return staff
    return dependency


def record(db: AsyncSession, staff: Staff, action: str, target_type: str, target_id: str, *,
           tenant_id: Optional[str] = None, reason: Optional[str] = None, details: Optional[Dict[str, Any]] = None) -> None:
    """Writes one staff action to the platform audit log, in the caller's transaction (it commits with the change)."""
    db.add(PlatformAuditEntry(
        actor_id=staff.user_id, actor_name=staff.name, action=action, target_type=target_type, target_id=target_id,
        tenant_id=tenant_id, reason=(reason or "").strip() or None, details=details or {}, ip=staff.ip,
    ))
