from typing import List
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.auth import AuthContext, get_current_auth, require_org_permission, require_recent_login
from app.core.database import get_db_session
from app.core.permissions import check_permission, role_in
from app.core.scope import membership, resolve_template_scope
from app.models.audit import AuditLog
from app.models.identity import User
from app.models.workspace import Workspace
from app.models.workspace_admin import WorkspaceMember
from app.schemas.workspace import OrganizationResponse, WorkspaceCreate, WorkspaceSummary, WorkspaceUpdate
from app.services.template_service import template_service
from app.services.workspace_service import workspace_service

router = APIRouter(tags=["Organization & Workspaces"])


async def _my_roles(db: AsyncSession, auth: AuthContext) -> dict:
    rows = (await db.execute(
        select(WorkspaceMember.template_id, WorkspaceMember.role).where(
            WorkspaceMember.tenant_id == auth.tenant_id, WorkspaceMember.user_id == auth.user_id)
    )).all()
    return {template_id: role for template_id, role in rows}


@router.get("/organization", response_model=OrganizationResponse)
async def get_organization(db: AsyncSession = Depends(get_db_session), auth: AuthContext = Depends(get_current_auth)):
    """Opening an organization is what makes it your current one: signing in again opens the one you were last in."""
    user = await db.get(User, auth.user_id)
    if user is not None and user.last_org_id != auth.tenant_id:
        user.last_org_id = auth.tenant_id
        await db.commit()
    return await workspace_service.organization(db, auth.tenant_id)


@router.get("/workspaces", response_model=List[WorkspaceSummary])
async def list_workspaces(db: AsyncSession = Depends(get_db_session), auth: AuthContext = Depends(get_current_auth)):
    """
    Workspaces you belong to. An organization admin also sees the others (to manage or join them),
    marked with no role: their data stays closed until they hold a role there.
    """
    roles = await _my_roles(db, auth)
    summaries = await workspace_service.list_summaries(db, auth.tenant_id)
    visible = [s for s in summaries if auth.has_template_access(s.id) and (s.id in roles or auth.is_org_admin())]
    return [s.model_copy(update={"my_role": roles.get(s.id)}) for s in visible]


@router.post("/workspaces", response_model=WorkspaceSummary, status_code=201)
async def create_workspace(
    payload: WorkspaceCreate,
    db: AsyncSession = Depends(get_db_session),
    auth: AuthContext = Depends(require_org_permission("workspaces.create")),
):
    """Creates a draft workspace owned by you. Its template is designed next, from sample documents, with the agent."""
    template = await template_service.create_template(
        db, tenant_id=auth.tenant_id, user_id=auth.user_id, name=payload.name.strip(),
        document_type="draft", description=payload.description,
    )
    ws = await db.get(Workspace, template.id)
    db.add(AuditLog(tenant_id=auth.tenant_id, template_id=None, event_type="WORKSPACE_CREATED", entity_type="Workspace", entity_id=template.id,
                    actor=auth.user_id, details={"name": ws.name, "slug": ws.slug}))
    await db.commit()
    return (await workspace_service.get_by_slug(db, auth.tenant_id, ws.slug)).model_copy(update={"my_role": "owner"})


async def _workspace(db: AsyncSession, auth: AuthContext, slug: str) -> Workspace:
    ws = (await db.execute(select(Workspace).where(Workspace.tenant_id == auth.tenant_id, Workspace.slug == slug))).scalar_one_or_none()
    if not ws or not auth.has_template_access(ws.id):
        raise HTTPException(status_code=404, detail=f"No workspace '{slug}' in this organization.")
    return ws


@router.get("/workspaces/{slug}", response_model=WorkspaceSummary)
async def get_workspace(slug: str, db: AsyncSession = Depends(get_db_session), auth: AuthContext = Depends(get_current_auth)):
    ws = await _workspace(db, auth, slug)
    found = await membership(db, ws.id, auth.user_id)
    if found is None and not auth.is_org_admin():
        raise HTTPException(status_code=403, detail={"code": "not_workspace_member", "message": "You're not a member of this workspace."})
    summary = await workspace_service.get_by_slug(db, auth.tenant_id, slug)
    return summary.model_copy(update={"my_role": found[1].id if found else None, "my_role_name": found[1].name if found else None})


@router.patch("/workspaces/{slug}", response_model=WorkspaceSummary)
async def update_workspace(
    slug: str,
    payload: WorkspaceUpdate,
    db: AsyncSession = Depends(get_db_session),
    auth: AuthContext = Depends(get_current_auth),
):
    ws = await _workspace(db, auth, slug)
    await check_permission(db, await resolve_template_scope(db, auth, ws.id), auth, "workspace.manage")
    template = await template_service.get_template(db, ws.id)
    if payload.name is not None:
        ws.name = template.name = payload.name.strip()
    if payload.description is not None:
        ws.description = template.description = payload.description
    await db.commit()
    return await workspace_service.get_by_slug(db, auth.tenant_id, ws.slug)


@router.delete("/workspaces/{slug}", status_code=204, dependencies=[Depends(require_recent_login)])
async def delete_workspace(
    slug: str,
    db: AsyncSession = Depends(get_db_session),
    auth: AuthContext = Depends(require_org_permission("workspaces.delete")),
):
    """Deletes a workspace and everything in it. Organization owners and admins can, without opening its data."""
    ws = await _workspace(db, auth, slug)
    template = await template_service.get_template(db, ws.id)
    db.add(AuditLog(tenant_id=auth.tenant_id, template_id=None, event_type="WORKSPACE_DELETED", entity_type="Workspace",
                    entity_id=ws.id, actor=auth.user_id, details={"name": ws.name, "slug": ws.slug}))
    await db.delete(ws)
    await db.delete(template)
    await db.commit()


class JoinIn(BaseModel):
    role: str = "admin"


@router.post("/workspaces/{slug}/join", response_model=WorkspaceSummary)
async def join_workspace(
    slug: str,
    body: JoinIn = JoinIn(),
    db: AsyncSession = Depends(get_db_session),
    auth: AuthContext = Depends(require_org_permission("org.manage")),
):
    """
    An organization admin adds themselves to a workspace to open its data. Opening it is deliberate and
    recorded in the audit log; only a system role can be taken this way.
    """
    ws = await _workspace(db, auth, slug)
    if await membership(db, ws.id, auth.user_id):
        raise HTTPException(status_code=409, detail="You're already a member of this workspace.")
    role = await role_in(db, ws.id, body.role)
    if role is None or not role.is_system:
        raise HTTPException(status_code=422, detail="Choose one of the built-in roles.")
    if role.id == "owner" and auth.org_role != "owner":
        raise HTTPException(status_code=403, detail="Only an organization owner can take the owner role.")
    user = await db.get(User, auth.user_id)
    db.add(WorkspaceMember(
        tenant_id=auth.tenant_id, template_id=ws.id, user_id=auth.user_id, role=role.id,
        name=user.display_name if user else auth.username or auth.user_id, email=user.email if user else auth.email,
    ))
    db.add(AuditLog(
        tenant_id=auth.tenant_id, template_id=ws.id, event_type="ORG_ADMIN_JOINED_WORKSPACE", entity_type="Workspace",
        entity_id=ws.id, actor=auth.user_id, details={"role": role.id, "org_role": auth.org_role},
    ))
    await db.commit()
    summary = await workspace_service.get_by_slug(db, auth.tenant_id, slug)
    return summary.model_copy(update={"my_role": role.id, "my_role_name": role.name})
