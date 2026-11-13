"""Roles: the built-in ones and a workspace's own, and the privileges each grants."""
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.v1.settings import common
from app.core.database import get_db_session
from app.core.permissions import IMPLIES, PERMISSION_GROUPS, PERMISSIONS, Access, clean_permissions, get_access, require
from app.models.identity import WorkspaceRole
from app.models.workspace_admin import WorkspaceInvitation, WorkspaceMember

router = APIRouter()


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
        "roles": [_role_dict(r, counts.get(r.id, 0)) for r in await common.roles_here(db, access.scope.template_id)],
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


async def _check_role_name(db: AsyncSession, template_id: str, name: str, exclude_id: Optional[str] = None) -> None:
    for r in await common.roles_here(db, template_id):
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
    common.check_grantable(access, permissions)
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
        common.check_grantable(access, [p for p in permissions if p not in (role.permissions or [])])
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
