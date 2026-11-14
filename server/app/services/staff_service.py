"""
Platform staff accounts, kept apart from customer accounts.

    staff_role         who is on the staff (PLATFORM_ADMIN_USER_IDS, or a platform_staff row), and as what
    invite / resend    an invitation by email, to anyone: they need no account yet
    preview / accept   opening the invitation (signed in with the invited address); a customer account under the same
                       sign-in is shown first, and deleted only once they confirm: organizations they alone own are
                       closed, their other memberships removed, their customer profile cleared
    tell               an email to a staff member when their place on the staff changes

A staff account works only in the admin app. The customer API refuses it (core/auth/dependencies.py), it can't create or
join an organization, and organizations can't invite its address.
"""
import secrets as token
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.auth.context import Identity
from app.core.config import settings
from app.core.platform_permissions import PLATFORM_ROLES
from app.core.secrets import token_hash
from app.models.audit import AuditLog
from app.models.identity import OrgMember, TrustedDevice, User
from app.models.notification import Notification
from app.models.platform import PlatformAuditEntry, PlatformStaff, PlatformStaffInvitation
from app.models.tenant import Tenant
from app.models.workspace import Workspace
from app.models.workspace_admin import WorkspaceMember

INVITE_DAYS = 7


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def staff_role(db: AsyncSession, user_id: str) -> Tuple[Optional[str], bool]:
    """(role, set by the deployment) for someone on the platform staff, or (None, False)."""
    if user_id in settings.platform_admin_ids:
        return "owner", True
    row = await db.get(PlatformStaff, user_id)
    return (row.role, False) if row else (None, False)


async def is_staff(db: AsyncSession, user_id: str) -> bool:
    return (await staff_role(db, user_id))[0] is not None


async def staff_email(db: AsyncSession, email: str) -> bool:
    """Whether an address belongs to a staff account (organizations can't invite it)."""
    user = (await db.execute(select(User).where(func.lower(User.email) == email.strip().lower()))).scalar_one_or_none()
    return bool(user and await is_staff(db, user.id))


def refuse_staff() -> HTTPException:
    return HTTPException(status_code=403, detail={
        "code": "staff_account",
        "message": "This is a Syruss staff account: it works only in the admin app. Use a different address for a customer account."})


def join_url(plain: str) -> str:
    return f"{settings.PUBLIC_ADMIN_URL.rstrip('/')}/join/{plain}"


# ---- inviting -------------------------------------------------------------------------------------

async def invite(db: AsyncSession, *, email: str, role: str, by_id: str, by_name: str, reason: Optional[str] = None,
                 by_email: Optional[str] = None
                 ) -> Tuple[PlatformStaffInvitation, str]:
    """A new invitation (the caller commits, then sends it with `send`). Returns it and its link token, shown nowhere else."""
    email = email.strip().lower()
    existing = (await db.execute(select(User).where(func.lower(User.email) == email))).scalar_one_or_none()
    if existing and await is_staff(db, existing.id):
        raise HTTPException(status_code=409, detail=f"{email} is already on the platform staff.")
    pending = (await db.execute(select(PlatformStaffInvitation).where(
        PlatformStaffInvitation.email == email, PlatformStaffInvitation.status == "pending",
        PlatformStaffInvitation.expires_at > _now()))).scalar_one_or_none()
    if pending:
        raise HTTPException(status_code=409, detail=f"{email} already has an invitation waiting. Send it again, or revoke it to change the role.")
    plain = token.token_urlsafe(32)
    row = PlatformStaffInvitation(email=email, role=role, token_hash=token_hash(plain), expires_at=_now() + timedelta(days=INVITE_DAYS),
                                  invited_by=by_id, invited_by_name=by_name, invited_by_email=by_email, reason=(reason or "").strip() or None)
    db.add(row)
    await db.flush()
    return row, plain


