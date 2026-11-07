"""
Knowledge sources (reference data: the agent sees the records, never the credentials).
"""
from datetime import datetime, timezone
from typing import Any, Dict
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from app.models.knowledge import Entity, KnowledgeProposal, KnowledgeProposalStatus, KnowledgeProposalType
from app.models.knowledge_source import KnowledgeSource
from app.services import source_service, workspace_events
from app.services.source_data import record_sets
from app.services.live.ui_events import publish_ui_event
from app.api.internal.agent_tools.scope import Workspace, get_workspace
from app.api.internal.agent_tools.common import audit, proposal_dict


router = APIRouter()


async def _knowledge_source(ws: Workspace, source_id: str) -> KnowledgeSource:
    source = await ws.db.get(KnowledgeSource, source_id)
    if not source or source.template_id != ws.template_id:
        raise HTTPException(status_code=404, detail="Source not found in this workspace.")
    return source


@router.get("/sources/{source_id}")
async def get_knowledge_source(source_id: str, ws: Workspace = Depends(get_workspace)):
    source = await _knowledge_source(ws, source_id)
    counts = dict((await ws.db.execute(
        select(Entity.entity_type, func.count(Entity.id)).where(Entity.template_id == ws.template_id).group_by(Entity.entity_type)
    )).all())
    objects = source_service.identified_objects(ws.template.schema_definition)
    return {
        "id": source.id, "name": source.name, "kind": source.kind, "notes": source.notes, "status": source.status,
        "last_error": source.last_error, "current_mapping": source.mapping,
        "record_sets": record_sets(source.data) if source.data is not None else [],
        "identified_objects": [{"type": t, **o, "known_entities": counts.get(t, 0)} for t, o in objects.items()],
    }


class MappingBody(BaseModel):
    mapping: Dict[str, Any] = Field(..., description="{records_path, objects: [{entity_type, id_column, name_column, attributes, alias_columns}]}")
    observation: str = Field(..., description="What the data is and why it maps this way, in plain language")
    dry_run: bool = Field(False, description="Only check the mapping and show what the import would do")


@router.post("/sources/{source_id}/mapping")
async def propose_source_mapping(source_id: str, body: MappingBody, ws: Workspace = Depends(get_workspace)):
    source = await _knowledge_source(ws, source_id)
    problems, rows = source_service.check_mapping(ws.template.schema_definition, source.data, body.mapping)
    if problems:
        raise HTTPException(status_code=422, detail={"message": "The mapping does not fit", "problems": problems})
    preview = await source_service.preview(ws.db, ws.template_id, rows, body.mapping)
    if body.dry_run:
        return {"preview": preview}

    # A newer mapping for the same source replaces a pending one.
    for old in (await ws.db.execute(select(KnowledgeProposal).where(
        KnowledgeProposal.template_id == ws.template_id,
        KnowledgeProposal.proposal_type == KnowledgeProposalType.SOURCE_MAPPING.value,
        KnowledgeProposal.status == KnowledgeProposalStatus.PENDING,
    ))).scalars().all():
        if (old.proposed_data or {}).get("source_id") == source.id:
            old.status = KnowledgeProposalStatus.REJECTED
            old.reviewer_notes = "Superseded by a newer mapping from the agent."
            old.reviewed_by = "agent"
            old.reviewed_at = datetime.now(timezone.utc)
    proposal = KnowledgeProposal(
        tenant_id=ws.tenant_id, template_id=ws.template_id, proposal_type=KnowledgeProposalType.SOURCE_MAPPING.value,
        observation=body.observation[:2000], created_by="agent",
        proposed_data={"source_id": source.id, "source_name": source.name, "mapping": body.mapping, "preview": preview},
    )
    ws.db.add(proposal)
    source.status = "REVIEW"
    await ws.db.flush()
    audit(ws, "PROPOSAL_CREATED", "KnowledgeProposal", proposal.id, {"type": proposal.proposal_type, "source_id": source.id})
    await workspace_events.emit(
        ws.db, tenant_id=ws.tenant_id, template_id=ws.template_id, type="agent.proposal",
        title=f"A mapping for '{source.name}' is ready to review", body=body.observation[:300],
        path="knowledge?tab=sources", data={"proposal_id": proposal.id, "source_id": source.id},
    )
    await ws.db.commit()
    await workspace_events.dispatch(ws.db)
    payload = proposal_dict(proposal)
    await publish_ui_event(f"template:{ws.template_id}", "agent_proposal", payload, tenant_id=ws.tenant_id, template_id=ws.template_id)
    return {"proposal": payload, "preview": preview}
