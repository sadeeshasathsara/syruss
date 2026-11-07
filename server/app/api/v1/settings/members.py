"""Members: who is in the workspace, changing someone's role, and removing someone (or leaving)."""
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.v1.settings import common
from app.core.database import get_db_session
from app.core.permissions import Access, get_access, require
from app.models.workspace_admin import WorkspaceMember

router = APIRouter()


def _member_dict(m: WorkspaceMember, role_names: Dict[str, str]) -> Dict[str, Any]:
    return {"id": m.id, "user_id": m.user_id, "name": m.name, "email": m.email, "role": m.role,
            "role_label": role_names.get(m.role, m.role), "created_at": m.created_at}


@router.get("/members")
async def list_members(access: Access = Depends(require("members.view")), db: AsyncSession = Depends(get_db_session)):
    names = await common.role_names(db, access.scope.template_id)
    return [{**_member_dict(m, names), "is_you": m.user_id == access.auth.user_id} for m in await common.members(db, access.scope.template_id)]


class RoleChange(BaseModel):
    role: str


async def _member(db: AsyncSession, access: Access, member_id: str) -> WorkspaceMember:
    m = await db.get(WorkspaceMember, member_id)
    if not m or m.template_id != access.scope.template_id:
        raise HTTPException(status_code=404, detail="Member not found in this workspace.")
    return m


def _guard_owners(members: List[WorkspaceMember], target: WorkspaceMember, new_role: Optional[str]) -> None:
    owners = [m for m in members if m.role == "owner"]
    if target.role == "owner" and new_role != "owner" and len(owners) == 1:
        raise HTTPException(status_code=409, detail="A workspace needs at least one owner. Make someone else an owner first.")


@router.patch("/members/{member_id}")
async def change_role(member_id: str, body: RoleChange, access: Access = Depends(require("members.manage")), db: AsyncSession = Depends(get_db_session)):
    target = await _member(db, access, member_id)
    if target.role == "owner" and access.role != "owner":
        raise HTTPException(status_code=403, detail="Only an owner can remove the owner role.")
    role = await common.assignable(db, access, body.role)
    _guard_owners(await common.members(db, access.scope.template_id), target, role.id)
    target.role = role.id
    await db.commit()
    return _member_dict(target, await common.role_names(db, access.scope.template_id))


@router.delete("/members/{member_id}", status_code=204)
async def remove_member(member_id: str, access: Access = Depends(get_access), db: AsyncSession = Depends(get_db_session)):
    """Removes someone, or lets you leave."""
    target = await _member(db, access, member_id)
    if target.user_id != access.auth.user_id and not access.can("members.manage"):
        raise HTTPException(status_code=403, detail="Your role can't remove other members.")
    if target.role == "owner" and access.role != "owner" and target.user_id != access.auth.user_id:
        raise HTTPException(status_code=403, detail="Only an owner can remove an owner.")
    _guard_owners(await common.members(db, access.scope.template_id), target, None)
    await db.delete(target)
    await db.commit()
