"""
Traces: the record of each run.
"""
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from app.models.outbox import AgentEventOutbox
from app.models.trace import AgentExecutionTrace
from app.services import budget_service, workspace_events
from app.services.live import platform_feed
from app.api.internal.agent_tools.scope import Workspace, get_workspace
from app.api.internal.agent_tools.common import document_in


router = APIRouter()


# A run is written when it starts, after every step, and when it ends; the platform watch marks one that stops
# reporting as LOST (services/run_watch.py). Each write is also a fact on the platform's live view.

class TraceStart(BaseModel):
    run_id: str = Field(..., min_length=1, max_length=64)
    trigger: str
    model: str
    document_id: Optional[str] = None
    origin: Dict[str, Any] = Field(default_factory=dict, description="The event's actor: type, id, name, origin, source, conversation_id")
    attempt: int = Field(1, ge=1)


class TraceProgress(BaseModel):
    steps: List[Dict[str, Any]] = Field(default_factory=list, description="New or changed steps; a step replaces the one with its number")
    usage: Dict[str, Any] = Field(default_factory=dict)
    model_calls: int = 0
    cost_usd: Optional[float] = None


class TraceBody(BaseModel):
    trigger: str
    model: str
    status: str
    started_at: datetime
    completed_at: datetime
    steps: List[Dict[str, Any]] = Field(default_factory=list)
    final_thought: Optional[str] = None
    usage: Dict[str, Any] = Field(default_factory=dict)
    error_message: Optional[str] = None
    document_id: Optional[str] = None
    run_id: Optional[str] = None
    origin: Dict[str, Any] = Field(default_factory=dict, description="The event's actor: type, id, name, origin, source, conversation_id")
    model_calls: int = 0
    cost_usd: Optional[float] = None
    attempt: int = Field(1, ge=1)


def _origin(trace: AgentExecutionTrace, origin: Dict[str, Any]) -> None:
    trace.origin_kind, trace.origin_source = origin.get("origin") or "system", origin.get("source") or "system"
    trace.actor_type, trace.actor_id, trace.actor_name = origin.get("type") or "system", origin.get("id"), origin.get("name")
    trace.conversation_id = origin.get("conversation_id")


def _usage(trace: AgentExecutionTrace, usage: Dict[str, Any], model_calls: int, cost_usd: Optional[float]) -> None:
    trace.usage, trace.model_calls, trace.cost_usd = usage, model_calls, cost_usd
    trace.input_tokens, trace.output_tokens = int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)


def run_fact(trace: AgentExecutionTrace) -> Dict[str, Any]:
    """What the platform's live view says about a run: facts only (sysadmin/live)."""
    return {
        "run_id": trace.run_id, "trace_id": trace.id, "trigger": trace.profile, "status": trace.status, "model": trace.model,
        "document_id": trace.document_id, "conversation_id": trace.conversation_id, "origin": trace.origin_kind,
        "source": trace.origin_source, "actor_type": trace.actor_type, "attempt": trace.attempt,
        "started_at": trace.started_at.isoformat() if trace.started_at else None,
        "queued_at": trace.queued_at.isoformat() if trace.queued_at else None,
        "step": trace.current_step, "tool": trace.current_tool, "model_calls": trace.model_calls,
        "input_tokens": trace.input_tokens, "output_tokens": trace.output_tokens, "cost_usd": trace.cost_usd,
        "duration_ms": trace.total_duration_ms,
    }


async def _run(ws: Workspace, run_id: str) -> Optional[AgentExecutionTrace]:
    return (await ws.db.execute(select(AgentExecutionTrace).where(
        AgentExecutionTrace.run_id == run_id, AgentExecutionTrace.template_id == ws.template_id).limit(1))).scalar_one_or_none()


