"""Questions the workspace agent asked; answering one wakes the agent to continue its work."""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db_session
from app.core.scope import TemplateScope, get_template_scope
from app.models.audit import AuditLog
from app.models.conversation import ChatMessage, Conversation
from app.models.question import AgentQuestion, AgentQuestionStatus
from app.services.agent_events import enqueue_agent_event, agent_event_relay
from app.core.permissions import Access, require

router = APIRouter(prefix="/questions", tags=["Agent Questions"], dependencies=[Depends(require("documents.view"))])


class QuestionResponse(BaseModel):
    id: str
    template_id: str
    question: str
    context: str
    options: List[str]
    answer_type: str = "text"
    evidence_document_ids: List[str]
    # What the agent read that this is about, as a table: {"title", "columns", "rows"}
    data_table: Optional[Dict[str, Any]] = None
    target_role: str
    priority: str
    related_document_id: Optional[str] = None
    # The flag's chat thread, and whether Syruss has resolved it (an answered flag stays pending until then)
    conversation_id: Optional[str] = None
    conversation_status: Optional[str] = None
    status: AgentQuestionStatus
    answer: Optional[str] = None
    answer_choices: Optional[List[str]] = None
    answer_other: Optional[str] = None
    answered_by: Optional[str] = None
    answered_at: Optional[datetime] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AnswerRequest(BaseModel):
    answer: Optional[str] = Field(None, description="The written answer (text flags)")
    choices: List[str] = Field(default_factory=list, description="Options picked (single or multiple choice flags)")
    other: Optional[str] = Field(None, description="Their own answer, under Other")


def _answer_text(q: AgentQuestion, body: AnswerRequest) -> str:
    """Checks the answer against the flag's type and returns it as one sentence for the agent."""
    other = (body.other or "").strip()
    if q.answer_type == "text":
        text = (body.answer or other).strip()
        if not text:
            raise HTTPException(status_code=422, detail="Write an answer.")
        return text
    unknown = [c for c in body.choices if c not in q.options]
    if unknown:
        raise HTTPException(status_code=422, detail=f"Not one of the options: {', '.join(unknown)}.")
    picked = list(dict.fromkeys(body.choices))
    if q.answer_type == "single" and len(picked) + bool(other) != 1:
        raise HTTPException(status_code=422, detail="Pick one option, or write your own under Other.")
    if not picked and not other:
        raise HTTPException(status_code=422, detail="Pick at least one option, or write your own under Other.")
    return "; ".join(picked + ([f"Other: {other}"] if other else []))


class DismissRequest(BaseModel):
    reason: Optional[str] = Field(None, description="Tell the agent why, so it does not ask again")


async def _get(db: AsyncSession, scope: TemplateScope, question_id: str) -> AgentQuestion:
    q = await db.get(AgentQuestion, question_id)
    if not q or q.template_id != scope.template_id:
        raise HTTPException(status_code=404, detail="Question not found in this workspace.")
    if q.status != AgentQuestionStatus.OPEN:
        raise HTTPException(status_code=400, detail="Question is already closed.")
    return q


def _response(q: AgentQuestion, conversation_status: Optional[str]) -> QuestionResponse:
    return QuestionResponse.model_validate(q).model_copy(update={"conversation_status": conversation_status})


@router.get("", response_model=List[QuestionResponse])
async def list_questions(
    status: str = Query("OPEN", description="OPEN, ANSWERED, DISMISSED, ALL, or ACTIVE (open, or answered while Syruss is still on it)"),
    limit: int = 50,
    db: AsyncSession = Depends(get_db_session),
    scope: TemplateScope = Depends(get_template_scope),
):
    stmt = (select(AgentQuestion, Conversation.status)
            .outerjoin(Conversation, Conversation.id == AgentQuestion.conversation_id)
            .where(AgentQuestion.template_id == scope.template_id))
    wanted = status.upper()
    if wanted == "ACTIVE":
        stmt = stmt.where(or_(AgentQuestion.status == AgentQuestionStatus.OPEN,
                              (AgentQuestion.status == AgentQuestionStatus.ANSWERED) & (Conversation.status == "open")))
    elif wanted != "ALL":
        try:
            stmt = stmt.where(AgentQuestion.status == AgentQuestionStatus(wanted))
        except ValueError:
            raise HTTPException(status_code=400, detail="status must be OPEN, ANSWERED, DISMISSED, ACTIVE or ALL")
    rows = (await db.execute(stmt.order_by(AgentQuestion.created_at.desc()).limit(limit))).all()
    return [_response(q, s) for q, s in rows]


