"""
Workspace settings: general preferences, plan and usage, members and roles, invitations,
your own notification preferences, API keys and webhooks. Every route checks the caller's
role in the workspace (app/core/permissions.py).
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core import ratelimit
from app.core.auth import require_recent_login
from app.core.database import get_db_session
from app.core.permissions import IMPLIES, PERMISSION_GROUPS, PERMISSIONS, Access, clean_permissions, get_access, require, role_in
from app.core.secrets import seal
from app.models.identity import WorkspaceRole
from app.models.workspace_admin import ApiKey, Webhook, WebhookDelivery, WorkspaceInvitation, WorkspaceMember
from app.models.jobs import WorkspaceJob
from app.models.template import DocumentTemplate
from app.services import budget_service, invitation_mail, scheduler, settings_service, workspace_events
from app.services.source_data import SourceError, _check_host

router = APIRouter(prefix="/settings", tags=["Workspace settings"])

INVITE_DAYS = 7


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _access_dict(access: Access) -> Dict[str, Any]:
    return {"role": access.role, "role_label": access.role_name, "permissions": sorted(access.permissions)}


# ---------------------------------------------------------------------------
# Settings, plan and usage
# ---------------------------------------------------------------------------

@router.get("")
async def get_settings(access: Access = Depends(get_access), db: AsyncSession = Depends(get_db_session)):
    data = await settings_service.get_settings(db, access.scope.template_id)
    return {"settings": data.model_dump(), "access": _access_dict(access)}


@router.patch("")
async def update_settings(patch: Dict[str, Any], access: Access = Depends(require("workspace.manage")), db: AsyncSession = Depends(get_db_session)):
    data = await settings_service.update_settings(db, access.scope.template_id, patch, access.auth.user_id)
    await db.commit()
    budget_service.forget(access.scope.template_id)
    return {"settings": data.model_dump(), "access": _access_dict(access)}


@router.get("/budget")
async def budget(access: Access = Depends(get_access), db: AsyncSession = Depends(get_db_session)):
    """The agent's budget this month: limits, what is used, and whether its work is paused."""
    return await budget_service.status(db, access.scope.template_id)


@router.get("/reflection")
async def reflection(access: Access = Depends(get_access), db: AsyncSession = Depends(get_db_session)):
    """When the agent last reflected, what it found, and when it will next."""
    tid = access.scope.template_id
    return {
        "cadence": (await settings_service.get_settings(db, tid)).agent.reflection,
        "reflect": scheduler.job_dict(await db.get(WorkspaceJob, (tid, "reflect"))),
        "graph": scheduler.job_dict(await db.get(WorkspaceJob, (tid, "graph"))),
    }


@router.post("/reflection/run")
async def reflect_now(access: Access = Depends(require("workspace.manage")), db: AsyncSession = Depends(get_db_session)):
    """Consolidates memory and asks the agent to reflect now, whatever the schedule."""
    template = await db.get(DocumentTemplate, access.scope.template_id)
    if not template or not template.active_version_id:
        raise HTTPException(status_code=409, detail="Publish the template first: there is no work to reflect on yet.")
    result = await scheduler.reflect(db, template, requested_by={
        "id": access.auth.user_id, "name": access.auth.username or access.auth.user_id,
    })
    if result is None:
        raise HTTPException(status_code=409, detail="A reflection is already being prepared. Try again in a moment.")
    await db.commit()
    scheduler.agent_event_relay.notify()
    await workspace_events.dispatch(db)
    return {"reflect": scheduler.job_dict(await db.get(WorkspaceJob, (template.id, "reflect"))), "result": result}


@router.get("/billing")
async def billing(access: Access = Depends(get_access), db: AsyncSession = Depends(get_db_session)):
    plan = (await settings_service.get_settings(db, access.scope.template_id)).plan
    return {
        "plan": plan, "plans": settings_service.PLANS, "usage": await settings_service.usage(db, access.scope.template_id),
        # No payment provider is connected yet: nothing is charged, and nothing is pretended.
        "payment_method": None, "invoices": [], "payments_enabled": False,
    }


