"""What the platform views share: names of organizations and workspaces (facts, shown to every staff member), which
organizations' content a staff member has open, and a run in the shape the views use."""
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional, Set
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.v1.sysadmin.access import Staff
from app.models.platform import PlatformAccessGrant
from app.models.tenant import Tenant
from app.models.trace import AgentExecutionTrace as Run
from app.models.workspace import Workspace


async def names(db: AsyncSession, tenant_ids: Iterable[Optional[str]] = (), workspace_ids: Iterable[Optional[str]] = ()) -> Dict[str, Dict[str, Any]]:
    """{"organizations": {id: {name, slug, status}}, "workspaces": {id: {name, slug, tenant_id}}}."""
    tids, wids = {t for t in tenant_ids if t}, {w for w in workspace_ids if w}
    workspaces = (await db.execute(select(Workspace).where(Workspace.id.in_(wids)))).scalars().all() if wids else []
    tids |= {w.tenant_id for w in workspaces}
    tenants = (await db.execute(select(Tenant).where(Tenant.id.in_(tids)))).scalars().all() if tids else []
    return {"organizations": {t.id: {"name": t.name, "slug": t.slug, "status": t.status} for t in tenants},
            "workspaces": {w.id: {"name": w.name, "slug": w.slug, "tenant_id": w.tenant_id} for w in workspaces}}


async def open_tenants(db: AsyncSession, staff: Staff) -> Set[str]:
    """The organizations whose content this staff member has open right now."""
    if not staff.can("support.access"):
        return set()
    return set((await db.execute(select(PlatformAccessGrant.tenant_id).where(
        PlatformAccessGrant.staff_id == staff.user_id, PlatformAccessGrant.ended_at.is_(None),
        PlatformAccessGrant.expires_at > datetime.now(timezone.utc)))).scalars().all())


def run_facts(r: Run) -> Dict[str, Any]:
    """A run as facts: never what it thought, read or wrote."""
    steps = r.steps or []
    return {
        "id": r.id, "run_id": r.run_id, "event_id": r.event_id, "tenant_id": r.tenant_id, "template_id": r.template_id,
        "trigger": r.profile, "status": r.status, "model": r.model, "attempt": r.attempt,
        "origin": r.origin_kind, "source": r.origin_source, "actor_type": r.actor_type,
        "document_id": r.document_id, "conversation_id": r.conversation_id,
        "queued_at": r.queued_at, "started_at": r.started_at, "completed_at": r.completed_at, "last_step_at": r.last_step_at,
        "duration_ms": r.total_duration_ms, "step": r.current_step, "tool": r.current_tool, "steps": len(steps),
        "tool_calls": sum(len(s.get("tool_calls") or []) for s in steps),
        "tool_errors": sum(1 for s in steps for c in (s.get("tool_calls") or []) if c.get("status") == "error"),
        "model_calls": r.model_calls, "input_tokens": r.input_tokens, "output_tokens": r.output_tokens, "cost_usd": r.cost_usd,
        "failed": r.status in ("FAILED", "DEAD_LETTERED", "LOST"),
    }


def step_facts(step: Dict[str, Any]) -> Dict[str, Any]:
    """One step without its thought, tool inputs or results: what was called, how long it took and whether it worked."""
    return {
        "step_number": step.get("step_number"), "step_started_at": step.get("step_started_at"),
        "step_completed_at": step.get("step_completed_at"), "thought_chars": len(step.get("thought") or ""),
        "tool_calls": [{"tool_name": c.get("tool_name"), "status": c.get("status"), "duration_ms": c.get("duration_ms")}
                       for c in (step.get("tool_calls") or [])],
    }