async def _close(db: AsyncSession, scope: TemplateScope, q: AgentQuestion, status: AgentQuestionStatus, answer: str,
                 choices: Optional[List[str]] = None, other: Optional[str] = None) -> None:
    q.status = status
    q.answer = answer
    q.answer_choices = choices or None
    q.answer_other = other or None
    q.answered_by = scope.username
    q.answered_at = datetime.now(timezone.utc)
    db.add(AuditLog(
        tenant_id=scope.tenant_id, template_id=scope.template_id, event_type=f"AGENT_QUESTION_{status.value}",
        entity_type="AgentQuestion", entity_id=q.id, actor=scope.user_id, details={"answer": answer},
    ))
    payload: Dict[str, Any] = {
        "question_id": q.id, "question": q.question, "context": q.context, "answer": answer,
        "answer_type": q.answer_type, "choices": choices or [], "other": other,
        "conversation_id": q.conversation_id, "conversation_kind": "flag" if q.conversation_id else None,
        "dismissed": status == AgentQuestionStatus.DISMISSED, "related_document_id": q.related_document_id,
        "evidence_document_ids": q.evidence_document_ids,
    }
    thread = await db.get(Conversation, q.conversation_id) if q.conversation_id else None
    if thread is not None:
        # The answer becomes their message in the flag's thread, where Syruss replies.
        dismissed = status == AgentQuestionStatus.DISMISSED
        db.add(ChatMessage(conversation_id=thread.id, tenant_id=scope.tenant_id, role="user",
                           content=f"Dismissed: {answer}" if dismissed else answer, evidence_citations=[], confidence=1.0,
                           agent_trace={"question_id": q.id}))
        thread.updated_at = datetime.now(timezone.utc)
        if dismissed:
            thread.status, thread.resolution, thread.resolved_at = "resolved", f"Dismissed: {answer}", datetime.now(timezone.utc)
        payload["channel_id"] = thread.id
    enqueue_agent_event(
        db, event_type="human.answered", tenant_id=scope.tenant_id, template_id=scope.template_id,
        payload=payload, actor_id=scope.user_id, actor_type="user", actor_name=scope.username,
        source="dashboard", conversation_id=thread.id if thread else None,
    )


@router.post("/{question_id}/answer", response_model=QuestionResponse)
async def answer_question(
    question_id: str,
    body: AnswerRequest,
    db: AsyncSession = Depends(get_db_session),
    scope: TemplateScope = Depends(get_template_scope),
    _access: Access = Depends(require("documents.review")),
):
    q = await _get(db, scope, question_id)
    answer = _answer_text(q, body)
    other = (body.other or "").strip() or None
    await _close(db, scope, q, AgentQuestionStatus.ANSWERED, answer, list(dict.fromkeys(body.choices)),
                 other if q.answer_type != "text" else None)
    await db.commit()
    agent_event_relay.notify()
    return _response(q, "open" if q.conversation_id else None)


@router.post("/{question_id}/dismiss", response_model=QuestionResponse)
async def dismiss_question(
    question_id: str,
    body: DismissRequest,
    db: AsyncSession = Depends(get_db_session),
    scope: TemplateScope = Depends(get_template_scope),
    _access: Access = Depends(require("documents.review")),
):
    q = await _get(db, scope, question_id)
    await _close(db, scope, q, AgentQuestionStatus.DISMISSED, body.reason or "Dismissed without a reason.")
    await db.commit()
    agent_event_relay.notify()
    return _response(q, "resolved" if q.conversation_id else None)
