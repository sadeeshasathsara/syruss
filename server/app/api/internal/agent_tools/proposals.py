"""
Proposals (changes that need a human's approval).
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from app.models.knowledge import Entity, KnowledgeProposal, KnowledgeProposalStatus, KnowledgeProposalType
from app.models.question import AgentQuestion, AgentQuestionStatus
from app.services import workspace_events
from app.services.rules.engine import check_rule_definitions
from app.services.live.ui_events import publish_ui_event
from app.api.internal.agent_tools.scope import Workspace, get_workspace
from app.api.internal.agent_tools.common import audit, document_in, proposal_dict


router = APIRouter()


class ProposalBody(BaseModel):
    proposal_type: KnowledgeProposalType
    observation: str = Field(..., description="What you noticed and why the change matters, in plain language")
    proposed_data: Dict[str, Any]
    evidence_document_ids: List[str] = Field(default_factory=list)
    evidence: Dict[str, Any] = Field(default_factory=dict, description="What you checked, e.g. results on past documents")
    channel_id: Optional[str] = None


@router.post("/proposals", status_code=201)
async def create_proposal(body: ProposalBody, ws: Workspace = Depends(get_workspace)):
    if body.proposal_type == KnowledgeProposalType.TEMPLATE_CHANGE:
        schema = body.proposed_data.get("schema_definition")
        if not isinstance(schema, dict):
            raise HTTPException(status_code=422, detail="TEMPLATE_CHANGE needs proposed_data.schema_definition (the complete new schema).")
        problems = check_rule_definitions(schema)
        if problems:
            raise HTTPException(status_code=422, detail={"message": "Proposed schema is invalid", "problems": problems})
    elif body.proposal_type == KnowledgeProposalType.ENTITY_MERGE:
        ids = [body.proposed_data.get("keep_entity_id")] + list(body.proposed_data.get("merge_entity_ids", []))
        found = (await ws.db.execute(select(func.count(Entity.id)).where(Entity.id.in_(ids), Entity.template_id == ws.template_id))).scalar()
        if found != len(set(ids)):
            raise HTTPException(status_code=422, detail="ENTITY_MERGE refers to entities outside this workspace.")
    for doc_id in body.evidence_document_ids:
        await document_in(ws, doc_id)

    # A newer proposal of the same kind supersedes older pending ones.
    for old in (await ws.db.execute(select(KnowledgeProposal).where(
        KnowledgeProposal.template_id == ws.template_id,
        KnowledgeProposal.proposal_type == body.proposal_type.value,
        KnowledgeProposal.status == KnowledgeProposalStatus.PENDING,
    ))).scalars().all():
        if body.proposal_type == KnowledgeProposalType.TEMPLATE_CHANGE:
            old.status = KnowledgeProposalStatus.REJECTED
            old.reviewer_notes = "Superseded by a newer proposal from the agent."
            old.reviewed_by = "agent"
            old.reviewed_at = datetime.now(timezone.utc)

    proposal = KnowledgeProposal(
        tenant_id=ws.tenant_id, template_id=ws.template_id, proposal_type=body.proposal_type.value,
        observation=body.observation, proposed_data=body.proposed_data,
        evidence_document_ids=body.evidence_document_ids, evidence=body.evidence, created_by="agent",
    )
    ws.db.add(proposal)
    await ws.db.flush()
    audit(ws, "PROPOSAL_CREATED", "KnowledgeProposal", proposal.id, {"type": body.proposal_type.value})
    await workspace_events.emit(
        ws.db, tenant_id=ws.tenant_id, template_id=ws.template_id, type="agent.proposal",
        title="The agent proposes a template change" if body.proposal_type == KnowledgeProposalType.TEMPLATE_CHANGE else "The agent proposes a change",
        body=body.observation[:300], path="setup" if body.proposal_type == KnowledgeProposalType.TEMPLATE_CHANGE else "agent",
        data={"proposal_id": proposal.id},
    )
    await ws.db.commit()
    await workspace_events.dispatch(ws.db)

    payload = proposal_dict(proposal)
    await publish_ui_event(f"template:{ws.template_id}", "agent_proposal", payload, tenant_id=ws.tenant_id, template_id=ws.template_id)
    if body.channel_id and body.proposal_type == KnowledgeProposalType.TEMPLATE_CHANGE:
        # The template wizard listens for this event on its session channel.
        schema = body.proposed_data["schema_definition"]
        open_q = (await ws.db.execute(select(AgentQuestion.question).where(
            AgentQuestion.template_id == ws.template_id, AgentQuestion.status == AgentQuestionStatus.OPEN,
        ))).scalars().all()
        await publish_ui_event(body.channel_id, "template_discovered", {
            "template_id": ws.template_id, "proposal_id": proposal.id, "template": schema, "schema": schema,
            "validation_rules": schema.get("validation_rules", []), "questions": list(open_q),
        }, tenant_id=ws.tenant_id, template_id=ws.template_id)
    return payload


@router.get("/proposals")
async def list_proposals(
    status: Optional[KnowledgeProposalStatus] = None, limit: int = Query(20, le=100), ws: Workspace = Depends(get_workspace),
):
    stmt = select(KnowledgeProposal).where(KnowledgeProposal.template_id == ws.template_id)
    if status:
        stmt = stmt.where(KnowledgeProposal.status == status)
    rows = (await ws.db.execute(stmt.order_by(KnowledgeProposal.created_at.desc()).limit(limit))).scalars().all()
    return [proposal_dict(p) for p in rows]
