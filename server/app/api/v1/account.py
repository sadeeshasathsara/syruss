"""
The signed-in person: their account, what they belong to, the invitations they accept, and their
notifications. None of this is tied to one workspace.
"""
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.core import ratelimit
from app.core.auth import AuthContext, Identity, get_current_auth, get_identity
from app.core.config import settings
from app.core.database import get_db_session
from app.models.audit import AuditLog
from app.models.identity import OrgMember, User, WorkspaceRole
from app.models.notification import Notification
from app.models.template import DocumentTemplate
from app.models.tenant import Tenant
from app.models.workspace import Workspace
from app.models.workspace_admin import WorkspaceInvitation, WorkspaceMember
from app.services import email_verification, identity_service, invitation_mail, workspace_events
from app.services.notifications import ACCOUNT

router = APIRouter(tags=["Account"])

NOTIFICATION_RETENTION_DAYS = 90


def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown"


# ---------------------------------------------------------------------------
# Account
# ---------------------------------------------------------------------------

@router.get("/me/context")
async def me_context(identity: Identity = Depends(get_identity), db: AsyncSession = Depends(get_db_session)):
    """
    Everything a frontend gates on: who you are, your organizations with your role in each, and your
    workspaces with your role and permissions. Needs a sign-in, not an organization (a new person has none yet).
    """
    context = await identity_service.context_for(db, identity)
    await db.commit()
    return context


@router.get("/me/email-verification")
async def email_verification_status(identity: Identity = Depends(get_identity), db: AsyncSession = Depends(get_db_session)):
    """Whether this sign-in's email address is confirmed, and whether a code is waiting to be typed."""
    return await email_verification.status(db, identity)


@router.post("/me/email-verification/send")
async def email_verification_send(identity: Identity = Depends(get_identity), db: AsyncSession = Depends(get_db_session)):
    """Emails a one-time code to the address this sign-in belongs to."""
    return await email_verification.send_code(db, identity)


class CodeIn(BaseModel):
    code: str = Field(..., min_length=1, max_length=32)


@router.post("/me/email-verification/confirm")
async def email_verification_confirm(body: CodeIn, identity: Identity = Depends(get_identity), db: AsyncSession = Depends(get_db_session)):
    return await email_verification.confirm(db, identity, body.code)


class OnboardingIn(BaseModel):
    first_name: str = Field(..., min_length=1, max_length=100)
    last_name: str = Field(..., min_length=1, max_length=100)
    phone: Optional[str] = Field(None, max_length=32)
    timezone: Optional[str] = Field(None, max_length=64)


@router.post("/onboarding", status_code=201)
async def onboarding(body: OnboardingIn, request: Request, identity: Identity = Depends(get_identity), db: AsyncSession = Depends(get_db_session)):
    """Creates the person's Syruss account. Repeating it returns the same account. Organizations come next."""
    ratelimit.limit(f"onboarding:{identity.user_id}", 10, 60)
    return await identity_service.onboard(
        db, identity, first_name=body.first_name, last_name=body.last_name, phone=body.phone, timezone_name=body.timezone,
    )


def _me(auth: AuthContext, u: Optional[User]) -> dict:
    base = {"user_id": auth.user_id, "tenant_id": auth.tenant_id, "name": auth.username or auth.user_id, "email": auth.email,
            "org_role": auth.org_role, "org_permissions": auth.org_permissions, "org_roles": auth.org_roles,
            "is_platform_admin": auth.is_platform_admin}
    if u is None:
        return {**base, "first_name": None, "last_name": None, "phone": None, "job_title": None, "timezone": "UTC"}
    return {**base, **identity_service.user_dict(u), "user_id": u.id}


class ProfileIn(BaseModel):
    first_name: Optional[str] = Field(None, min_length=1, max_length=100)
    last_name: Optional[str] = Field(None, min_length=1, max_length=100)
    phone: Optional[str] = Field(None, max_length=32)
    job_title: Optional[str] = Field(None, max_length=255)
    timezone: Optional[str] = Field(None, max_length=64)


@router.get("/me")
async def me(auth: AuthContext = Depends(get_current_auth), db: AsyncSession = Depends(get_db_session)):
    return _me(auth, await db.get(User, auth.user_id))