# ---------------------------------------------------------------------------
# Members and roles
# ---------------------------------------------------------------------------

def _member_dict(m: WorkspaceMember, role_names: Dict[str, str]) -> Dict[str, Any]:
    return {"id": m.id, "user_id": m.user_id, "name": m.name, "email": m.email, "role": m.role,
            "role_label": role_names.get(m.role, m.role), "created_at": m.created_at}


async def _members(db: AsyncSession, template_id: str) -> List[WorkspaceMember]:
    return list((await db.execute(
        select(WorkspaceMember).where(WorkspaceMember.template_id == template_id).order_by(WorkspaceMember.created_at)
    )).scalars().all())


async def _roles_here(db: AsyncSession, template_id: str) -> List[WorkspaceRole]:
    """The built-in roles first, then this workspace's own."""
    return list((await db.execute(
        select(WorkspaceRole).where(or_(WorkspaceRole.is_system.is_(True), WorkspaceRole.template_id == template_id))
        .order_by(WorkspaceRole.is_system.desc(), WorkspaceRole.created_at)
    )).scalars().all())


def _role_dict(r: WorkspaceRole, members: int = 0) -> Dict[str, Any]:
    return {"key": r.id, "id": r.id, "label": r.name, "name": r.name, "description": r.description, "system": r.is_system,
            "permissions": sorted(r.permissions or []), "members": members}


@router.get("/roles")
async def roles(access: Access = Depends(get_access), db: AsyncSession = Depends(get_db_session)):
    """
    Roles you can give people here: built-in and custom, with every privilege each one grants. A role is
    a job title: it decides what a person can see and do in this workspace.
    """
    counts = {k: v for k, v in (await db.execute(
        select(WorkspaceMember.role, func.count(WorkspaceMember.id)).where(WorkspaceMember.template_id == access.scope.template_id)
        .group_by(WorkspaceMember.role)
    )).all()}
    return {
        "roles": [_role_dict(r, counts.get(r.id, 0)) for r in await _roles_here(db, access.scope.template_id)],
        "permissions": [{"key": k, "label": v} for k, v in PERMISSIONS.items()],
        "groups": [{"key": g["key"], "label": g["label"],
                    "permissions": [{"key": k, "label": PERMISSIONS[k], "description": d} for k, d in g["permissions"]]} for g in PERMISSION_GROUPS],
        "implies": IMPLIES,
        "can_manage": access.can("roles.manage"),
    }


class RoleIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    description: str = Field("", max_length=500)
    permissions: List[str] = Field(default_factory=list)


class RolePatch(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=64)
    description: Optional[str] = Field(None, max_length=500)
    permissions: Optional[List[str]] = None


def _check_grantable(access: Access, permissions: List[str]) -> None:
    """Nobody can hand out a privilege they don't hold themselves."""
    beyond = sorted(set(permissions) - access.permissions)
    if beyond:
        raise HTTPException(status_code=403, detail=f"You can't grant privileges you don't have: {', '.join(PERMISSIONS[p] for p in beyond)}.")


async def _check_role_name(db: AsyncSession, template_id: str, name: str, exclude_id: Optional[str] = None) -> None:
    for r in await _roles_here(db, template_id):
        if r.id != exclude_id and r.name.strip().lower() == name.strip().lower():
            raise HTTPException(status_code=409, detail=f"There is already a role called '{r.name}'.")


async def _custom_role(db: AsyncSession, access: Access, role_id: str) -> WorkspaceRole:
    r = await db.get(WorkspaceRole, role_id)
    if not r or (not r.is_system and r.template_id != access.scope.template_id):
        raise HTTPException(status_code=404, detail="Role not found in this workspace.")
    if r.is_system:
        raise HTTPException(status_code=409, detail="Built-in roles can't be changed.")
    return r


@router.post("/roles", status_code=201)
async def create_role(body: RoleIn, access: Access = Depends(require("roles.manage")), db: AsyncSession = Depends(get_db_session)):
    permissions = clean_permissions(body.permissions)
    _check_grantable(access, permissions)
    await _check_role_name(db, access.scope.template_id, body.name)
    role = WorkspaceRole(
        tenant_id=access.scope.tenant_id, template_id=access.scope.template_id, name=body.name.strip(),
        description=body.description.strip(), is_system=False, permissions=permissions, created_by=access.auth.user_id,
    )
    db.add(role)
    await db.commit()
    return _role_dict(role)


