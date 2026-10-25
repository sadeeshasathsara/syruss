"""
Template tenancy: the second gate.

Every workspace request names a template (X-Template-ID header, `template_id` query param, or
`template_id` path param). Getting in takes: the template belongs to the caller's organization
(gate 1 already proved they belong to it), and the caller is a member of the workspace. Their role's
permissions are loaded here, on every request, so removing someone or editing a role takes effect at once.
Organization admins manage workspaces but are not members of them: they get in only by holding a role.
"""
from dataclasses import dataclass, field
from typing import FrozenSet, Optional
from fastapi import Depends, Header, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.auth import AuthContext, get_current_auth
from app.core.database import get_db_session
from app.models.identity import WorkspaceRole
from app.models.template import DocumentTemplate
from app.models.workspace_admin import WorkspaceMember


@dataclass(frozen=True)
class TemplateScope:
    tenant_id: str
    template_id: str
    user_id: str
    username: str
    template: DocumentTemplate
    role_id: str = ""
    role_name: str = ""
    permissions: FrozenSet[str] = field(default_factory=frozenset)


def _not_a_member() -> HTTPException:
    return HTTPException(status_code=403, detail={"code": "not_workspace_member", "message": "You're not a member of this workspace."})


async def membership(db: AsyncSession, template_id: str, user_id: str):
    """(member, role) of a person in a workspace, or None."""
    row = (await db.execute(
        select(WorkspaceMember, WorkspaceRole)
        .join(WorkspaceRole, WorkspaceRole.id == WorkspaceMember.role)
        .where(WorkspaceMember.template_id == template_id, WorkspaceMember.user_id == user_id)
    )).first()
    return (row[0], row[1]) if row else None


async def resolve_template_scope(
    db: AsyncSession, auth: AuthContext, template_id: Optional[str]
) -> TemplateScope:
    if not template_id:
        raise HTTPException(status_code=400, detail="A template workspace is required (X-Template-ID header or template_id).")
    if not auth.has_template_access(template_id):
        raise HTTPException(status_code=403, detail="This API key is not valid for that workspace.")
    template = await db.get(DocumentTemplate, template_id)
    if not template or template.tenant_id != auth.tenant_id:
        raise HTTPException(status_code=404, detail="Template not found.")

    if auth.user_id.startswith("apikey:"):
        role = await db.get(WorkspaceRole, "editor" if "API_KEY:WRITE" in auth.org_roles else "viewer")
    else:
        found = await membership(db, template_id, auth.user_id)
        if found is None:
            raise _not_a_member()
        role = found[1]
    return TemplateScope(
        tenant_id=auth.tenant_id,
        template_id=template_id,
        user_id=auth.user_id,
        username=auth.username or auth.user_id,
        template=template,
        role_id=role.id,
        role_name=role.name,
        permissions=frozenset(role.permissions or []),
    )


async def get_template_scope(
    request: Request,
    template_id_query: Optional[str] = Query(None, alias="template_id"),
    x_template_id: Optional[str] = Header(None, alias="X-Template-ID"),
    db: AsyncSession = Depends(get_db_session),
    auth: AuthContext = Depends(get_current_auth),
) -> TemplateScope:
    effective = request.path_params.get("template_id") or template_id_query or x_template_id
    return await resolve_template_scope(db, auth, effective)
