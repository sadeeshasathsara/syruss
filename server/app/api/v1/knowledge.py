import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db_session
from app.core.scope import TemplateScope, get_template_scope
from app.schemas.conversation import ChatMessageCreate
from app.schemas.knowledge import (
    EntityCreate,
    EntityResponse,
    KnowledgeProposalResponse,
    KnowledgeProposalReviewRequest,
    UserHelperAskRequest,
    UserHelperAskResponse,
)
from app.services.conversation_service import conversation_service
from app.services.knowledge_service import knowledge_service
from app.core.permissions import Access, require

router = APIRouter(prefix="/knowledge", tags=["Workspace Knowledge"], dependencies=[Depends(require("knowledge.view"))])


@router.post("/ask", response_model=UserHelperAskResponse)
async def ask_workspace_agent(
    payload: UserHelperAskRequest,
    db: AsyncSession = Depends(get_db_session),
    scope: TemplateScope = Depends(get_template_scope),
):
    """Asks the workspace agent a question. The answer arrives on the session's live channel."""
    session_id = payload.session_id or f"help_{uuid.uuid4().hex[:8]}"
    conv = await conversation_service.get_or_create_for_channel(db, scope, session_id)
    await conversation_service.post_user_message(
        db, scope, conv, ChatMessageCreate(content=payload.question), channel_id=session_id,
    )
    return UserHelperAskResponse(session_id=session_id, conversation_id=conv.id)


@router.post("/entities", response_model=EntityResponse, status_code=201)
async def create_entity(
    payload: EntityCreate,
    db: AsyncSession = Depends(get_db_session),
    scope: TemplateScope = Depends(get_template_scope),
    _access: Access = Depends(require("knowledge.manage")),
):
    return await knowledge_service.create_entity(db, scope, payload)


@router.get("/entities", response_model=List[EntityResponse])
async def search_entities(
    query: Optional[str] = None,
    entity_type: Optional[str] = None,
    identifier_value: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db_session),
    scope: TemplateScope = Depends(get_template_scope),
):
    return await knowledge_service.search_entities(
        db, scope, query=query, entity_type=entity_type, identifier_value=identifier_value, limit=limit, offset=offset,
    )


@router.get("/entities/{entity_id}", response_model=EntityResponse)
async def get_entity(
    entity_id: str,
    db: AsyncSession = Depends(get_db_session),
    scope: TemplateScope = Depends(get_template_scope),
):
    return await knowledge_service.get_entity(db, scope, entity_id)


@router.get("/proposals", response_model=List[KnowledgeProposalResponse])
async def list_proposals(
    status: Optional[str] = "PENDING",
    db: AsyncSession = Depends(get_db_session),
    scope: TemplateScope = Depends(get_template_scope),
):
    """Changes the agent wants to make that need approval (template changes, entity creation/merges)."""
    return await knowledge_service.list_proposals(db, scope, status)


@router.post("/proposals/{proposal_id}/review", response_model=KnowledgeProposalResponse)
async def review_proposal(
    proposal_id: str,
    payload: KnowledgeProposalReviewRequest,
    db: AsyncSession = Depends(get_db_session),
    scope: TemplateScope = Depends(get_template_scope),
    _access: Access = Depends(require("knowledge.manage")),
):
    """APPROVE applies the change (e.g. activates the proposed template version); REJECT discards it."""
    return await knowledge_service.review_proposal(db, scope, proposal_id, payload.action, payload.notes)