@router.patch("/roles/{role_id}")
async def update_role(role_id: str, body: RolePatch, access: Access = Depends(require("roles.manage")), db: AsyncSession = Depends(get_db_session)):
    role = await _custom_role(db, access, role_id)
    if body.permissions is not None:
        permissions = clean_permissions(body.permissions)
        # Only the privileges being added need to be ones the editor holds.
        _check_grantable(access, [p for p in permissions if p not in (role.permissions or [])])
        role.permissions = permissions
    if body.name is not None:
        await _check_role_name(db, access.scope.template_id, body.name, exclude_id=role.id)
        role.name = body.name.strip()
    if body.description is not None:
        role.description = body.description.strip()
    await db.commit()
    return _role_dict(role)


@router.delete("/roles/{role_id}", status_code=204)
async def delete_role(role_id: str, access: Access = Depends(require("roles.manage")), db: AsyncSession = Depends(get_db_session)):
    """A role in use can't be deleted: move its people to another role first."""
    role = await _custom_role(db, access, role_id)
    in_use = (await db.execute(select(func.count(WorkspaceMember.id)).where(WorkspaceMember.role == role.id))).scalar() or 0
    pending = (await db.execute(select(func.count(WorkspaceInvitation.id)).where(
        WorkspaceInvitation.role == role.id, WorkspaceInvitation.status == "PENDING"))).scalar() or 0
    if in_use or pending:
        raise HTTPException(status_code=409, detail=f"{in_use} member(s) and {pending} invitation(s) still use this role. Move them to another role first.")
    # Finished invitations (accepted or revoked) only remember the role; they go with it.
    await db.execute(delete(WorkspaceInvitation).where(WorkspaceInvitation.role == role.id))
    await db.delete(role)
    await db.commit()


async def _role_names(db: AsyncSession, template_id: str) -> Dict[str, str]:
    return {r.id: r.name for r in await _roles_here(db, template_id)}


@router.get("/members")
async def list_members(access: Access = Depends(require("members.view")), db: AsyncSession = Depends(get_db_session)):
    names = await _role_names(db, access.scope.template_id)
    return [{**_member_dict(m, names), "is_you": m.user_id == access.auth.user_id} for m in await _members(db, access.scope.template_id)]


class RoleChange(BaseModel):
    role: str


async def _member(db: AsyncSession, access: Access, member_id: str) -> WorkspaceMember:
    m = await db.get(WorkspaceMember, member_id)
    if not m or m.template_id != access.scope.template_id:
        raise HTTPException(status_code=404, detail="Member not found in this workspace.")
    return m


async def _assignable(db: AsyncSession, access: Access, role_id: str) -> WorkspaceRole:
    """A role the caller may give to someone: it exists here and grants nothing the caller lacks."""
    role = await role_in(db, access.scope.template_id, role_id)
    if role is None:
        raise HTTPException(status_code=422, detail="That role doesn't exist in this workspace.")
    if role.id == "owner" and access.role != "owner":
        raise HTTPException(status_code=403, detail="Only an owner can grant the owner role.")
    _check_grantable(access, list(role.permissions or []))
    return role


def _guard_owners(members: List[WorkspaceMember], target: WorkspaceMember, new_role: Optional[str]) -> None:
    owners = [m for m in members if m.role == "owner"]
    if target.role == "owner" and new_role != "owner" and len(owners) == 1:
        raise HTTPException(status_code=409, detail="A workspace needs at least one owner. Make someone else an owner first.")


@router.patch("/members/{member_id}")
async def change_role(member_id: str, body: RoleChange, access: Access = Depends(require("members.manage")), db: AsyncSession = Depends(get_db_session)):
    target = await _member(db, access, member_id)
    if target.role == "owner" and access.role != "owner":
        raise HTTPException(status_code=403, detail="Only an owner can remove the owner role.")
    role = await _assignable(db, access, body.role)
    _guard_owners(await _members(db, access.scope.template_id), target, role.id)
    target.role = role.id
    await db.commit()
    return _member_dict(target, await _role_names(db, access.scope.template_id))


