"""What several settings areas share: a workspace's members and roles, and which roles a caller may hand out."""
from typing import Dict, List
from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.permissions import PERMISSIONS, Access, role_in
from app.models.identity import WorkspaceRole
from app.models.workspace_admin import WorkspaceMember


async def members(db: AsyncSession, template_id: str) -> List[WorkspaceMember]:
    return list((await db.execute(
        select(WorkspaceMember).where(WorkspaceMember.template_id == template_id).order_by(WorkspaceMember.created_at)
    )).scalars().all())


async def roles_here(db: AsyncSession, template_id: str) -> List[WorkspaceRole]:
    """The built-in roles first, then this workspace's own."""
    return list((await db.execute(
        select(WorkspaceRole).where(or_(WorkspaceRole.is_system.is_(True), WorkspaceRole.template_id == template_id))
        .order_by(WorkspaceRole.is_system.desc(), WorkspaceRole.created_at)
    )).scalars().all())


async def role_names(db: AsyncSession, template_id: str) -> Dict[str, str]:
    return {r.id: r.name for r in await roles_here(db, template_id)}


def check_grantable(access: Access, permissions: List[str]) -> None:
    """Nobody can hand out a privilege they don't hold themselves."""
    beyond = sorted(set(permissions) - access.permissions)
    if beyond:
        raise HTTPException(status_code=403, detail=f"You can't grant privileges you don't have: {', '.join(PERMISSIONS[p] for p in beyond)}.")


async def assignable(db: AsyncSession, access: Access, role_id: str) -> WorkspaceRole:
    """A role the caller may give to someone: it exists here and grants nothing the caller lacks."""
    role = await role_in(db, access.scope.template_id, role_id)
    if role is None:
        raise HTTPException(status_code=422, detail="That role doesn't exist in this workspace.")
    if role.id == "owner" and access.role != "owner":
        raise HTTPException(status_code=403, detail="Only an owner can grant the owner role.")
    check_grantable(access, list(role.permissions or []))
    return role
