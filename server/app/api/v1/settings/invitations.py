"""Invitations: asking someone into the workspace with a role, renewing or resending the link, and revoking it."""
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.v1.settings import common
from app.core import ratelimit
from app.core.database import get_db_session
from app.core.permissions import Access, require
from app.core.secrets import token_hash
from app.models.workspace_admin import WorkspaceInvitation
from app.services import invitation_mail
from app.services.billing import service as billing

router = APIRouter()

INVITE_DAYS = 7


class InviteIn(BaseModel):
    email: str = Field(..., max_length=255, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    role: str = "reviewer"
    note: Optional[str] = Field(None, max_length=500, description="A personal line shown in the email")
    send_email: bool = True


def _invitation_dict(i: WorkspaceInvitation, role_names: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    expired = i.status == "PENDING" and i.expires_at < datetime.now(timezone.utc)
    return {"id": i.id, "email": i.email, "role": i.role, "role_label": (role_names or {}).get(i.role, i.role),
            "status": "EXPIRED" if expired else i.status, "invited_by": i.invited_by, "expires_at": i.expires_at, "created_at": i.created_at,
            "email_status": invitation_mail.email_state(i), "email_sent_at": i.email_sent_at, "email_error": i.email_error, "note": i.note}


def _issued(i: WorkspaceInvitation, token: str, role_names: Dict[str, str]) -> Dict[str, Any]:
    """What the inviter gets back: the invitation, and the link (also emailed) in case they'd rather pass it on themselves."""
    return {"invitation": _invitation_dict(i, role_names), "token": token, "link": invitation_mail.invite_url(token)}


@router.get("/invitations")
async def list_invitations(access: Access = Depends(require("members.manage")), db: AsyncSession = Depends(get_db_session)):
    rows = (await db.execute(select(WorkspaceInvitation).where(
        WorkspaceInvitation.template_id == access.scope.template_id, WorkspaceInvitation.status == "PENDING",
    ).order_by(WorkspaceInvitation.created_at.desc()))).scalars().all()
    names = await common.role_names(db, access.scope.template_id)
    return [_invitation_dict(i, names) for i in rows]


def _new_token(invitation: WorkspaceInvitation) -> str:
    token = secrets.token_urlsafe(24)
    invitation.token_hash = token_hash(token)
    invitation.expires_at = datetime.now(timezone.utc) + timedelta(days=INVITE_DAYS)
    return token


@router.post("/invitations", status_code=201)
async def invite(body: InviteIn, background: BackgroundTasks, access: Access = Depends(require("members.manage")),
                 db: AsyncSession = Depends(get_db_session)):
    """
    Invites an email address to this workspace with a role. The invitation is emailed (after this answers) and the
    link is returned too. A second invitation to the same address is refused: resend the pending one instead.
    """
    ratelimit.limit(f"invite:{access.scope.template_id}", 60, 3600)
    role = await common.assignable(db, access, body.role)
    email = body.email.strip().lower()
    members = await common.members(db, access.scope.template_id)
    if any((m.email or "").lower() == email for m in members):
        raise HTTPException(status_code=409, detail=f"{email} is already a member.")
    pending = (await db.execute(select(WorkspaceInvitation).where(
        WorkspaceInvitation.template_id == access.scope.template_id, WorkspaceInvitation.email == email,
        WorkspaceInvitation.status == "PENDING", WorkspaceInvitation.expires_at > datetime.now(timezone.utc)))).scalar_one_or_none()
    if pending is not None:
        raise HTTPException(status_code=409, detail=f"{email} already has a pending invitation. Resend it, or revoke it first to change the role.")
    await billing.check_limit(db, "members_per_workspace", template_id=access.scope.template_id)
    invitation = WorkspaceInvitation(
        tenant_id=access.scope.tenant_id, template_id=access.scope.template_id, email=email,
        role=role.id, invited_by=await invitation_mail.inviter_name(db, access.auth), token_hash="",
        expires_at=datetime.now(timezone.utc), note=(body.note or "").strip() or None,
    )
    token = _new_token(invitation)
    if body.send_email:
        invitation_mail.mark_sending(invitation)
    db.add(invitation)
    await db.commit()
    background.add_task(invitation_mail.deliver_invitation, "workspace", invitation.id, token, body.send_email)
    return _issued(invitation, token, await common.role_names(db, access.scope.template_id))


async def _invitation(db: AsyncSession, access: Access, invitation_id: str) -> WorkspaceInvitation:
    i = await db.get(WorkspaceInvitation, invitation_id)
    if not i or i.template_id != access.scope.template_id:
        raise HTTPException(status_code=404, detail="Invitation not found.")
    return i


@router.post("/invitations/{invitation_id}/renew")
async def renew_invitation(invitation_id: str, background: BackgroundTasks, access: Access = Depends(require("members.manage")),
                           db: AsyncSession = Depends(get_db_session)):
    """A new link (the old one stops working) valid for another week. Not emailed; their bell, if they have one, gets the new link."""
    i = await _invitation(db, access, invitation_id)
    if i.status != "PENDING":
        raise HTTPException(status_code=409, detail="This invitation was already used or revoked.")
    token = _new_token(i)
    await db.commit()
    background.add_task(invitation_mail.deliver_invitation, "workspace", i.id, token, False)
    return _issued(i, token, await common.role_names(db, access.scope.template_id))


@router.post("/invitations/{invitation_id}/resend")
async def resend_invitation(invitation_id: str, background: BackgroundTasks, access: Access = Depends(require("members.manage")),
                            db: AsyncSession = Depends(get_db_session)):
    """Emails the invitation again with a fresh link (the old one stops working) and a fresh week."""
    i = await _invitation(db, access, invitation_id)
    if i.status != "PENDING":
        raise HTTPException(status_code=409, detail="This invitation was already used or revoked.")
    invitation_mail.check_can_resend(i)
    token = _new_token(i)
    invitation_mail.mark_sending(i)
    await db.commit()
    background.add_task(invitation_mail.deliver_invitation, "workspace", i.id, token, True)
    return _issued(i, token, await common.role_names(db, access.scope.template_id))


@router.delete("/invitations/{invitation_id}", status_code=204)
async def revoke_invitation(invitation_id: str, access: Access = Depends(require("members.manage")), db: AsyncSession = Depends(get_db_session)):
    i = await _invitation(db, access, invitation_id)
    i.status = "REVOKED"
    await invitation_mail.withdraw_notification(db, i)
    await db.commit()