@router.delete("/members/{member_id}", status_code=204)
async def remove_member(member_id: str, access: Access = Depends(get_access), db: AsyncSession = Depends(get_db_session)):
    """Removes someone, or lets you leave."""
    target = await _member(db, access, member_id)
    if target.user_id != access.auth.user_id and not access.can("members.manage"):
        raise HTTPException(status_code=403, detail="Your role can't remove other members.")
    if target.role == "owner" and access.role != "owner" and target.user_id != access.auth.user_id:
        raise HTTPException(status_code=403, detail="Only an owner can remove an owner.")
    _guard_owners(await _members(db, access.scope.template_id), target, None)
    await db.delete(target)
    await db.commit()


@router.get("/notifications")
async def my_notification_prefs(access: Access = Depends(get_access), db: AsyncSession = Depends(get_db_session)):
    me = (await db.execute(select(WorkspaceMember).where(
        WorkspaceMember.template_id == access.scope.template_id, WorkspaceMember.user_id == access.auth.user_id,
    ))).scalar_one_or_none()
    prefs = (me.notification_prefs if me else {}) or {}
    return [
        {**t, "enabled": prefs.get(t["type"], t["notify_by_default"]),
         "available": workspace_events.EVENT_TYPES[t["type"]]["permission"] in access.permissions}
        for t in workspace_events.event_types()
    ]


@router.put("/notifications")
async def set_my_notification_prefs(prefs: Dict[str, bool], access: Access = Depends(get_access), db: AsyncSession = Depends(get_db_session)):
    me = (await db.execute(select(WorkspaceMember).where(
        WorkspaceMember.template_id == access.scope.template_id, WorkspaceMember.user_id == access.auth.user_id,
    ))).scalar_one_or_none()
    if me is None:
        raise HTTPException(status_code=409, detail="Only members of this workspace have notification preferences here.")
    me.notification_prefs = {k: bool(v) for k, v in prefs.items() if k in workspace_events.EVENT_TYPES}
    await db.commit()
    return await my_notification_prefs(access, db)


# ---------------------------------------------------------------------------
# Invitations
# ---------------------------------------------------------------------------

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
    names = await _role_names(db, access.scope.template_id)
    return [_invitation_dict(i, names) for i in rows]


def _new_token(invitation: WorkspaceInvitation) -> str:
    token = secrets.token_urlsafe(24)
    invitation.token_hash = _hash(token)
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
    role = await _assignable(db, access, body.role)
    email = body.email.strip().lower()
    members = await _members(db, access.scope.template_id)
    if any((m.email or "").lower() == email for m in members):
        raise HTTPException(status_code=409, detail=f"{email} is already a member.")
    pending = (await db.execute(select(WorkspaceInvitation).where(
        WorkspaceInvitation.template_id == access.scope.template_id, WorkspaceInvitation.email == email,
        WorkspaceInvitation.status == "PENDING", WorkspaceInvitation.expires_at > datetime.now(timezone.utc)))).scalar_one_or_none()
    if pending is not None:
        raise HTTPException(status_code=409, detail=f"{email} already has a pending invitation. Resend it, or revoke it first to change the role.")
    await settings_service.check_limit(db, access.scope.template_id, "members")
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
    return _issued(invitation, token, await _role_names(db, access.scope.template_id))


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
    return _issued(i, token, await _role_names(db, access.scope.template_id))


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
    return _issued(i, token, await _role_names(db, access.scope.template_id))


@router.delete("/invitations/{invitation_id}", status_code=204)
async def revoke_invitation(invitation_id: str, access: Access = Depends(require("members.manage")), db: AsyncSession = Depends(get_db_session)):
    i = await _invitation(db, access, invitation_id)
    i.status = "REVOKED"
    await invitation_mail.withdraw_notification(db, i)
    await db.commit()