def renew(row: PlatformStaffInvitation) -> str:
    """A fresh link and a fresh week (the old link stops working)."""
    plain = token.token_urlsafe(32)
    row.token_hash, row.expires_at = token_hash(plain), _now() + timedelta(days=INVITE_DAYS)
    return plain


async def send(invitation_id: str, plain: str) -> bool:
    """Emails the invitation. True when it left. Never raises."""
    from app.core.database import AsyncSessionLocal
    from app.services.notifications import STAFF_INVITATION, Recipient, notify
    async with AsyncSessionLocal() as db:
        row = await db.get(PlatformStaffInvitation, invitation_id)
        if row is None:
            return False
        try:
            delivery = await notify(STAFF_INVITATION, Recipient(email=row.email), {
                "inviter": row.invited_by_name, "role": row.role.title(), "role_description": PLATFORM_ROLES.get(row.role, ""),
                "url": join_url(plain), "expires_on": f"{row.expires_at:%d %B %Y}"}, channels={"email"})
        except Exception:  # noqa: BLE001  (a mail problem must not fail the invitation)
            return False
        sent = getattr(delivery, "email", None) == "sent"
        if sent:
            row.email_sent_at = _now()
            await db.commit()
        return sent


async def tell(email: Optional[str], title: str, body: str, with_link: bool = True, path: str = "/overview") -> None:
    """An email to a staff member (their place on the staff, an approval), linking to a page of the admin app. Never raises."""
    if not email:
        return
    from app.services.notifications import STAFF_UPDATE, Recipient, notify
    try:
        data: Dict[str, Any] = {"title": title, "body": body}
        if with_link:
            data["url"] = settings.PUBLIC_ADMIN_URL.rstrip("/") + path
        await notify(STAFF_UPDATE, Recipient(email=email), data, channels={"email"})
    except Exception:  # noqa: BLE001
        pass


# ---- accepting ------------------------------------------------------------------------------------

async def by_token(db: AsyncSession, plain: str) -> PlatformStaffInvitation:
    row = (await db.execute(select(PlatformStaffInvitation).where(PlatformStaffInvitation.token_hash == token_hash(plain)))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="This invitation link isn't valid. Ask a platform owner for a new one.")
    return row


@dataclass
class CustomerAccount:
    """What a customer account under this sign-in has, and what accepting would do to it."""
    organizations: List[Dict[str, Any]]
    workspaces: int

    def as_dict(self) -> Dict[str, Any]:
        return {"organizations": self.organizations, "workspaces": self.workspaces}


async def customer_account(db: AsyncSession, user_id: str) -> Optional[CustomerAccount]:
    """The customer side of an account (None when there is none): each organization and what happens to it."""
    user = await db.get(User, user_id)
    if user is None or await is_staff(db, user_id):
        return None
    rows = (await db.execute(select(Tenant, OrgMember.role).join(OrgMember, OrgMember.tenant_id == Tenant.id)
                             .where(OrgMember.user_id == user_id).order_by(Tenant.name))).all()
    orgs = []
    for tenant, role in rows:
        owners = (await db.execute(select(func.count(OrgMember.id)).where(
            OrgMember.tenant_id == tenant.id, OrgMember.role == "owner", OrgMember.user_id != user_id))).scalar() or 0
        alone = role == "owner" and owners == 0 and tenant.status != "closed"
        orgs.append({"id": tenant.id, "name": tenant.name, "role": role, "status": tenant.status,
                     "outcome": "closed" if alone else "left"})
    spaces = (await db.execute(select(func.count(WorkspaceMember.id)).where(WorkspaceMember.user_id == user_id))).scalar() or 0
    return CustomerAccount(orgs, spaces)


async def check_open(db: AsyncSession, row: PlatformStaffInvitation, identity: Identity) -> None:
    if row.status != "pending" or row.expires_at < _now():
        raise HTTPException(status_code=409, detail="This invitation was already used, revoked or has expired. Ask a platform owner for a new one.")
    if (identity.email or "").strip().lower() != row.email:
        raise HTTPException(status_code=403, detail={"code": "wrong_address",
                                                     "message": f"This invitation is for {row.email}. Sign out and sign in with that address."})
    from app.services import email_verification
    if settings.INVITE_REQUIRE_VERIFIED_EMAIL and not await email_verification.is_verified(db, identity):
        raise HTTPException(status_code=403, detail={"code": "email_unverified",
                                                     "message": "Confirm your email address with the code we email you, then accept."})


