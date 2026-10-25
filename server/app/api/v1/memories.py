"""
What Syruss remembers, for the people it remembers it for.

Personal memories (about you, in this workspace) are visible only to you; you can edit, delete or
clear them all. Workspace memories (how the business works) are visible to every member and
managed by people who can manage knowledge.
"""
from typing import Literal, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db_session
from app.core.permissions import Access, get_access, require
from app.models.audit import AuditLog
from app.models.memory import Memory
from app.services import memory_service

router = APIRouter(prefix="/memories", tags=["Memory"], dependencies=[Depends(require("knowledge.view"))])


def _mine_or_shared(access: Access):
    return [
        Memory.template_id == access.scope.template_id,
        or_(Memory.scope == "workspace", (Memory.scope == "personal") & (Memory.user_id == access.auth.user_id)),
    ]


async def _editable(db: AsyncSession, access: Access, memory_id: str) -> Memory:
    m = await db.get(Memory, memory_id)
    if not m or m.template_id != access.scope.template_id or m.scope == "conversation":
        raise HTTPException(status_code=404, detail="Memory not found.")
    if m.scope == "personal" and m.user_id != access.auth.user_id:
        raise HTTPException(status_code=404, detail="Memory not found.")
    if m.scope == "workspace" and not access.can("knowledge.manage"):
        raise HTTPException(status_code=403, detail=f"Your role ({access.role_name}) can't change what Syruss knows about the workspace.")
    return m


def _audit(db: AsyncSession, access: Access, event: str, m: Memory) -> None:
    db.add(AuditLog(tenant_id=access.scope.tenant_id, template_id=access.scope.template_id, event_type=event,
                    entity_type="Memory", entity_id=m.id, actor=access.auth.user_id, details={"scope": m.scope, "kind": m.kind}))


@router.get("")
async def list_memories(
    scope: Literal["personal", "workspace"] = "personal",
    kind: Optional[str] = None,
    q: Optional[str] = None,
    include_archived: bool = False,
    limit: int = Query(50, ge=1, le=200),
    offset: int = 0,
    access: Access = Depends(get_access),
    db: AsyncSession = Depends(get_db_session),
):
    where = [*_mine_or_shared(access), Memory.scope == scope]
    if kind:
        where.append(Memory.kind == kind)
    if q:
        where.append(Memory.content.ilike(f"%{q.strip()}%"))
    if not include_archived:
        where.append(Memory.archived.is_(False))
    rows = (await db.execute(
        select(Memory).where(*where).order_by(Memory.pinned.desc(), Memory.importance.desc(), Memory.updated_at.desc())
        .offset(offset).limit(limit)
    )).scalars().all()
    counts = dict((await db.execute(
        select(Memory.scope, func.count(Memory.id)).where(*_mine_or_shared(access), Memory.archived.is_(False)).group_by(Memory.scope)
    )).all())
    return {
        "items": [memory_service.memory_dict(m) for m in rows],
        "counts": {"personal": counts.get("personal", 0), "workspace": counts.get("workspace", 0)},
        "can_manage_workspace": access.can("knowledge.manage"),
        "semantic_search": await memory_service.has_vectors(db),
    }


class MemoryIn(BaseModel):
    scope: Literal["personal", "workspace"]
    kind: Literal["lesson", "observation", "preference", "fact"]
    content: str = Field(..., min_length=3, max_length=2000)


@router.post("", status_code=201)
async def teach(body: MemoryIn, access: Access = Depends(get_access), db: AsyncSession = Depends(get_db_session)):
    """Tell Syruss something to remember: about you, or (with knowledge rights) about the workspace."""
    if body.scope == "workspace" and not access.can("knowledge.manage"):
        raise HTTPException(status_code=403, detail="Your role can't change what Syruss knows about the workspace.")
    if body.scope == "personal" and body.kind not in ("preference", "fact"):
        raise HTTPException(status_code=422, detail="Personal memories are preferences or facts about you.")
    m, _ = await memory_service.add(
        db, tenant_id=access.scope.tenant_id, template_id=access.scope.template_id, scope=body.scope, kind=body.kind,
        content=body.content, created_by=access.auth.user_id, importance=0.7,
        user_id=access.auth.user_id if body.scope == "personal" else None, source={"type": "person"},
    )
    _audit(db, access, "MEMORY_TAUGHT", m)
    await db.commit()
    return memory_service.memory_dict(m)


class MemoryEdit(BaseModel):
    content: Optional[str] = Field(None, min_length=3, max_length=2000)
    pinned: Optional[bool] = None
    archived: Optional[bool] = None


@router.patch("/{memory_id}")
async def edit(memory_id: str, body: MemoryEdit, access: Access = Depends(get_access), db: AsyncSession = Depends(get_db_session)):
    m = await _editable(db, access, memory_id)
    if body.content is not None and body.content.strip() != m.content:
        m.content, m.needs_embedding = body.content.strip(), True  # the agent re-embeds it
    if body.pinned is not None:
        m.pinned = body.pinned
    if body.archived is not None:
        m.archived = body.archived
    _audit(db, access, "MEMORY_EDITED", m)
    await db.commit()
    return memory_service.memory_dict(m)


@router.delete("/{memory_id}", status_code=204)
async def forget(memory_id: str, access: Access = Depends(get_access), db: AsyncSession = Depends(get_db_session)):
    m = await _editable(db, access, memory_id)
    _audit(db, access, "MEMORY_DELETED", m)
    await db.delete(m)
    await db.commit()


@router.delete("", status_code=204)
async def forget_me(scope: Literal["personal"] = "personal", access: Access = Depends(get_access), db: AsyncSession = Depends(get_db_session)):
    """Everything Syruss remembers about you in this workspace, gone."""
    await db.execute(delete(Memory).where(
        Memory.template_id == access.scope.template_id, Memory.scope == "personal", Memory.user_id == access.auth.user_id,
    ))
    db.add(AuditLog(tenant_id=access.scope.tenant_id, template_id=access.scope.template_id, event_type="MEMORY_CLEARED",
                    entity_type="Memory", entity_id=access.auth.user_id, actor=access.auth.user_id, details={"scope": scope}))
    await db.commit()
