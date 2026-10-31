"""
Emailing invitations. The request that creates (or resends) an invitation answers straight away and marks the
invitation SENDING; this runs after the answer, sends the message and records how it went, so a slow or broken mail
server never holds up a person and the inviter can see "Sent" or "Couldn't send" and act on it.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional, Union
from fastapi import HTTPException
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.org_permissions import ORG_ROLES
from app.models.identity import OrgInvitation, User, WorkspaceRole
from app.models.template import DocumentTemplate
from app.models.tenant import Tenant
from app.models.workspace_admin import WorkspaceInvitation
from app.services import notifications
from app.services.notifications import INVITATION

logger = logging.getLogger(__name__)

Kind = Literal["workspace", "organization"]
Invitation = Union[WorkspaceInvitation, OrgInvitation]
STUCK_AFTER = timedelta(minutes=2)


def invite_url(token: str) -> str:
    return f"{settings.PUBLIC_SITE_URL.rstrip('/')}/invite/{token}"


async def inviter_name(db, auth) -> str:
    """How the invited person will see who invited them: the profile name, else the sign-in name."""
    user = await db.get(User, auth.user_id)
    return (user.display_name if user and user.display_name else None) or auth.username or auth.user_id


def email_state(i: Invitation) -> str:
    """What to show: a send that never reported back (the server restarted mid-send) counts as failed."""
    if i.email_status == "SENDING" and i.created_at and datetime.now(timezone.utc) - max(i.created_at, i.email_sent_at or i.created_at) > STUCK_AFTER:
        return "FAILED"
    return i.email_status


def check_can_resend(i: Invitation) -> None:
    sent = i.email_sent_at
    if sent and datetime.now(timezone.utc) - sent < timedelta(seconds=settings.MAIL_RESEND_SECONDS):
        wait = settings.MAIL_RESEND_SECONDS - int((datetime.now(timezone.utc) - sent).total_seconds())
        raise HTTPException(status_code=429, detail=f"Wait {wait} seconds before sending this again.", headers={"Retry-After": str(wait)})


def mark_sending(i: Invitation) -> None:
    i.email_status = "SENDING"
    i.email_error = None


async def withdraw_notification(db, i: Invitation) -> None:
    """The invitation is settled (accepted or revoked): its notification leaves the person's bell."""
    recipient = await notifications.recipient_for_email(db, i.email)
    if recipient.user_id:
        await notifications.retire_in_app(db, user_id=recipient.user_id, type="invitation.received", key="invitation_id", value=i.id)


async def deliver_invitation(kind: Kind, invitation_id: str, token: str, send_email: bool = True) -> None:
    """Background task: tells the invited person (email, and the bell if they have an account) and records how the email went."""
    model = WorkspaceInvitation if kind == "workspace" else OrgInvitation
    async with AsyncSessionLocal() as db:
        i = await db.get(model, invitation_id)
        if i is None or i.status != "PENDING":
            return
        try:
            tenant = await db.get(Tenant, i.tenant_id)
            workspace: Optional[str] = None
            role_label: str
            role_description: Optional[str] = None
            if kind == "workspace":
                template = await db.get(DocumentTemplate, i.template_id)
                workspace = template.name if template else "a workspace"
                role = await db.get(WorkspaceRole, i.role)
                role_label = role.name if role else i.role
                role_description = (role.description or None) if role else None
            else:
                role_label = ORG_ROLES.get(i.role, i.role)
            recipient = await notifications.recipient_for_email(db, i.email)  # an account behind the address gets it in the app too
            delivery = await notifications.notify(INVITATION, recipient, dict(
                inviter=i.invited_by, organization=tenant.name if tenant else "an organization", workspace=workspace, role=role_label,
                role_description=role_description, note=i.note, url=invite_url(token), invitation_id=i.id,
                expires_on=i.expires_at.strftime("%d %B %Y"),
            ), channels=("email", "in_app") if send_email else ("in_app",))
            if delivery.email == "sent":
                i.email_status, i.email_sent_at, i.email_error = "SENT", datetime.now(timezone.utc), None
            elif delivery.email == "failed":
                i.email_status, i.email_error = "FAILED", delivery.email_error
        except Exception:  # never let a bug here look like success
            logger.exception("Telling %s about their invitation failed", i.email)
            if send_email:
                i.email_status, i.email_error = "FAILED", "The email couldn't be prepared."
        await db.commit()
