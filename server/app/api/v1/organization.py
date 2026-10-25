"""
The organization: its people and invitations. Organization roles decide who runs the organization;
they never open a workspace's data (that takes a workspace role, see api/v1/settings.py).
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core import ratelimit
from app.core.auth import AuthContext, Identity, get_current_auth, get_identity, require_org_permission, require_recent_login
from app.core.config import settings
from app.core.database import get_db_session
from app.core.org_permissions import ORG_GRANTS, ORG_PERMISSIONS, ORG_ROLE_DESCRIPTIONS, ORG_ROLES
from app.models.audit import AuditLog
from app.models.identity import OrgInvitation, OrgMember, User
from app.models.tenant import Tenant
from app.models.workspace_admin import WorkspaceMember
from app.services import email_verification, identity_service, invitation_mail

router = APIRouter(tags=["Organization"])

OrgRole = Literal["owner", "admin", "member"]


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def _members(db: AsyncSession, tenant_id: str) -> List[tuple]:
    return list((await db.execute(
        select(OrgMember, User).join(User, User.id == OrgMember.user_id).where(OrgMember.tenant_id == tenant_id).order_by(OrgMember.created_at)
    )).all())


def _member_dict(m: OrgMember, u: User, you: str) -> Dict[str, Any]:
    return {"user_id": u.id, "name": u.display_name, "email": u.email, "role": m.role, "role_label": ORG_ROLES[m.role],
            "created_at": m.created_at, "is_you": u.id == you}


class OrgPatch(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)


class OrgCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    slug: Optional[str] = Field(None, max_length=48)


@router.post("/organizations", status_code=201)
async def create_organization(body: OrgCreate, db: AsyncSession = Depends(get_db_session), identity: Identity = Depends(get_identity)):
    """Creates an organization you own. Needs an account, not an organization (you don't have one yet)."""
    ratelimit.limit(f"org-create:{identity.user_id}", 10, 60)
    return await identity_service.create_organization(db, identity, name=body.name, slug=body.slug)


@router.get("/organizations/slug-check")
async def slug_check(slug: str, db: AsyncSession = Depends(get_db_session), identity: Identity = Depends(get_identity)):
    """Whether an address is free, so the form can say so before it is submitted."""
    ratelimit.limit(f"slug-check:{identity.user_id}", 60, 60)
    try:
        cleaned = identity_service.clean_slug(slug)
    except HTTPException as exc:
        return {"slug": slug, "available": False, "reason": exc.detail}
    free = await identity_service.slug_is_free(db, cleaned)
    return {"slug": cleaned, "available": free, "reason": None if free else "That address is taken."}


@router.patch("/organization")
async def rename_organization(body: OrgPatch, db: AsyncSession = Depends(get_db_session), auth: AuthContext = Depends(require_org_permission("org.manage"))):
    tenant = await db.get(Tenant, auth.tenant_id)
    before, tenant.name = tenant.name, body.name.strip()
    if before != tenant.name:
        db.add(AuditLog(tenant_id=tenant.id, event_type="ORG_RENAMED", entity_type="Organization", entity_id=tenant.id, actor=auth.user_id,
                        details={"from": before, "to": tenant.name}))
    await db.commit()
    return {"id": tenant.id, "name": tenant.name, "slug": tenant.slug}


@router.get("/organization/roles")
async def organization_roles(auth: AuthContext = Depends(get_current_auth)):
    return {
        "roles": [{"key": k, "label": v, "description": ORG_ROLE_DESCRIPTIONS[k], "permissions": sorted(ORG_GRANTS[k])} for k, v in ORG_ROLES.items()],
        "permissions": [{"key": k, "label": v} for k, v in ORG_PERMISSIONS.items()],
        "you": {"role": auth.org_role, "permissions": auth.org_permissions},
    }


@router.get("/organization/members")
async def list_org_members(db: AsyncSession = Depends(get_db_session), auth: AuthContext = Depends(get_current_auth)):
    return [_member_dict(m, u, auth.user_id) for m, u in await _members(db, auth.tenant_id)]


class OrgRoleChange(BaseModel):
    role: OrgRole


def _guard_owners(members: List[tuple], target: OrgMember, new_role: Optional[str]) -> None:
    if target.role == "owner" and new_role != "owner" and sum(1 for m, _ in members if m.role == "owner") == 1:
        raise HTTPException(status_code=409, detail="An organization needs at least one owner. Make someone else an owner first.")


async def _member(db: AsyncSession, tenant_id: str, user_id: str) -> OrgMember:
    m = (await db.execute(select(OrgMember).where(OrgMember.tenant_id == tenant_id, OrgMember.user_id == user_id))).scalar_one_or_none()
    if not m:
        raise HTTPException(status_code=404, detail="Member not found in this organization.")
    return m


@router.patch("/organization/members/{user_id}", dependencies=[Depends(require_recent_login)])
async def change_org_role(user_id: str, body: OrgRoleChange, db: AsyncSession = Depends(get_db_session),
                          auth: AuthContext = Depends(require_org_permission("org.members.manage"))):
    target = await _member(db, auth.tenant_id, user_id)
    if (body.role == "owner" or target.role == "owner") and auth.org_role != "owner":
        raise HTTPException(status_code=403, detail="Only an owner can grant or remove the owner role.")
    _guard_owners(await _members(db, auth.tenant_id), target, body.role)
    db.add(AuditLog(tenant_id=auth.tenant_id, event_type="ORG_ROLE_CHANGED", entity_type="OrgMember", entity_id=target.id,
                    actor=auth.user_id, details={"user": user_id, "from": target.role, "to": body.role}))
    target.role = body.role
    await db.commit()
    user = await db.get(User, user_id)
    return _member_dict(target, user, auth.user_id)


@router.delete("/organization/members/{user_id}", status_code=204, dependencies=[Depends(require_recent_login)])
async def remove_org_member(user_id: str, db: AsyncSession = Depends(get_db_session), auth: AuthContext = Depends(get_current_auth)):
    """Removes someone from the organization and from all its workspaces, or lets you leave."""
    target = await _member(db, auth.tenant_id, user_id)
    if user_id != auth.user_id and not auth.can_org("org.members.manage"):
        raise HTTPException(status_code=403, detail="Your organization role can't remove other members.")
    if target.role == "owner" and auth.org_role != "owner":
        raise HTTPException(status_code=403, detail="Only an owner can remove an owner.")
    _guard_owners(await _members(db, auth.tenant_id), target, None)
    await db.execute(delete(WorkspaceMember).where(WorkspaceMember.tenant_id == auth.tenant_id, WorkspaceMember.user_id == user_id))
    db.add(AuditLog(tenant_id=auth.tenant_id, event_type="ORG_MEMBER_REMOVED", entity_type="OrgMember", entity_id=target.id,
                    actor=auth.user_id, details={"user": user_id}))
    await db.delete(target)
    await db.commit()


# ---------------------------------------------------------------------------
# Invitations (emailed, and the link is returned too)
# ---------------------------------------------------------------------------

class OrgInviteIn(BaseModel):
    email: str = Field(..., max_length=255, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    role: Literal["admin", "member"] = "member"
    note: Optional[str] = Field(None, max_length=500, description="A personal line shown in the email")
    send_email: bool = True


def _invitation_dict(i: OrgInvitation) -> Dict[str, Any]:
    expired = i.status == "PENDING" and i.expires_at < datetime.now(timezone.utc)
    return {"id": i.id, "email": i.email, "role": i.role, "role_label": ORG_ROLES[i.role], "status": "EXPIRED" if expired else i.status,
            "invited_by": i.invited_by, "expires_at": i.expires_at, "created_at": i.created_at,
            "email_status": invitation_mail.email_state(i), "email_sent_at": i.email_sent_at, "email_error": i.email_error, "note": i.note}


def _issued(i: OrgInvitation, token: str) -> Dict[str, Any]:
    return {"invitation": _invitation_dict(i), "token": token, "link": invitation_mail.invite_url(token)}


def _new_token(i: OrgInvitation) -> str:
    token = secrets.token_urlsafe(24)
    i.token_hash = _hash(token)
    i.expires_at = datetime.now(timezone.utc) + timedelta(days=settings.INVITE_DAYS)
    return token


@router.get("/organization/invitations")
async def list_org_invitations(db: AsyncSession = Depends(get_db_session), auth: AuthContext = Depends(require_org_permission("org.members.manage"))):
    rows = (await db.execute(select(OrgInvitation).where(OrgInvitation.tenant_id == auth.tenant_id, OrgInvitation.status == "PENDING")
                             .order_by(OrgInvitation.created_at.desc()))).scalars().all()
    return [_invitation_dict(i) for i in rows]


@router.post("/organization/invitations", status_code=201)
async def invite_to_organization(body: OrgInviteIn, background: BackgroundTasks, db: AsyncSession = Depends(get_db_session),
                                 auth: AuthContext = Depends(require_org_permission("org.members.manage"))):
    ratelimit.limit(f"org-invite:{auth.tenant_id}", 60, 3600)
    email = body.email.strip().lower()
    members = await _members(db, auth.tenant_id)
    if any(u.email.lower() == email for _, u in members):
        raise HTTPException(status_code=409, detail=f"{email} is already a member.")
    pending = (await db.execute(select(OrgInvitation).where(
        OrgInvitation.tenant_id == auth.tenant_id, OrgInvitation.email == email, OrgInvitation.status == "PENDING",
        OrgInvitation.expires_at > datetime.now(timezone.utc)))).scalar_one_or_none()
    if pending is not None:
        raise HTTPException(status_code=409, detail=f"{email} already has a pending invitation. Resend it, or revoke it first to change the role.")
    invitation = OrgInvitation(tenant_id=auth.tenant_id, email=email, role=body.role, token_hash="", expires_at=datetime.now(timezone.utc),
                               invited_by=await invitation_mail.inviter_name(db, auth), note=(body.note or "").strip() or None)
    token = _new_token(invitation)
    if body.send_email:
        invitation_mail.mark_sending(invitation)
    db.add(invitation)
    await db.flush()
    db.add(AuditLog(tenant_id=auth.tenant_id, event_type="ORG_INVITATION_SENT", entity_type="OrgInvitation", entity_id=invitation.id,
                    actor=auth.user_id, details={"email": email, "role": body.role}))
    await db.commit()
    background.add_task(invitation_mail.deliver_invitation, "organization", invitation.id, token, body.send_email)
    return _issued(invitation, token)


async def _own_invitation(db: AsyncSession, auth: AuthContext, invitation_id: str) -> OrgInvitation:
    i = await db.get(OrgInvitation, invitation_id)
    if not i or i.tenant_id != auth.tenant_id:
        raise HTTPException(status_code=404, detail="Invitation not found.")
    return i


@router.post("/organization/invitations/{invitation_id}/renew")
async def renew_org_invitation(invitation_id: str, background: BackgroundTasks, db: AsyncSession = Depends(get_db_session),
                               auth: AuthContext = Depends(require_org_permission("org.members.manage"))):
    i = await _own_invitation(db, auth, invitation_id)
    if i.status != "PENDING":
        raise HTTPException(status_code=409, detail="This invitation was already used or revoked.")
    token = _new_token(i)
    await db.commit()
    background.add_task(invitation_mail.deliver_invitation, "organization", i.id, token, False)
    return _issued(i, token)


@router.post("/organization/invitations/{invitation_id}/resend")
async def resend_org_invitation(invitation_id: str, background: BackgroundTasks, db: AsyncSession = Depends(get_db_session),
                                auth: AuthContext = Depends(require_org_permission("org.members.manage"))):
    """Emails the invitation again with a fresh link (the old one stops working) and a fresh week."""
    i = await _own_invitation(db, auth, invitation_id)
    if i.status != "PENDING":
        raise HTTPException(status_code=409, detail="This invitation was already used or revoked.")
    invitation_mail.check_can_resend(i)
    token = _new_token(i)
    invitation_mail.mark_sending(i)
    await db.commit()
    background.add_task(invitation_mail.deliver_invitation, "organization", i.id, token, True)
    return _issued(i, token)


@router.delete("/organization/invitations/{invitation_id}", status_code=204)
async def revoke_org_invitation(invitation_id: str, db: AsyncSession = Depends(get_db_session),
                                auth: AuthContext = Depends(require_org_permission("org.members.manage"))):
    i = await _own_invitation(db, auth, invitation_id)
    i.status = "REVOKED"
    await invitation_mail.withdraw_notification(db, i)
    db.add(AuditLog(tenant_id=auth.tenant_id, event_type="ORG_INVITATION_REVOKED", entity_type="OrgInvitation", entity_id=i.id,
                    actor=auth.user_id, details={"email": i.email}))
    await db.commit()


async def _by_token(db: AsyncSession, token: str) -> OrgInvitation:
    i = (await db.execute(select(OrgInvitation).where(OrgInvitation.token_hash == _hash(token)))).scalar_one_or_none()
    if i is None:
        raise HTTPException(status_code=404, detail="This invite link isn't valid. Ask for a new one.")
    return i


@router.get("/org-invitations/{token}")
async def view_org_invitation(token: str, request: Request, db: AsyncSession = Depends(get_db_session)):
    """What an organization invite link is for, shown before signing in."""
    ratelimit.limit(f"org-invite-view:{request.client.host if request.client else 'x'}", 30, 60)
    i = await _by_token(db, token)
    tenant = await db.get(Tenant, i.tenant_id)
    expired = i.expires_at < datetime.now(timezone.utc)
    return {"kind": "organization", "organization": {"name": tenant.name if tenant else "", "slug": tenant.slug if tenant else ""},
            "email": i.email, "role": i.role, "role_label": ORG_ROLES[i.role], "invited_by": i.invited_by,
            "status": "EXPIRED" if i.status == "PENDING" and expired else i.status, "expires_at": i.expires_at}


@router.post("/org-invitations/{token}/accept")
async def accept_org_invitation(token: str, db: AsyncSession = Depends(get_db_session), identity: Identity = Depends(get_identity)):
    user = await db.get(User, identity.user_id)
    if user is None:
        raise HTTPException(status_code=403, detail={"code": "onboarding_required", "message": "Finish setting up your account first."})
    i = await _by_token(db, token)
    if i.status != "PENDING" or i.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=409, detail="This invitation was already used, revoked or has expired.")
    if (identity.email or "").lower() != i.email.lower():
        raise HTTPException(status_code=403, detail="This invitation was sent to a different email address.")
    if settings.INVITE_REQUIRE_VERIFIED_EMAIL and not await email_verification.is_verified(db, identity):
        raise HTTPException(status_code=403, detail="Verify your email address before accepting this invitation.")
    tenant = await db.get(Tenant, i.tenant_id)
    if not tenant or not tenant.is_active:
        raise HTTPException(status_code=404, detail="The organization no longer exists.")
    if not (await db.execute(select(OrgMember.id).where(OrgMember.tenant_id == i.tenant_id, OrgMember.user_id == user.id))).first():
        db.add(OrgMember(tenant_id=i.tenant_id, user_id=user.id, role=i.role, invited_by=i.invited_by))
    i.status, i.accepted_by = "ACCEPTED", user.id
    await invitation_mail.withdraw_notification(db, i)
    db.add(AuditLog(tenant_id=i.tenant_id, event_type="ORG_INVITATION_ACCEPTED", entity_type="OrgInvitation", entity_id=i.id,
                    actor=user.id, details={"role": i.role}))
    await db.commit()
    return {"organization": {"slug": tenant.slug, "name": tenant.name}, "role": i.role}