# ---------------------------------------------------------------------------
# API keys
# ---------------------------------------------------------------------------

class ApiKeyIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    scope: Literal["read", "write"] = "read"
    expires_in_days: Optional[int] = Field(None, ge=1, le=730)


def _key_dict(k: ApiKey) -> Dict[str, Any]:
    return {"id": k.id, "name": k.name, "prefix": k.prefix, "scope": k.scope, "created_by": k.created_by,
            "created_at": k.created_at, "expires_at": k.expires_at, "last_used_at": k.last_used_at, "revoked_at": k.revoked_at}


@router.get("/api-keys")
async def list_api_keys(access: Access = Depends(require("integrations.manage")), db: AsyncSession = Depends(get_db_session)):
    rows = (await db.execute(select(ApiKey).where(ApiKey.template_id == access.scope.template_id, ApiKey.scope != "intake")
                             .order_by(ApiKey.created_at.desc()))).scalars().all()
    return [_key_dict(k) for k in rows]


@router.post("/api-keys", status_code=201, dependencies=[Depends(require_recent_login)])
async def create_api_key(body: ApiKeyIn, access: Access = Depends(require("integrations.manage")), db: AsyncSession = Depends(get_db_session)):
    """The key is returned once. Send it as `Authorization: Bearer <key>` with `X-Template-ID`."""
    await settings_service.check_limit(db, access.scope.template_id, "api_keys")
    plain = f"sk_{secrets.token_urlsafe(32)}"
    key = ApiKey(
        tenant_id=access.scope.tenant_id, template_id=access.scope.template_id, name=body.name.strip(),
        prefix=plain[:10], key_hash=_hash(plain), scope=body.scope, created_by=access.auth.username or access.auth.user_id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=body.expires_in_days) if body.expires_in_days else None,
    )
    db.add(key)
    await db.commit()
    return {"api_key": _key_dict(key), "key": plain}


@router.delete("/api-keys/{key_id}", status_code=204, dependencies=[Depends(require_recent_login)])
async def revoke_api_key(key_id: str, access: Access = Depends(require("integrations.manage")), db: AsyncSession = Depends(get_db_session)):
    key = await db.get(ApiKey, key_id)
    if not key or key.template_id != access.scope.template_id or key.scope == "intake":
        raise HTTPException(status_code=404, detail="API key not found.")
    key.revoked_at = key.revoked_at or datetime.now(timezone.utc)
    await db.commit()


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------

class WebhookIn(BaseModel):
    url: str
    description: str = ""
    events: List[str] = Field(default_factory=lambda: ["*"])
    active: bool = True


class WebhookPatch(BaseModel):
    url: Optional[str] = None
    description: Optional[str] = None
    events: Optional[List[str]] = None
    active: Optional[bool] = None


async def _webhook_dict(db: AsyncSession, h: Webhook) -> Dict[str, Any]:
    last = (await db.execute(select(WebhookDelivery).where(WebhookDelivery.webhook_id == h.id)
                             .order_by(WebhookDelivery.created_at.desc()).limit(1))).scalar_one_or_none()
    return {"id": h.id, "url": h.url, "description": h.description, "events": h.events, "active": h.active,
            "created_by": h.created_by, "created_at": h.created_at,
            "last_delivery": {"ok": last.ok, "status_code": last.status_code, "error": last.error, "at": last.created_at} if last else None}


async def _check_webhook(url: str, events: List[str]) -> None:
    unknown = [e for e in events if e != "*" and e not in workspace_events.EVENT_TYPES]
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown events: {', '.join(unknown)}.")
    if not url.startswith("https://") and not url.startswith("http://"):
        raise HTTPException(status_code=422, detail="The URL must start with https://.")
    try:
        await _check_host(url)
    except SourceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


async def _webhook(db: AsyncSession, access: Access, hook_id: str) -> Webhook:
    h = await db.get(Webhook, hook_id)
    if not h or h.template_id != access.scope.template_id:
        raise HTTPException(status_code=404, detail="Webhook not found.")
    return h


