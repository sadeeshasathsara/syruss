"""
Helpers several parts of the agent API share.
"""
from typing import Any, Dict, Optional
from fastapi import HTTPException
from app.models.audit import AuditLog
from app.models.conversation import Conversation
from app.models.document import Document
from app.models.knowledge import Entity, KnowledgeProposal
from app.models.outbox import AgentEventOutbox
from app.models.question import AgentQuestion
from app.api.internal.agent_tools.scope import Workspace


async def document_in(ws: Workspace, document_id: str) -> Document:
    doc = await ws.db.get(Document, document_id)
    if not doc or doc.template_id != ws.template_id or doc.tenant_id != ws.tenant_id:
        raise HTTPException(status_code=404, detail="Document not found in this workspace.")
    return doc


def audit(ws: Workspace, event_type: str, entity_type: str, entity_id: str, details: Dict[str, Any]) -> None:
    ws.db.add(AuditLog(
        tenant_id=ws.tenant_id, template_id=ws.template_id, event_type=event_type,
        entity_type=entity_type, entity_id=entity_id, actor="agent", details=details, event_id=ws.event_id,
    ))


def question_dict(q: AgentQuestion) -> Dict[str, Any]:
    return {
        "id": q.id, "question": q.question, "context": q.context, "options": q.options, "answer_type": q.answer_type,
        "status": q.status.value, "answer": q.answer, "answer_choices": q.answer_choices, "answer_other": q.answer_other,
        "answered_by": q.answered_by,
        "target_role": q.target_role, "priority": q.priority,
        "related_document_id": q.related_document_id, "evidence_document_ids": q.evidence_document_ids,
        "data_table": q.data_table, "conversation_id": q.conversation_id,
        "created_at": q.created_at.isoformat() if q.created_at else None,
    }


def proposal_dict(p: KnowledgeProposal) -> Dict[str, Any]:
    return {
        "id": p.id, "proposal_type": p.proposal_type, "observation": p.observation,
        "proposed_data": p.proposed_data, "status": p.status.value, "reviewer_notes": p.reviewer_notes,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


def entity_dict(e: Entity) -> Dict[str, Any]:
    return {
        "id": e.id, "entity_type": e.entity_type, "name": e.name,
        "identifier_key": e.identifier_key, "identifier_value": e.identifier_value,
        "attributes": e.attributes, "aliases": [a.alias for a in e.aliases], "created_by": e.created_by,
    }


def conversation_dict(c: Conversation) -> Dict[str, Any]:
    return {"id": c.id, "title": c.title, "kind": c.kind, "status": c.status, "audience": c.audience,
            "document_ids": (c.context_metadata or {}).get("document_ids", []), "resolution": c.resolution,
            "updated_at": c.updated_at.isoformat() if c.updated_at else None}


async def on_a_persons_request(ws: Workspace) -> Optional[Dict[str, Any]]:
    """Who the agent is acting for in a private chat (screens it builds there are theirs to keep), else None."""
    event = await ws.db.get(AgentEventOutbox, ws.event_id)
    if event and event.event_type == "human.message" and event.payload.get("conversation_kind") == "chat" and (event.actor or {}).get("type") == "user":
        return event.actor
    return None
