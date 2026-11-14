"""
The platform staff: who they are and what role each has. Nobody is added from here: people are invited from the server
(python -m app.invite_staff, or `syruss staff add`), which also makes their sign-in. Owners change roles, remove people,
withdraw invitations and review access here, after a fresh sign-in. Every change is
in the platform audit log and is emailed to the person it is about. Owners named in PLATFORM_ADMIN_USER_IDS are listed but
can't be changed here: the deployment sets them, so the platform can never be left without an owner.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.v1.sysadmin.access import Staff, check_sign_in, record, require_staff
from app.api.v1.sysadmin.approvals import act, risky
from app.core.auth import Identity, get_identity
from app.core.config import settings
from app.core.database import get_db_session
from app.core.platform_permissions import PLATFORM_GRANTS, PLATFORM_ROLES
from app.models.identity import User
from app.models.platform import PlatformStaff, PlatformStaffInvitation
from app.services import platform_controls, staff_offboarding, staff_service

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
    # Their use of the admin app, and what follows from it (offboarding and access reviews); None for the deployment's owners
    last_active_at: Optional[datetime] = None
    reviewed_at: Optional[datetime] = None
    reviewed_by_name: Optional[str] = None
    review_due_at: Optional[datetime] = None
    review_due: bool = False
    removal_at: Optional[datetime] = None


class ChangeRole(BaseModel):
    role: Role
    reason: Optional[str] = Field(None, max_length=500)


def _member(user: Optional[User], user_id: str, role: str, *, from_config: bool, row: Optional[PlatformStaff] = None,
            policy: Optional[Dict[str, int]] = None) -> StaffMember:
    access = staff_offboarding.describe(row, policy or staff_offboarding.DEFAULT_POLICY) if row else {}
    return StaffMember(
        user_id=user_id, name=(user.display_name if user else None) or user_id, email=user.email if user else None, role=role,
        permissions=sorted(PLATFORM_GRANTS[role]), from_config=from_config, added_by=row.added_by if row else None,
        added_at=row.created_at if row else None, last_seen_at=user.last_seen_at if user else None,
        status=user.status if user else "no account yet", **access,
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
    policy = await staff_offboarding.policy(db)
    items = [_member(users.get(uid), uid, "owner", from_config=True) for uid in configured]
    items += [_member(u, r.user_id, r.role, from_config=False, row=r, policy=policy) for r, u in rows]
    return {"items": items, "roles": PLATFORM_ROLES, "policy": policy}


# ---- unused and unneeded access (see services/staff_offboarding.py) ----------------------------------

class Review(BaseModel):
    note: Optional[str] = Field(None, max_length=500)


@router.post("/{user_id}/review")
async def review(user_id: str, body: Review, staff: Staff = Depends(require_staff("staff.manage")),
                 db: AsyncSession = Depends(get_db_session)) -> StaffMember:
    """An owner confirms this person still needs their platform access (not their own: another owner confirms that)."""
    row = await _changeable(db, staff, user_id)
    row.reviewed_at, row.reviewed_by_name = datetime.now(timezone.utc), staff.name
    user = await db.get(User, user_id)
    record(db, staff, "staff.reviewed", "user", user_id, reason=body.note, details={**_who(user), "role": row.role})
    await db.commit()
    return _member(user, user_id, row.role, from_config=False, row=row, policy=await staff_offboarding.policy(db))


class PolicyIn(BaseModel):
    inactive_days: int = Field(..., ge=0, le=365, description="Days without using the admin app before access is taken away (0: never)")
    warn_days: int = Field(..., ge=0, le=30, description="How many days before that they are told")
    review_days: int = Field(..., ge=0, le=365, description="How often an owner confirms each person still needs access (0: never)")
    reason: str = Field(..., min_length=5, max_length=500)


@router.put("/policy")
async def set_policy(body: PolicyIn, staff: Staff = Depends(require_staff("staff.manage", recent=True)),
                     db: AsyncSession = Depends(get_db_session)) -> Dict[str, int]:
    if body.inactive_days and body.inactive_days < 14:
        raise HTTPException(status_code=422, detail="Give people at least 14 days without the admin app before their access is taken away.")
    if body.inactive_days and body.warn_days >= body.inactive_days:
        raise HTTPException(status_code=422, detail="The warning has to come before access is taken away.")
    if body.review_days and body.review_days < 30:
        raise HTTPException(status_code=422, detail="Reviews can be at most monthly (30 days).")
    value = body.model_dump(exclude={"reason"})
    await platform_controls.save(db, staff_offboarding.POLICY, value, by_id=staff.user_id, by_name=staff.name)
    record(db, staff, "staff.policy_changed", "platform", staff_offboarding.POLICY, reason=body.reason, details=value)
    await db.commit()
    return value


def _invitation(i: PlatformStaffInvitation) -> Dict[str, Any]:
    expired = i.status == "pending" and i.expires_at < datetime.now(timezone.utc)
    return {"id": i.id, "email": i.email, "role": i.role, "status": "expired" if expired else i.status, "expires_at": i.expires_at,
            "invited_by_name": i.invited_by_name, "reason": i.reason, "email_sent_at": i.email_sent_at, "created_at": i.created_at,
            "accepted_at": i.accepted_at}


@router.get("/invitations", dependencies=[Depends(require_staff())])
async def invitations(db: AsyncSession = Depends(get_db_session)) -> List[Dict[str, Any]]:
    """Invitations waiting for an answer (and expired ones, to send again)."""
    rows = (await db.execute(select(PlatformStaffInvitation).where(PlatformStaffInvitation.status == "pending")
                             .order_by(PlatformStaffInvitation.created_at.desc()))).scalars().all()
    return [_invitation(i) for i in rows]


async def _pending(db: AsyncSession, invitation_id: str) -> PlatformStaffInvitation:
    row = await db.get(PlatformStaffInvitation, invitation_id)
    if row is None or row.status != "pending":
        raise HTTPException(status_code=404, detail="No invitation waiting with that id.")
    return row


@router.delete("/invitations/{invitation_id}", status_code=204)
async def revoke(invitation_id: str, staff: Staff = Depends(require_staff("staff.manage", recent=True)),
                 db: AsyncSession = Depends(get_db_session)) -> None:
    row = await _pending(db, invitation_id)
    row.status = "revoked"
    record(db, staff, "staff.invitation_revoked", "invitation", row.id, details={"email": row.email, "role": row.role})
    await db.commit()
    await staff_service.tell(row.email, "Your invitation to the Syruss platform staff was withdrawn",
                             f"{staff.name} withdrew the invitation to join the platform staff as {row.role.title()}. The link no longer works.",
                             with_link=False)


# ---- the invitation, from the invited person's side ---------------------------------------------

async def _invitee(identity: Identity = Depends(get_identity)) -> Identity:
    """Opened in the admin app, signed in through its client (password and one-time code)."""
    if settings.OIDC_ADMIN_CLIENT and identity.client_id != settings.OIDC_ADMIN_CLIENT:
        raise HTTPException(status_code=403, detail={"code": "admin_app_required", "message": "Open the invitation in the Syruss admin app."})
    check_sign_in(identity)
    return identity


@router.get("/join/{token}")
async def view_invitation(token: str, identity: Identity = Depends(_invitee), db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    """What the invitation is for, whether it can be accepted with this sign-in, and what accepting would remove."""
    row = await staff_service.by_token(db, token)
    customer = await staff_service.customer_account(db, identity.user_id)
    problem = None
    try:
        await staff_service.check_open(db, row, identity)
    except HTTPException as exc:
        problem = exc.detail if isinstance(exc.detail, dict) else {"code": "closed", "message": exc.detail}
    return {"invitation": _invitation(row), "role_description": PLATFORM_ROLES.get(row.role, ""), "signed_in_as": identity.email,
            "problem": problem, "already_staff": await staff_service.is_staff(db, identity.user_id),
            "customer_account": customer.as_dict() if customer else None}


class CodeIn(BaseModel):
    code: str = Field(..., min_length=4, max_length=12)


@router.post("/join/{token}/verify/send")
async def send_join_code(token: str, identity: Identity = Depends(_invitee), db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    """Emails a one-time code to the signed-in address, to prove it is theirs before they join (as customers do)."""
    await staff_service.by_token(db, token)
    from app.services import email_verification
    return await email_verification.send_code(db, identity)


@router.post("/join/{token}/verify")
async def confirm_join_code(token: str, body: CodeIn, identity: Identity = Depends(_invitee), db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    await staff_service.by_token(db, token)
    from app.services import email_verification
    return await email_verification.confirm(db, identity, body.code)


class AcceptIn(BaseModel):
    delete_customer_account: bool = Field(False, description="Required when the sign-in has a customer account: it is deleted")


@router.post("/join/{token}")
async def accept_invitation(token: str, body: AcceptIn, identity: Identity = Depends(_invitee),
                            db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    row = await staff_service.by_token(db, token)
    member = await staff_service.accept(db, row, identity, confirmed=body.delete_customer_account)
    await db.commit()
    user = await db.get(User, member.user_id)
    await staff_service.tell(user.email, "You're on the Syruss platform staff",
                             f"You joined the platform staff as {member.role.title()}, invited by {row.invited_by_name}. Sign in to the admin app with this address.")
    # The person who invited them, or, for an invitation from the server, every owner
    inviter = row.invited_by_email or await _email_of(db, row.invited_by)
    for email in [inviter] if inviter else await staff_offboarding.owner_emails(db, without=user.id):
        await staff_service.tell(email, f"{user.display_name} joined the platform staff",
                                 f"{user.display_name} ({user.email}) accepted the invitation and is on the staff as {member.role.title()}.")
    return {"role": member.role}


async def _email_of(db: AsyncSession, user_id: str) -> Optional[str]:
    user = await db.get(User, user_id)
    return user.email if user else None


@risky("staff.change_owner_role", "staff.owners", "staff.manage")
async def _change_role(db: AsyncSession, staff: Staff, p: Dict[str, Any]) -> StaffMember:
    user_id, role, reason = p["user_id"], p["role"], p.get("reason")
    row = await _changeable(db, staff, user_id)
    if row.role == "owner" and role != "owner" and await _owners_left(db, user_id) == 0:
        raise HTTPException(status_code=409, detail="The platform needs at least one owner.")
    before, row.role = row.role, role
    user = await db.get(User, user_id)
    record(db, staff, "staff.role_changed", "user", user_id, reason=reason, details={**_who(user), "from": before, "to": role})
    await db.commit()
    if before != role:
        await staff_service.tell(user.email if user else None, f"Your platform staff role is now {role.title()}",
                                 f"{staff.name} changed your role from {before.title()} to {role.title()}: {PLATFORM_ROLES.get(role, '')}"
                                 + (f" Why: {reason}" if reason else ""))
    return _member(user, user_id, row.role, from_config=False, row=row)


@router.patch("/{user_id}")
async def change_role(user_id: str, body: ChangeRole, staff: Staff = Depends(require_staff("staff.manage", recent=True)),
                      db: AsyncSession = Depends(get_db_session)) -> Any:
    """A new role. Making someone an owner, or no longer one, may need a second owner's approval."""
    row = await _changeable(db, staff, user_id)
    payload = {"user_id": user_id, "role": body.role, "reason": body.reason}
    if "owner" not in (row.role, body.role) or row.role == body.role:
        return await _change_role(db, staff, payload)
    user = await db.get(User, user_id)
    who = user.display_name if user else user_id
    summary = f"Make {who} a platform owner" if body.role == "owner" else f"Change {who} from owner to {body.role.title()}"
    return await act(db, staff, "staff.change_owner_role", payload, summary=summary, target_type="user", target_id=user_id, reason=body.reason)