@router.patch("/me")
async def update_me(body: ProfileIn, auth: AuthContext = Depends(get_current_auth), db: AsyncSession = Depends(get_db_session)):
    """Your profile. The email belongs to your sign-in (Keycloak) and can't be changed here."""
    user = await db.get(User, auth.user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Account not found.")
    if body.first_name is not None:
        user.first_name = body.first_name.strip()
    if body.last_name is not None:
        user.last_name = body.last_name.strip()
    if body.phone is not None:
        user.phone = identity_service.clean_phone(body.phone)
    if body.job_title is not None:
        user.job_title = body.job_title.strip() or None
    if body.timezone is not None:
        user.timezone = identity_service.valid_timezone(body.timezone.strip() or None)
    # Members lists show the name people chose.
    await db.execute(update(WorkspaceMember).where(WorkspaceMember.user_id == user.id).values(name=user.display_name))
    await db.commit()
    return _me(auth.model_copy(update={"username": user.display_name}), user)


@router.post("/auth/logout", status_code=204)
async def logout(_: Identity = Depends(get_identity)):
    """The session lives in the web app's server and in Keycloak; ending it is their job. Nothing is kept here."""
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Workspace invitations (by link)
# ---------------------------------------------------------------------------

async def _invitation(db: AsyncSession, token: str) -> WorkspaceInvitation:
    invitation = (await db.execute(select(WorkspaceInvitation).where(
        WorkspaceInvitation.token_hash == hashlib.sha256(token.encode()).hexdigest(),
    ))).scalar_one_or_none()
    if invitation is None:
        raise HTTPException(status_code=404, detail="This invite link isn't valid. Ask for a new one.")
    return invitation


async def _invite_email_ok(db: AsyncSession, invited: str, identity: Identity) -> None:
    """The link belongs to the address it was made for, and that address must be proven."""
    if (identity.email or "").lower() != invited.lower():
        raise HTTPException(status_code=403, detail="This invitation was sent to a different email address.")
    if settings.INVITE_REQUIRE_VERIFIED_EMAIL and not await email_verification.is_verified(db, identity):
        raise HTTPException(status_code=403, detail="Verify your email address before accepting this invitation.")


@router.get("/invitations/{token}")
async def view_invitation(token: str, request: Request, db: AsyncSession = Depends(get_db_session)):
    """What an invite link is for, shown before signing in. The unguessable token is the key; only the minimum is revealed."""
    ratelimit.limit(f"invite-view:{_client(request)}", 30, 60)
    i = await _invitation(db, token)
    workspace = await db.get(Workspace, i.template_id)
    role = await db.get(WorkspaceRole, i.role)
    tenant = await db.get(Tenant, i.tenant_id)
    expired = i.expires_at < datetime.now(timezone.utc)
    return {"kind": "workspace", "organization": {"name": tenant.name if tenant else "", "slug": tenant.slug if tenant else ""},
            "workspace": {"id": i.template_id, "name": workspace.name if workspace else "", "slug": workspace.slug if workspace else ""},
            "email": i.email, "role": i.role, "role_label": role.name if role else i.role, "invited_by": i.invited_by,
            "status": "EXPIRED" if i.status == "PENDING" and expired else i.status, "expires_at": i.expires_at}


@router.post("/invitations/{token}/accept")
async def accept_invitation(token: str, db: AsyncSession = Depends(get_db_session), identity: Identity = Depends(get_identity)):
    """Joins the workspace (and its organization, as a member) with the invited role."""
    user = await db.get(User, identity.user_id)
    if user is None:
        raise HTTPException(status_code=403, detail={"code": "onboarding_required", "message": "Finish setting up your account first."})
    i = await _invitation(db, token)
    if i.status != "PENDING" or i.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=409, detail="This invitation was already used, revoked or has expired.")
    await _invite_email_ok(db, i.email, identity)
    template = await db.get(DocumentTemplate, i.template_id)
    if not template or template.tenant_id != i.tenant_id:
        raise HTTPException(status_code=404, detail="The workspace no longer exists.")
    if (await db.get(WorkspaceRole, i.role)) is None:
        raise HTTPException(status_code=409, detail="The role this invitation was for no longer exists. Ask for a new invitation.")
    in_org = (await db.execute(select(OrgMember.id).where(OrgMember.tenant_id == i.tenant_id, OrgMember.user_id == user.id))).first()
    if not in_org:
        db.add(OrgMember(tenant_id=i.tenant_id, user_id=user.id, role="member", invited_by=i.invited_by))
    member = (await db.execute(select(WorkspaceMember).where(
        WorkspaceMember.template_id == i.template_id, WorkspaceMember.user_id == user.id,
    ))).scalar_one_or_none()
    if member is None:
        member = WorkspaceMember(
            tenant_id=i.tenant_id, template_id=i.template_id, user_id=user.id, role=i.role,
            name=user.display_name, email=user.email, invited_by=i.invited_by,
        )
        db.add(member)
    i.status, i.accepted_by = "ACCEPTED", user.id
    await invitation_mail.withdraw_notification(db, i)
    db.add(AuditLog(tenant_id=i.tenant_id, template_id=i.template_id, event_type="INVITATION_ACCEPTED", entity_type="WorkspaceInvitation",
                    entity_id=i.id, actor=user.id, details={"role": i.role}))
    role = await db.get(WorkspaceRole, i.role)
    await workspace_events.emit(
        db, tenant_id=i.tenant_id, template_id=i.template_id, type="member.joined",
        title=f"{member.name} joined as {role.name.lower()}", path="settings/members/people",
        severity="success", exclude_user=user.id,
    )
    await db.commit()
    await workspace_events.dispatch(db)
    workspace = await db.get(Workspace, i.template_id)
    tenant = await db.get(Tenant, i.tenant_id)
    return {"organization": {"slug": tenant.slug if tenant else ""}, "workspace": {"id": i.template_id, "slug": workspace.slug if workspace else ""},
            "role": member.role}


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