@router.get("/webhooks")
async def list_webhooks(access: Access = Depends(require("integrations.manage")), db: AsyncSession = Depends(get_db_session)):
    rows = (await db.execute(select(Webhook).where(Webhook.template_id == access.scope.template_id).order_by(Webhook.created_at.desc()))).scalars().all()
    return {"webhooks": [await _webhook_dict(db, h) for h in rows], "events": workspace_events.event_types()}


@router.post("/webhooks", status_code=201, dependencies=[Depends(require_recent_login)])
async def create_webhook(body: WebhookIn, access: Access = Depends(require("integrations.manage")), db: AsyncSession = Depends(get_db_session)):
    """The signing secret is returned once."""
    await _check_webhook(body.url, body.events)
    await settings_service.check_limit(db, access.scope.template_id, "webhooks")
    secret = f"whsec_{secrets.token_urlsafe(24)}"
    hook = Webhook(
        tenant_id=access.scope.tenant_id, template_id=access.scope.template_id, url=body.url.strip(),
        description=body.description.strip(), events=body.events or ["*"], active=body.active,
        secret=seal({"secret": secret}), created_by=access.auth.username or access.auth.user_id,
    )
    db.add(hook)
    await db.commit()
    return {"webhook": await _webhook_dict(db, hook), "secret": secret}


@router.patch("/webhooks/{hook_id}", dependencies=[Depends(require_recent_login)])
async def update_webhook(hook_id: str, body: WebhookPatch, access: Access = Depends(require("integrations.manage")), db: AsyncSession = Depends(get_db_session)):
    hook = await _webhook(db, access, hook_id)
    if body.url is not None or body.events is not None:
        await _check_webhook(body.url or hook.url, body.events if body.events is not None else hook.events)
    for field in ("url", "description", "events", "active"):
        value = getattr(body, field)
        if value is not None:
            setattr(hook, field, value)
    await db.commit()
    return await _webhook_dict(db, hook)


@router.post("/webhooks/{hook_id}/rotate-secret", dependencies=[Depends(require_recent_login)])
async def rotate_webhook_secret(hook_id: str, access: Access = Depends(require("integrations.manage")), db: AsyncSession = Depends(get_db_session)):
    hook = await _webhook(db, access, hook_id)
    secret = f"whsec_{secrets.token_urlsafe(24)}"
    hook.secret = seal({"secret": secret})
    await db.commit()
    return {"secret": secret}


@router.post("/webhooks/{hook_id}/test")
async def test_webhook(hook_id: str, access: Access = Depends(require("integrations.manage")), db: AsyncSession = Depends(get_db_session)):
    """Sends a `ping` event now and reports how the endpoint answered."""
    await _webhook(db, access, hook_id)
    delivery = await workspace_events.deliver(hook_id, {
        "id": f"evt_test_{secrets.token_hex(6)}", "type": "ping", "created_at": datetime.now(timezone.utc).isoformat(),
        "workspace_id": access.scope.template_id, "data": {"message": "A test from your workspace's webhook settings."},
    })
    return {"ok": delivery.ok, "status_code": delivery.status_code, "error": delivery.error, "duration_ms": delivery.duration_ms}


@router.get("/webhooks/{hook_id}/deliveries")
async def webhook_deliveries(hook_id: str, access: Access = Depends(require("integrations.manage")), db: AsyncSession = Depends(get_db_session)):
    await _webhook(db, access, hook_id)
    rows = (await db.execute(select(WebhookDelivery).where(WebhookDelivery.webhook_id == hook_id)
                             .order_by(WebhookDelivery.created_at.desc()).limit(50))).scalars().all()
    return [{"id": d.id, "event_type": d.event_type, "ok": d.ok, "status_code": d.status_code, "error": d.error,
             "duration_ms": d.duration_ms, "at": d.created_at} for d in rows]


@router.delete("/webhooks/{hook_id}", status_code=204, dependencies=[Depends(require_recent_login)])
async def delete_webhook(hook_id: str, access: Access = Depends(require("integrations.manage")), db: AsyncSession = Depends(get_db_session)):
    await db.delete(await _webhook(db, access, hook_id))
    await db.commit()
