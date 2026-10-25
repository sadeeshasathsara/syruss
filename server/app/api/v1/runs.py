"""
The agent's run log: every time it woke up, why, for whom, what it did and what it cost.

Owners and admins see every run in a workspace (people's, API keys' and the platform's own);
other members see the runs they started. Organization admins see every workspace.
"""
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.auth import AuthContext, get_current_auth
from app.core.database import get_db_session
from app.core.permissions import Access, get_access, require
from app.models.document import Document
from app.models.trace import AgentExecutionTrace as Run
from app.models.workspace import Workspace

router = APIRouter(tags=["Agent runs"])


def run_summary(r: Run, filename: Optional[str] = None, workspace: Optional[Workspace] = None) -> Dict[str, Any]:
    out = {
        "id": r.id, "run_id": r.run_id, "trigger": r.profile, "status": r.status, "model": r.model,
        "origin": r.origin_kind, "source": r.origin_source, "actor_type": r.actor_type, "actor_id": r.actor_id,
        "actor_name": r.actor_name, "conversation_id": r.conversation_id, "document_id": r.document_id,
        "document_name": filename, "started_at": r.started_at, "duration_ms": r.total_duration_ms,
        "model_calls": r.model_calls, "input_tokens": r.input_tokens, "output_tokens": r.output_tokens,
        "cost_usd": r.cost_usd, "steps": len(r.steps or []), "error": r.error_message,
        "summary": (r.final_thought or "")[:400],
    }
    if workspace is not None:
        out["workspace"] = {"id": workspace.id, "name": workspace.name, "slug": workspace.slug}
    return out


def _filtered(stmt, origin: Optional[str], source: Optional[str], status: Optional[str], before: Optional[datetime]):
    if origin:
        stmt = stmt.where(Run.origin_kind == origin)
    if source:
        stmt = stmt.where(Run.origin_source == source)
    if status:
        stmt = stmt.where(Run.status == status)
    if before:
        stmt = stmt.where(Run.started_at < before)
    return stmt


async def _with_names(db: AsyncSession, runs: List[Run]) -> Dict[str, str]:
    ids = {r.document_id for r in runs if r.document_id}
    if not ids:
        return {}
    return dict((await db.execute(select(Document.id, Document.original_filename).where(Document.id.in_(ids)))).all())


@router.get("/runs", dependencies=[Depends(require("agent.chat"))])
async def list_runs(
    origin: Optional[Literal["user", "system"]] = None,
    source: Optional[str] = None,
    status: Optional[str] = None,
    mine: bool = False,
    before: Optional[datetime] = None,
    limit: int = Query(50, ge=1, le=200),
    access: Access = Depends(get_access),
    db: AsyncSession = Depends(get_db_session),
):
    """Newest first; `before` pages back. Members without the audit permission see their own runs."""
    sees_all = access.can("audit.view")
    stmt = _filtered(select(Run).where(Run.template_id == access.scope.template_id), origin, source, status, before)
    if mine or not sees_all:
        stmt = stmt.where(Run.actor_id == access.auth.user_id)
    runs = list((await db.execute(stmt.order_by(Run.started_at.desc()).limit(limit + 1))).scalars().all())
    names = await _with_names(db, runs)
    return {"items": [run_summary(r, names.get(r.document_id or "")) for r in runs[:limit]],
            "has_more": len(runs) > limit, "scope": "all" if sees_all and not mine else "mine"}


@router.get("/runs/{run_id}", dependencies=[Depends(require("agent.chat"))])
async def get_run(run_id: str, access: Access = Depends(get_access), db: AsyncSession = Depends(get_db_session)):
    r = await db.get(Run, run_id)
    if not r or r.template_id != access.scope.template_id or (not access.can("audit.view") and r.actor_id != access.auth.user_id):
        raise HTTPException(status_code=404, detail="Run not found.")
    names = await _with_names(db, [r])
    return {**run_summary(r, names.get(r.document_id or "")), "steps": r.steps or [], "final_thought": r.final_thought,
            "usage": r.usage, "completed_at": r.completed_at}


@router.get("/organization/runs")
async def organization_runs(
    workspace_id: Optional[str] = None,
    origin: Optional[Literal["user", "system"]] = None,
    source: Optional[str] = None,
    status: Optional[str] = None,
    before: Optional[datetime] = None,
    limit: int = Query(50, ge=1, le=200),
    auth: AuthContext = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db_session),
):
    """Every workspace's runs, for organization admins."""
    if not auth.can_org("org.audit.view"):
        raise HTTPException(status_code=403, detail="Your organization role can't see runs across workspaces.")
    stmt = _filtered(select(Run).where(Run.tenant_id == auth.tenant_id), origin, source, status, before)
    if workspace_id:
        stmt = stmt.where(Run.template_id == workspace_id)
    runs = list((await db.execute(stmt.order_by(Run.started_at.desc()).limit(limit + 1))).scalars().all())
    names = await _with_names(db, runs)
    workspaces = {w.id: w for w in (await db.execute(select(Workspace).where(Workspace.id.in_({r.template_id for r in runs})))).scalars().all()} if runs else {}
    return {"items": [run_summary(r, names.get(r.document_id or ""), workspaces.get(r.template_id)) for r in runs[:limit]],
            "has_more": len(runs) > limit}