async def accept(db: AsyncSession, row: PlatformStaffInvitation, identity: Identity, *, confirmed: bool) -> PlatformStaff:
    """
    Makes the signed-in person staff. A customer account under the same sign-in is deleted first, and only when `confirmed`:
    organizations they alone own are closed (their data is removed after the retention period), their other memberships
    removed, and their customer profile cleared. The caller commits.
    """
    await check_open(db, row, identity)
    if await is_staff(db, identity.user_id):
        raise HTTPException(status_code=409, detail="You're already on the platform staff.")
    customer = await customer_account(db, identity.user_id)
    user = await db.get(User, identity.user_id)
    if customer is not None and not confirmed:
        raise HTTPException(status_code=409, detail={"code": "confirm_customer_account_deletion", "customer_account": customer.as_dict(),
                                                     "message": "This address has a Syruss customer account. Accepting deletes it."})
    now = _now()
    if customer is not None:
        for org in customer.organizations:
            if org["outcome"] == "closed":
                tenant = await db.get(Tenant, org["id"])
                tenant.set_status("closed", "Its only owner joined the Syruss platform staff")
                db.add(AuditLog(tenant_id=tenant.id, event_type="ORG_DELETED", entity_type="Organization", entity_id=tenant.id, actor=user.id,
                                details={"name": tenant.name, "why": "its only owner joined the Syruss platform staff"}))
            else:
                db.add(AuditLog(tenant_id=org["id"], event_type="ORG_MEMBER_REMOVED", entity_type="OrgMember", entity_id=user.id, actor=user.id,
                                details={"user": user.id, "why": "joined the Syruss platform staff"}))
        await db.execute(delete(WorkspaceMember).where(WorkspaceMember.user_id == user.id))
        await db.execute(delete(OrgMember).where(OrgMember.user_id == user.id))
        await db.execute(delete(Notification).where(Notification.user_id == user.id))
        await db.execute(delete(TrustedDevice).where(TrustedDevice.user_id == user.id))
        user.phone, user.job_title, user.avatar_url, user.last_org_id = None, None, None, None
    if user is None:
        first, last = identity.given_name or "", identity.family_name or ""
        if not first:  # the sign-in's display name ("Nina Hire"), else the address's own name
            shown = (identity.username or "").strip()
            if shown and "@" not in shown:
                first, _, rest = shown.partition(" ")
                last = last or rest
            else:
                first = (identity.email or "staff").split("@")[0]
        user = User(id=identity.user_id, email=row.email, email_verified=bool(identity.email_verified), first_name=first[:100],
                    last_name=(last or "")[:100] or "Staff")
        db.add(user)
        await db.flush()
    member = PlatformStaff(user_id=user.id, role=row.role, added_by=row.invited_by, last_active_at=now)
    db.add(member)
    row.status, row.accepted_by, row.accepted_at = "accepted", user.id, now
    db.add(PlatformAuditEntry(actor_id=user.id, actor_name=user.display_name, action="staff.joined", target_type="user", target_id=user.id,
                              details={"name": user.display_name, "email": user.email, "role": row.role, "invited_by": row.invited_by_name,
                                       "customer_account_deleted": customer is not None,
                                       "organizations_closed": [o["name"] for o in (customer.organizations if customer else []) if o["outcome"] == "closed"]}))
    await db.flush()
    return member


async def workspaces_of(db: AsyncSession, user_id: str) -> List[str]:
    return list((await db.execute(select(Workspace.name).join(WorkspaceMember, WorkspaceMember.template_id == Workspace.id)
                                  .where(WorkspaceMember.user_id == user_id))).scalars().all())
