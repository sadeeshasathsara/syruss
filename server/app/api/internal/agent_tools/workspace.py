"""
Workspace overview: what the agent reads first about the workspace it works in.
"""
from typing import Any, Dict
from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from app.models.conversation import Conversation
from app.models.document import Document, DocumentRole
from app.models.knowledge import Entity, KnowledgeProposal, KnowledgeProposalStatus
from app.models.knowledge_source import KnowledgeSource
from app.models.question import AgentQuestion, AgentQuestionStatus
from app.services import budget_service, settings_service
from app.services import rule_service
from app.services.template_service import template_service
from app.api.internal.agent_tools.scope import Workspace, get_workspace
from app.api.internal.agent_tools.common import conversation_dict, proposal_dict, question_dict


router = APIRouter()


@router.get("/workspace")
async def get_workspace_overview(ws: Workspace = Depends(get_workspace)):
    db = ws.db
    tpl = ws.template
    active = await template_service.get_active_version(db, tpl)
    status_counts = dict((await db.execute(
        select(Document.status, func.count(Document.id))
        .where(Document.template_id == ws.template_id, Document.role == DocumentRole.DOCUMENT)
        .group_by(Document.status)
    )).all())
    entity_types = (await db.execute(
        select(Entity.entity_type, func.count(Entity.id))
        .where(Entity.template_id == ws.template_id).group_by(Entity.entity_type)
    )).all()
    open_questions = (await db.execute(
        select(AgentQuestion).where(AgentQuestion.template_id == ws.template_id, AgentQuestion.status == AgentQuestionStatus.OPEN)
        .order_by(AgentQuestion.created_at.desc()).limit(20)
    )).scalars().all()
    pending = (await db.execute(
        select(KnowledgeProposal).where(KnowledgeProposal.template_id == ws.template_id, KnowledgeProposal.status == KnowledgeProposalStatus.PENDING)
        .order_by(KnowledgeProposal.created_at.desc()).limit(20)
    )).scalars().all()
    sources = (await db.execute(
        select(KnowledgeSource).where(KnowledgeSource.template_id == ws.template_id).order_by(KnowledgeSource.created_at.desc())
    )).scalars().all()
    conversations = (await db.execute(
        select(Conversation).where(Conversation.template_id == ws.template_id, Conversation.kind == "agent",
                                   Conversation.status == "open").order_by(Conversation.updated_at.desc()).limit(20)
    )).scalars().all()
    return {
        "template": {
            "id": tpl.id, "name": tpl.name, "document_type": tpl.document_type, "description": tpl.description,
            "schema_definition": tpl.schema_definition,
            "active_version": {"id": active.id, "version": active.version} if active else None,
        },
        "documents_by_status": {k.value: v for k, v in status_counts.items()},
        "entity_types": [{"entity_type": t, "count": c} for t, c in entity_types],
        "open_questions": [question_dict(q) for q in open_questions],
        "pending_proposals": [proposal_dict(p) for p in pending],
        "open_conversations": [conversation_dict(c) for c in conversations],
        "knowledge_sources": [{"id": s.id, "name": s.name, "kind": s.kind, "status": s.status} for s in sources],
        "rules": [{k: v for k, v in rule_service.detail(r).items() if k in ("id", "name", "scope", "check", "when", "severity", "status")}
                  for r in await rule_service.list_rules(db, ws.template_id, ["active", "suggested", "broken"])],
        "workspace_policy": await _policy(ws),
    }


async def _policy(ws: Workspace) -> Dict[str, Any]:
    prefs = await settings_service.get_settings(ws.db, ws.template_id)
    return {
        "instructions": prefs.agent.instructions,
        "commit_only_when_at_least": prefs.documents.commit_threshold,
        "may_register_entities": prefs.agent.may_register_entities,
        "may_learn_aliases": prefs.agent.may_learn_aliases,
        "budget": {k: v for k, v in (await budget_service.status(ws.db, ws.template_id)).items()
                   if k in ("state", "share", "limits")},
    }