@router.post("/traces/start", status_code=201)
async def start_trace(body: TraceStart, ws: Workspace = Depends(get_workspace)):
    """The run has begun: it shows as running everywhere at once."""
    if body.document_id:
        await document_in(ws, body.document_id)
    trace = await _run(ws, body.run_id)
    if trace is None:
        event = await ws.db.get(AgentEventOutbox, ws.event_id) if ws.event_id else None
        now = datetime.now(timezone.utc)
        trace = AgentExecutionTrace(
            id=str(uuid.uuid4()), tenant_id=ws.tenant_id, template_id=ws.template_id, document_id=body.document_id,
            profile=body.trigger, model=body.model, status="RUNNING", started_at=now, last_step_at=now, steps=[], usage={},
            run_id=body.run_id, event_id=ws.event_id, attempt=body.attempt, queued_at=event.created_at if event else None,
        )
        _origin(trace, body.origin)
        ws.db.add(trace)
        await ws.db.commit()
    await platform_feed.publish_fact("run.started", tenant_id=ws.tenant_id, template_id=ws.template_id, **run_fact(trace))
    return {"id": trace.id}


@router.patch("/traces/{run_id}")
async def trace_progress(run_id: str, body: TraceProgress, ws: Workspace = Depends(get_workspace)):
    """A step is done: its record is saved now, so a crash later still leaves it, and the run is known to be alive."""
    trace = await _run(ws, run_id)
    if trace is None:
        raise HTTPException(status_code=404, detail="No run with that id has started in this workspace.")
    if trace.status != "RUNNING":
        return {"id": trace.id, "status": trace.status}  # finished (or given up as lost) meanwhile: its record stands
    steps = {s.get("step_number"): s for s in (trace.steps or [])}
    for step in body.steps:
        steps[step.get("step_number")] = step
    trace.steps = [steps[k] for k in sorted(steps, key=lambda n: n or 0)]
    last = trace.steps[-1] if trace.steps else {}
    trace.current_step = last.get("step_number")
    trace.current_tool = ((last.get("tool_calls") or [{}])[-1].get("tool_name") or None) if last.get("tool_calls") else None
    trace.last_step_at = datetime.now(timezone.utc)
    _usage(trace, body.usage, body.model_calls, body.cost_usd)
    await ws.db.commit()
    await platform_feed.publish_fact("run.step", tenant_id=ws.tenant_id, template_id=ws.template_id, **run_fact(trace))
    return {"id": trace.id, "status": trace.status}


@router.post("/traces", status_code=201)
async def report_trace(body: TraceBody, ws: Workspace = Depends(get_workspace)):
    """The run is over: finishes the run that started (or writes it whole, for a run that never reported its start)."""
    if body.document_id:
        await document_in(ws, body.document_id)
    trace = await _run(ws, body.run_id) if body.run_id else None
    if trace is None:
        trace = AgentExecutionTrace(id=str(uuid.uuid4()), tenant_id=ws.tenant_id, template_id=ws.template_id,
                                    run_id=body.run_id, event_id=ws.event_id, attempt=body.attempt)
        event = await ws.db.get(AgentEventOutbox, ws.event_id) if ws.event_id else None
        trace.queued_at = event.created_at if event else None
        ws.db.add(trace)
    trace.document_id, trace.profile, trace.model, trace.status = body.document_id, body.trigger, body.model, body.status
    trace.started_at, trace.completed_at = trace.started_at or body.started_at, body.completed_at
    trace.total_duration_ms = (body.completed_at - trace.started_at).total_seconds() * 1000
    trace.steps, trace.final_thought, trace.error_message = body.steps, body.final_thought, body.error_message
    trace.current_tool, trace.last_step_at = None, body.completed_at
    trace.current_step = len(body.steps) or None
    _origin(trace, body.origin)
    _usage(trace, body.usage, body.model_calls, body.cost_usd)
    await ws.db.flush()
    await budget_service.check_alerts(ws.db, tenant_id=ws.tenant_id, template_id=ws.template_id)
    await ws.db.commit()
    await workspace_events.dispatch(ws.db)
    budget_service.forget(ws.template_id)
    await platform_feed.publish_fact("run.finished", tenant_id=ws.tenant_id, template_id=ws.template_id, **run_fact(trace),
                                     error=(body.error_message or "")[:300] or None)
    return {"id": trace.id}