def _mine(auth: AuthContext):
    """Your notifications here, plus the ones that follow you across organizations (an invitation to a new one)."""
    return (Notification.user_id == auth.user_id, Notification.tenant_id.in_([auth.tenant_id, ACCOUNT]))


@router.get("/notifications")
async def list_notifications(
    unread_only: bool = False,
    limit: int = Query(30, ge=1, le=100),
    before: Optional[datetime] = None,
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db_session),
):
    """Newest first; `before` pages back. Notifications older than the retention window are removed."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=NOTIFICATION_RETENTION_DAYS)
    await db.execute(delete(Notification).where(*_mine(auth), Notification.created_at < cutoff))
    stmt = select(Notification).where(*_mine(auth))
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    if before:
        stmt = stmt.where(Notification.created_at < before)
    rows = (await db.execute(stmt.order_by(Notification.created_at.desc()).limit(limit + 1))).scalars().all()
    unread = (await db.execute(select(func.count(Notification.id)).where(*_mine(auth), Notification.read_at.is_(None)))).scalar() or 0
    await db.commit()
    return {"items": [workspace_events.notification_dict(n) for n in rows[:limit]], "unread": unread, "has_more": len(rows) > limit}


async def _notification(db: AsyncSession, auth: AuthContext, notification_id: str) -> Notification:
    n = await db.get(Notification, notification_id)
    if not n or n.user_id != auth.user_id or n.tenant_id not in (auth.tenant_id, ACCOUNT):
        raise HTTPException(status_code=404, detail="Notification not found.")
    return n


class ReadIn(BaseModel):
    read: bool = True


@router.post("/notifications/{notification_id}/read")
async def mark_read(notification_id: str, body: ReadIn = ReadIn(), auth: AuthContext = Depends(get_current_auth), db: AsyncSession = Depends(get_db_session)):
    n = await _notification(db, auth, notification_id)
    n.read_at = (n.read_at or datetime.now(timezone.utc)) if body.read else None
    await db.commit()
    return workspace_events.notification_dict(n)


@router.post("/notifications/read-all", status_code=204)
async def mark_all_read(auth: AuthContext = Depends(get_current_auth), db: AsyncSession = Depends(get_db_session)):
    await db.execute(update(Notification).where(*_mine(auth), Notification.read_at.is_(None)).values(read_at=datetime.now(timezone.utc)))
    await db.commit()


@router.delete("/notifications/{notification_id}", status_code=204)
async def delete_notification(notification_id: str, auth: AuthContext = Depends(get_current_auth), db: AsyncSession = Depends(get_db_session)):
    await db.delete(await _notification(db, auth, notification_id))
    await db.commit()


@router.delete("/notifications", status_code=204)
async def clear_notifications(
    which: Literal["all", "read"] = "all", auth: AuthContext = Depends(get_current_auth), db: AsyncSession = Depends(get_db_session),
):
    """Clears every notification, or only those already read."""
    stmt = delete(Notification).where(*_mine(auth))
    if which == "read":
        stmt = stmt.where(Notification.read_at.is_not(None))
    await db.execute(stmt)
    await db.commit()
