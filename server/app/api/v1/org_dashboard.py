"""
The organization dashboard: an overview, the organization's own activity (who changed what), and closing it down.
Counts and who-did-what only; nothing here opens a workspace's documents.
"""
from datetime import datetime
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.auth import AuthContext, get_current_auth, require_org_permission, require_recent_login
from app.core.database import get_db_session
from app.core.org_permissions import ORG_ROLES
from app.models.audit import AuditLog
from app.models.identity import OrgInvitation, OrgMember, User
from app.models.tenant import Tenant
from app.services.workspace_service import workspace_service

router = APIRouter(tags=["Organization"])

# The events that belong to the organization itself, whichever workspace they touched.
ORG_EVENTS = (
    "ORG_ROLE_CHANGED", "ORG_MEMBER_REMOVED", "ORG_INVITATION_SENT", "ORG_INVITATION_REVOKED", "ORG_INVITATION_ACCEPTED",
    "ORG_RENAMED", "ORG_DELETED", "WORKSPACE_CREATED", "WORKSPACE_DELETED", "ORG_ADMIN_JOINED_WORKSPACE",
)


def _role(key: Optional[str]) -> str:
    return ORG_ROLES.get(key or "", key or "a role")


def describe(event: str, who: str, d: Dict[str, Any], names: Dict[str, str]) -> str:
    person = lambda uid: names.get(uid, "someone")  # noqa: E731
    return {
        "ORG_ROLE_CHANGED": lambda: f"{who} changed {person(d.get('user'))}'s role from {_role(d.get('from'))} to {_role(d.get('to'))}.",
        "ORG_MEMBER_REMOVED": lambda: f"{who} removed {person(d.get('user'))} from the organization.",
        "ORG_INVITATION_SENT": lambda: f"{who} invited {d.get('email', 'someone')} as {_role(d.get('role'))}.",
        "ORG_INVITATION_REVOKED": lambda: f"{who} withdrew the invitation to {d.get('email', 'someone')}.",
        "ORG_INVITATION_ACCEPTED": lambda: f"{who} joined the organization as {_role(d.get('role'))}.",
        "ORG_RENAMED": lambda: f"{who} renamed the organization from {d.get('from')} to {d.get('to')}.",
        "ORG_DELETED": lambda: f"{who} closed the organization.",
        "WORKSPACE_CREATED": lambda: f"{who} created the workspace {d.get('name', '')}.".replace(" .", "."),
        "WORKSPACE_DELETED": lambda: f"{who} deleted the workspace {d.get('name', '')}.".replace(" .", "."),
        "ORG_ADMIN_JOINED_WORKSPACE": lambda: f"{who} joined a workspace as {d.get('role', 'a member')} to manage it.",
    }.get(event, lambda: f"{who}: {event.replace('_', ' ').lower()}")()


async def _names(db: AsyncSession, ids: set) -> Dict[str, str]:
    if not ids:
        return {}
    users = (await db.execute(select(User).where(User.id.in_(ids)))).scalars().all()
    return {u.id: u.display_name for u in users}


async def _activity(db: AsyncSession, tenant_id: str, *, limit: int, before: Optional[datetime]) -> Dict[str, Any]:
    stmt = select(AuditLog).where(AuditLog.tenant_id == tenant_id, AuditLog.event_type.in_(ORG_EVENTS))
    if before:
        stmt = stmt.where(AuditLog.created_at < before)
    rows: List[AuditLog] = list((await db.execute(stmt.order_by(AuditLog.created_at.desc()).limit(limit + 1))).scalars().all())
    shown = rows[:limit]
    ids = {r.actor for r in shown} | {str(r.details.get("user")) for r in shown if r.details.get("user")}
    names = await _names(db, ids)
    items = [{
        "id": r.id, "event": r.event_type, "created_at": r.created_at, "actor": names.get(r.actor, "System" if r.actor in ("system", "agent") else "Someone"),
        "text": describe(r.event_type, names.get(r.actor, "Someone"), r.details or {}, names),
    } for r in shown]
    return {"items": items, "next_before": items[-1]["created_at"] if len(rows) > limit and items else None}


@router.get("/organization/overview")
async def overview(auth: AuthContext = Depends(get_current_auth), db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    tenant = await db.get(Tenant, auth.tenant_id)
    summaries = await workspace_service.list_summaries(db, auth.tenant_id)
    mine = {w.id for w in summaries if auth.has_template_access(w.id)}
    from app.api.v1.workspaces import _my_roles
    roles = await _my_roles(db, auth)
    mine_summaries = [w for w in summaries if w.id in roles]
    people = dict((await db.execute(select(OrgMember.role, func.count(OrgMember.id)).where(OrgMember.tenant_id == auth.tenant_id).group_by(OrgMember.role))).all())
    can_manage = "org.members.manage" in auth.org_permissions
    pending = (await db.execute(select(func.count(OrgInvitation.id)).where(
        OrgInvitation.tenant_id == auth.tenant_id, OrgInvitation.status == "PENDING", OrgInvitation.expires_at > func.now()))).scalar() if can_manage else None
    can_audit = "org.audit.view" in auth.org_permissions
    return {
        "organization": {"name": tenant.name, "slug": tenant.slug, "created_at": tenant.created_at},
        "workspaces": {"total": len(summaries), "draft": sum(1 for w in summaries if w.status == "draft"), "mine": len(mine_summaries), "reachable": len(mine)},
        "people": {"total": sum(people.values()), "owners": people.get("owner", 0), "admins": people.get("admin", 0), "members": people.get("member", 0)},
        "pending_invitations": pending,
        "attention": {
            "needs_review": sum(w.needs_review for w in mine_summaries),
            "questions": sum(w.open_questions + w.pending_proposals for w in mine_summaries),
            "drafts": sum(1 for w in mine_summaries if w.status == "draft"),
        },
        "activity": (await _activity(db, auth.tenant_id, limit=6, before=None))["items"] if can_audit else None,
    }


@router.get("/organization/activity")
async def activity(
    limit: int = Query(30, ge=1, le=100), before: Optional[datetime] = None,
    auth: AuthContext = Depends(require_org_permission("org.audit.view")), db: AsyncSession = Depends(get_db_session),
):
    """Who changed what in the organization, newest first, a page at a time."""
    return await _activity(db, auth.tenant_id, limit=limit, before=before)


class CloseIn(BaseModel):
    confirm: str


@router.delete("/organization", status_code=204, dependencies=[Depends(require_recent_login)])
async def close_organization(body: CloseIn, auth: AuthContext = Depends(get_current_auth), db: AsyncSession = Depends(get_db_session)):
    """
    Closes the organization for everyone. Only an owner may, and only by typing its name. Nothing is erased here: the
    organization stops opening, and its data can be removed later by the platform.
    """
    if auth.org_role != "owner":
        raise HTTPException(status_code=403, detail="Only an owner can close the organization.")
    tenant = await db.get(Tenant, auth.tenant_id)
    if body.confirm.strip() != tenant.name:
        raise HTTPException(status_code=422, detail="Type the organization's name exactly to confirm.")
    tenant.is_active = False
    db.add(AuditLog(tenant_id=tenant.id, event_type="ORG_DELETED", entity_type="Organization", entity_id=tenant.id, actor=auth.user_id, details={"name": tenant.name}))
    await db.commit()