@risky("staff.remove_owner", "staff.owners", "staff.manage")
async def _remove(db: AsyncSession, staff: Staff, p: Dict[str, Any]) -> None:
    user_id, reason = p["user_id"], p.get("reason")
    row = await _changeable(db, staff, user_id)
    if row.role == "owner" and await _owners_left(db, user_id) == 0:
        raise HTTPException(status_code=409, detail="The platform needs at least one owner.")
    user = await db.get(User, user_id)
    record(db, staff, "staff.removed", "user", user_id, reason=reason, details={**_who(user), "role": row.role})
    await db.delete(row)
    await db.commit()
    await staff_service.tell(user.email if user else None, "You're no longer on the Syruss platform staff",
                             f"{staff.name} removed you from the platform staff. The admin app no longer opens for this address."
                             + (f" Why: {reason}" if reason else ""), with_link=False)


@router.delete("/{user_id}", status_code=204, response_model=None)
async def remove_staff(user_id: str, reason: Optional[str] = None, staff: Staff = Depends(require_staff("staff.manage", recent=True)),
                       db: AsyncSession = Depends(get_db_session)) -> Any:
    """Takes someone off the staff at once; removing an owner may need a second owner's approval."""
    row = await _changeable(db, staff, user_id)
    payload = {"user_id": user_id, "reason": reason}
    if row.role != "owner":
        return await _remove(db, staff, payload)
    user = await db.get(User, user_id)
    who = user.display_name if user else user_id
    return await act(db, staff, "staff.remove_owner", payload, summary=f"Remove {who} (an owner) from the staff",
                     target_type="user", target_id=user_id, reason=reason)
