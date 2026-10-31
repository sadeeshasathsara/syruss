from typing import Literal, Optional
from fastapi import APIRouter, Depends, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db_session
from app.core.permissions import require
from app.core.scope import TemplateScope, get_template_scope
from app.schemas.conversation import (
    ChatMessageCreate,
    ChatMessageResponse,
    ConversationCreate,
    ConversationResponse,
    ConversationUpdate,
)
from app.services.conversation_service import conversation_service

router = APIRouter(prefix="/conversations", tags=["Workspace Chat"], dependencies=[Depends(require("agent.chat"))])


@router.get("")
async def list_conversations(
    kind: Literal["chat", "agent", "history"] = "chat",
    status: Optional[Literal["open", "resolved"]] = None,
    limit: int = Query(20, ge=1, le=50),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_session),
    scope: TemplateScope = Depends(get_template_scope),
):
    """
    A page of conversations, newest first: their ids and titles only, never their messages.
    kind "chat": your chats. kind "agent": conversations Syruss opened with people in your role.
    kind "history": both, for the chat history.
    """
    result = await conversation_service.list_conversations(db, scope, kind, limit, offset, status)
    if kind in ("agent", "history"):
        await db.commit()  # resolving the role may have made the first visitor the owner
    return result


@router.post("", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    payload: Optional[ConversationCreate] = None,
    db: AsyncSession = Depends(get_db_session),
    scope: TemplateScope = Depends(get_template_scope),
):
    return await conversation_service.create_conversation(db, scope, payload)


@router.get("/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(
    conversation_id: str,
    db: AsyncSession = Depends(get_db_session),
    scope: TemplateScope = Depends(get_template_scope),
):
    return await conversation_service.get_conversation(db, scope, conversation_id)


@router.patch("/{conversation_id}", response_model=ConversationResponse)
async def update_conversation(
    conversation_id: str,
    payload: ConversationUpdate,
    db: AsyncSession = Depends(get_db_session),
    scope: TemplateScope = Depends(get_template_scope),
):
    return await conversation_service.update_conversation(db, scope, conversation_id, payload)


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: str,
    db: AsyncSession = Depends(get_db_session),
    scope: TemplateScope = Depends(get_template_scope),
):
    await conversation_service.delete_conversation(db, scope, conversation_id)


@router.post("/{conversation_id}/messages", response_model=ChatMessageResponse)
async def post_message(
    conversation_id: str,
    payload: ChatMessageCreate,
    wait: bool = True,
    db: AsyncSession = Depends(get_db_session),
    scope: TemplateScope = Depends(get_template_scope),
):
    """
    Sends a message to the workspace agent. With `wait=false` it returns the stored message at
    once and the agent's work and reply arrive live on the workspace channel.
    """
    if not wait:
        conv = await conversation_service.get_conversation(db, scope, conversation_id)
        return await conversation_service.post_user_message(db, scope, conv, payload)
    return await conversation_service.send_and_wait(db, scope, conversation_id, payload)


@router.post("/{conversation_id}/stop")
async def stop_reply(
    conversation_id: str,
    db: AsyncSession = Depends(get_db_session),
    scope: TemplateScope = Depends(get_template_scope),
):
    """Stops Syruss's reply in this conversation (queued or under way)."""
    return await conversation_service.stop_reply(db, scope, conversation_id)


@router.post("/{conversation_id}/messages/stream")
async def stream_message(
    conversation_id: str,
    payload: ChatMessageCreate,
    db: AsyncSession = Depends(get_db_session),
    scope: TemplateScope = Depends(get_template_scope),
):
    """Sends a message and streams the agent's thoughts and reply as Server-Sent Events."""
    return StreamingResponse(
        conversation_service.stream_reply(db, scope, conversation_id, payload),
        media_type="text/event-stream",
    )
