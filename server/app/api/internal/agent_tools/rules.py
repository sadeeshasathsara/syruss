"""
Rules (checks in the rule language that run on every record; people add them).
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from app.models.outbox import AgentEventOutbox
from app.services import workspace_events
from app.models.rule import TemplateRule
from app.services import rule_service
from app.services.rule_engine import schema_rules
from app.api.internal.agent_tools.scope import Workspace, get_workspace
from app.api.internal.agent_tools.common import document_in, on_a_persons_request


router = APIRouter()


MAX_PENDING_RULES = 10


class RuleDraft(BaseModel):
    scope: str = Field("document", max_length=100, description='"document", or an object type to check each of its rows')
    check: str = Field(..., max_length=600)
    when: str = Field("", max_length=600)
    severity: str = "error"
    name: Optional[str] = Field(None, max_length=120)


class RuleSuggestion(rule_service.RuleIn):
    rationale: str = Field(..., min_length=1, max_length=1000, description="Why the rule is worth having: what you saw, on how many documents")
    source_document_id: Optional[str] = None


@router.get("/rules")
async def agent_list_rules(ws: Workspace = Depends(get_workspace)):
    """The workspace's rules (suggested, active, disabled, broken) and the checks that come with the template version."""
    return {"rules": [rule_service.detail(r) for r in await rule_service.list_rules(ws.db, ws.template_id)],
            "template_checks": schema_rules(ws.template.schema_definition)}


@router.post("/rules/check")
async def agent_check_rule(body: RuleDraft, ws: Workspace = Depends(get_workspace)):
    """Compiles a rule without saving it and tries it on the committed records: problems come back with where and why."""
    return await rule_service.check(ws.db, ws.template, body.model_dump(), hidden=ws.hidden)


@router.post("/rules/suggest", status_code=201)
async def agent_suggest_rule(body: RuleSuggestion, ws: Workspace = Depends(get_workspace)):
    """
    Suggests a rule. It does nothing until a person adds it (in the chat card or the Rules tab), whether you thought of it
    or a person asked for it in their chat.
    """
    pending = (await ws.db.execute(select(func.count()).select_from(TemplateRule).where(
        TemplateRule.template_id == ws.template_id, TemplateRule.status == "suggested"))).scalar_one()
    if pending >= MAX_PENDING_RULES:
        raise HTTPException(status_code=409, detail=f"{pending} rule suggestions are already waiting for a person. Wait for them to be added or dismissed.")
    if body.source_document_id:
        await document_in(ws, body.source_document_id)
    actor = await on_a_persons_request(ws)
    event = await ws.db.get(AgentEventOutbox, ws.event_id) if actor else None
    r = await rule_service.create(
        ws.db, ws.template, rule_service.RuleIn(**body.model_dump(include=set(rule_service.RuleIn.model_fields))),
        actor="agent", actor_kind="agent", status="suggested", rationale=body.rationale, source_document_id=body.source_document_id,
        source_conversation_id=event.payload.get("conversation_id") if event else None)
    if actor is None:
        await workspace_events.emit(
            ws.db, tenant_id=ws.tenant_id, template_id=ws.template_id, type="agent.rule", title="Syruss suggests a rule", body=r.name,
            path="template/rules", data={"rule": r.id})
    await ws.db.commit()
    await rule_service.announce(r, "suggested")
    await workspace_events.dispatch(ws.db)
    return rule_service.detail(r)


class RuleIdeas(BaseModel):
    ideas: List[str] = Field(..., min_length=1, max_length=rule_service.MAX_IDEAS,
                             description="Rules worth having, each one sentence the way a person would say it")


@router.post("/rules/ideas")
async def agent_rule_ideas(body: RuleIdeas, ws: Workspace = Depends(get_workspace)):
    """The ideas a rules.ideas run came up with: shown on the new-rule page as starting points (nothing is created)."""
    event = await ws.db.get(AgentEventOutbox, ws.event_id)
    if not event or event.event_type != "rules.ideas":
        raise HTTPException(status_code=403, detail="Rule ideas are given only when you were asked for them.")
    result = await rule_service.store_ideas(ws.db, ws.template, event.payload.get("version_id") or ws.template.active_version_id, body.ideas)
    await ws.db.commit()
    return result
