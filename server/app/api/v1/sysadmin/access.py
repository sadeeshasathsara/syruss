"""
Who may use the platform tools, and the record of what they did.

Three checks, all on the server: the token was issued to the system admin app (which signs in with a second factor),
the person is on the platform staff, and their role grants the permission. Changes that would hurt in the wrong hands
also need a sign-in from the last few minutes. Every change is written to the platform audit log, which only grows.
"""
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel
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
    if user_id in settings.platform_admin_ids:
        return "owner", True
    row = await db.get(PlatformStaff, user_id)
    return (row.role, False) if row else (None, False)


async def current_staff(request: Request, identity: Identity = Depends(get_identity)) -> Staff:
    if settings.OIDC_ADMIN_CLIENT and identity.client_id != settings.OIDC_ADMIN_CLIENT:
        raise _forbidden("admin_app_required", "Platform tools open only from the system admin app.")
    async with AsyncSessionLocal() as db:
        role, from_config = await staff_role(db, identity.user_id)
        user = await db.get(User, identity.user_id)
    if role is None:
        raise _forbidden("platform_staff_required", "You're not on the platform staff.")
    if user is not None and user.status != "active":
        raise _forbidden("account_disabled", "This account is disabled.")
    return Staff(
        user_id=identity.user_id, name=(user.display_name if user else None) or identity.username or identity.user_id,
        email=user.email if user else identity.email, role=role, permissions=sorted(PLATFORM_GRANTS[role]),
        from_config=from_config, auth_time=identity.auth_time, ip=_ip(request),
    )


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
