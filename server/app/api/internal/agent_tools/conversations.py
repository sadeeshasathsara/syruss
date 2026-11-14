"""
Talking to humans: questions, threads the agent opens, replies in chat, and a chat's title and summary.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from app.models.conversation import Conversation, ChatMessage
from app.models.outbox import AgentEventOutbox
from app.models.question import AgentQuestion, AgentQuestionStatus
from app.services import memory_service, workspace_events
from app.services.display_names import name_documents
from app.services.live.ui_events import publish_ui_event
from app.api.internal.agent_tools.scope import Workspace, get_workspace
from app.api.internal.agent_tools.common import audit, conversation_dict, document_in, question_dict


router = APIRouter()


class DataTable(BaseModel):
    """Rows the agent read, for a person to look at beside a question: a few columns, one list of cells per row."""
    title: Optional[str] = Field(None, max_length=120)
    columns: List[str] = Field(..., min_length=1, max_length=12)
    rows: List[List[Any]] = Field(..., min_length=1, max_length=80)

    def cleaned(self) -> Dict[str, Any]:
        width = len(self.columns)
        cell = lambda v: "" if v is None else str(v).strip()[:200]  # noqa: E731
        return {"title": (self.title or "").strip() or None, "columns": [cell(c) for c in self.columns],
                "rows": [[cell(c) for c in (r + [""] * width)[:width]] for r in self.rows]}


def _markdown_table(t: Dict[str, Any]) -> str:
    esc = lambda v: str(v).replace("|", "\\|").replace("\n", " ")  # noqa: E731
    lines = ["| " + " | ".join(esc(c) for c in t["columns"]) + " |", "|" + "---|" * len(t["columns"])]
    lines += ["| " + " | ".join(esc(c) for c in r) + " |" for r in t["rows"]]
    return "\n".join(lines)


class QuestionBody(BaseModel):
    question: str = Field(..., description="One short question, for the person who answers it")
    context: str = Field(..., description="For the person who answers: what they need to know to answer, in a sentence or two")
    answer_type: str = Field("text", pattern="^(text|single|multiple)$",
                             description="text: a written answer; single: one of the options; multiple: any of them")
    options: List[str] = Field(default_factory=list, description="Choices for single/multiple; 'Other' is always added")
    evidence_document_ids: List[str] = Field(default_factory=list)
    data_table: Optional[DataTable] = Field(None, description="The data the question is about, as a table")
    target_role: str = Field("admin", pattern="^(admin|reviewer)$")
    priority: str = Field("normal", pattern="^(low|normal|high)$")
    related_document_id: Optional[str] = None
    channel_id: Optional[str] = Field(None, description="Also show the question on this live channel")


@router.post("/questions", status_code=201)
async def flag(body: QuestionBody, ws: Workspace = Depends(get_workspace)):
    """A flag: one quick decision for a person. Choices always come with "Other" (their own answer)."""
    body.options = [o.strip() for o in body.options if o.strip() and o.strip().lower() not in ("other", "other:")]
    if body.answer_type != "text" and len(body.options) < 2:
        raise HTTPException(status_code=422, detail="Give at least two options for a single or multiple choice flag, "
                                                    "or ask for a text answer.")
    for doc_id in set(body.evidence_document_ids + ([body.related_document_id] if body.related_document_id else [])):
        await document_in(ws, doc_id)
    duplicate = (await ws.db.execute(select(AgentQuestion).where(
        AgentQuestion.template_id == ws.template_id, AgentQuestion.status == AgentQuestionStatus.OPEN,
        func.lower(AgentQuestion.question) == body.question.strip().lower(),
    ))).scalar_one_or_none()
    if duplicate:
        return {**question_dict(duplicate), "duplicate": True}
    # People read both: documents by their names, not ids or file names.
    body.question = await name_documents(ws.db, ws.template_id, body.question.strip())
    body.context = await name_documents(ws.db, ws.template_id, body.context)
    q = AgentQuestion(
        tenant_id=ws.tenant_id, template_id=ws.template_id, question=body.question, context=body.context,
        options=body.options, answer_type=body.answer_type, evidence_document_ids=body.evidence_document_ids,
        data_table=body.data_table.cleaned() if body.data_table else None, target_role=body.target_role,
        priority=body.priority, related_document_id=body.related_document_id,
    )
    ws.db.add(q)
    await ws.db.flush()
    # Its own chat thread: the answer is posted there, and Syruss follows up there until it resolves it.
    documents = list(dict.fromkeys(([body.related_document_id] if body.related_document_id else []) + body.evidence_document_ids))
    thread = Conversation(
        tenant_id=ws.tenant_id, template_id=ws.template_id, user_id="agent", kind="flag", status="open",
        audience=body.target_role, title=q.question[:120],
        context_metadata={"document_ids": documents, "question_id": q.id, "opened_by_event": ws.event_id},
    )
    ws.db.add(thread)
    await ws.db.flush()
    choices = "".join(f"\n- {o}" for o in q.options) + ("\n- Other" if q.options else "")
    table = f"\n\n{_markdown_table(q.data_table)}\n" if q.data_table else ""
    ws.db.add(ChatMessage(conversation_id=thread.id, tenant_id=ws.tenant_id, role="assistant",
                          content=f"**{q.question}**\n\n{q.context}{table}{choices}", evidence_citations=[], confidence=1.0,
                          agent_trace={"event_id": ws.event_id, "question_id": q.id}))
    q.conversation_id = thread.id
    audit(ws, "AGENT_QUESTION_ASKED", "AgentQuestion", q.id, {"question": q.question})
    await workspace_events.emit(
        ws.db, tenant_id=ws.tenant_id, template_id=ws.template_id, type="agent.question",
        severity="warning" if q.priority == "high" else "info", title="The agent has a question", body=q.question,
        path=f"review?flag={q.id}", data={"question_id": q.id},
    )
    await ws.db.commit()
    await workspace_events.dispatch(ws.db)
    payload = question_dict(q)
    await publish_ui_event(f"template:{ws.template_id}", "agent_question", payload, tenant_id=ws.tenant_id, template_id=ws.template_id)
    if body.channel_id:
        await publish_ui_event(body.channel_id, "agent_question", payload, tenant_id=ws.tenant_id, template_id=ws.template_id)
    return payload


@router.get("/questions")
async def list_questions(
    status: Optional[AgentQuestionStatus] = None, limit: int = Query(20, le=100), ws: Workspace = Depends(get_workspace),
):
    stmt = select(AgentQuestion).where(AgentQuestion.template_id == ws.template_id)
    if status:
        stmt = stmt.where(AgentQuestion.status == status)
    rows = (await ws.db.execute(stmt.order_by(AgentQuestion.created_at.desc()).limit(limit))).scalars().all()
    return [question_dict(q) for q in rows]


class ChatReplyBody(BaseModel):
    content: str
    reply_to: Optional[str] = Field(None, description="The user message id this answers")
    evidence_citations: List[Dict[str, Any]] = Field(default_factory=list)
    confidence: float = 1.0
    channel_id: Optional[str] = Field(None, description="Live channel to deliver on (defaults to the conversation id)")
    actions: List[str] = Field(default_factory=list, max_length=20,
                               description="What the run did before this reply (built a screen, suggested a rule...), so later "
                                           "runs in this conversation know it")


# Who an agent conversation is for, by the permission their role must have.
AUDIENCE_PERMISSION = {"admin": "template.publish", "reviewer": "documents.review"}


async def _agent_conversation(ws: Workspace, conversation_id: str) -> Conversation:
    conv = await ws.db.get(Conversation, conversation_id)
    if not conv or conv.template_id != ws.template_id or conv.kind not in ("agent", "flag"):
        raise HTTPException(status_code=404, detail="No conversation you opened has this id.")
    return conv


class OpenConversationBody(BaseModel):
    title: str = Field(..., min_length=3, max_length=120, description="The issue, in a few words")
    message: str = Field(..., min_length=1, description="Your opening message: what you found, what you need")
    audience: str = Field("admin", pattern="^(admin|reviewer)$")
    document_ids: List[str] = Field(default_factory=list, description="Documents it is about")
    priority: str = Field("normal", pattern="^(low|normal|high)$")


@router.post("/conversations", status_code=201)
async def open_conversation(body: OpenConversationBody, ws: Workspace = Depends(get_workspace)):
    """Opens a conversation with the people who can settle an issue. It shows in their agent inbox."""
    for doc_id in body.document_ids:
        await document_in(ws, doc_id)
    same = (await ws.db.execute(select(Conversation).where(
        Conversation.template_id == ws.template_id, Conversation.kind == "agent", Conversation.status == "open",
        func.lower(Conversation.title) == body.title.strip().lower(),
    ))).scalars().first()
    if same:
        return {**conversation_dict(same), "already_open": True,
                "note": "You already have an open conversation about this; continue it with reply_to_user."}
    body.message = await name_documents(ws.db, ws.template_id, body.message)
    conv = Conversation(
        tenant_id=ws.tenant_id, template_id=ws.template_id, user_id="agent", kind="agent", status="open",
        audience=body.audience, title=body.title.strip(),
        context_metadata={"document_ids": body.document_ids, "priority": body.priority, "opened_by_event": ws.event_id},
    )
    ws.db.add(conv)
    await ws.db.flush()
    ws.db.add(ChatMessage(conversation_id=conv.id, tenant_id=ws.tenant_id, role="assistant", content=body.message,
                          evidence_citations=[], confidence=1.0, agent_trace={"event_id": ws.event_id}))
    audit(ws, "AGENT_CONVERSATION_OPENED", "Conversation", conv.id, {"title": conv.title, "audience": body.audience})
    await workspace_events.emit(
        ws.db, tenant_id=ws.tenant_id, template_id=ws.template_id, type="agent.conversation",
        severity="warning" if body.priority == "high" else "info", title=f"Syruss: {conv.title}",
        body=body.message[:300], path=f"agent?conversation={conv.id}", data={"conversation_id": conv.id},
        permission=AUDIENCE_PERMISSION[body.audience],
    )
    await ws.db.commit()
    await workspace_events.dispatch(ws.db)
    await publish_ui_event(f"template:{ws.template_id}", "agent_conversation", conversation_dict(conv),
                           tenant_id=ws.tenant_id, template_id=ws.template_id)
    return conversation_dict(conv)


class WorkingNoteBody(BaseModel):
    note: str = Field("", max_length=4000, description="Where you left off: what you were doing and what you wait for")
    document_ids: List[str] = Field(default_factory=list, max_length=200, description="The documents you worked on")


@router.get("/conversations/{conversation_id}/state")
async def get_working_note(conversation_id: str, ws: Workspace = Depends(get_workspace)):
    """The note you left in a conversation you opened, of where you left off (kept until it is resolved)."""
    conv = await _agent_conversation(ws, conversation_id)
    state = conv.agent_state if conv.agent_state and "note" in conv.agent_state else None   # older saved runs are not resumed
    return {**conversation_dict(conv), "state": state}


@router.put("/conversations/{conversation_id}/state")
async def save_working_note(conversation_id: str, body: WorkingNoteBody, ws: Workspace = Depends(get_workspace)):
    conv = await _agent_conversation(ws, conversation_id)
    if conv.status != "open":
        return {"saved": False, "reason": "The conversation is resolved; its state is no longer kept."}
    conv.agent_state = {"note": body.note, "document_ids": body.document_ids,
                        "saved_at": datetime.now(timezone.utc).isoformat(), "event_id": ws.event_id}
    await ws.db.commit()
    return {"saved": True}


class ResolveBody(BaseModel):
    resolution: str = Field(..., min_length=3, description="What was decided or done")


async def _waking_conversation(ws: Workspace) -> Optional[str]:
    """The conversation a person wrote in to wake this run (a chat message, or an answer in a flag's thread), if any."""
    event = await ws.db.get(AgentEventOutbox, ws.event_id) if ws.event_id else None
    if event is None or event.event_type not in ("human.message", "human.answered"):
        return None
    return (event.payload or {}).get("conversation_id")


async def _replied_in(ws: Workspace, conv: Conversation) -> bool:
    rows = (await ws.db.execute(select(ChatMessage.agent_trace).where(
        ChatMessage.conversation_id == conv.id, ChatMessage.role == "assistant"))).scalars().all()
    return any((trace or {}).get("event_id") == ws.event_id for trace in rows)


@router.post("/conversations/{conversation_id}/resolve")
async def resolve_conversation(conversation_id: str, body: ResolveBody, ws: Workspace = Depends(get_workspace)):
    """Marks a conversation you opened as resolved. Its saved working state is cleared. When a person's message
    woke you in it, reply to them first: the resolution note comes after your answer, not before it."""
    conv = await _agent_conversation(ws, conversation_id)
    if await _waking_conversation(ws) == conv.id and not await _replied_in(ws, conv):
        raise HTTPException(status_code=409, detail=(
            "Reply to them in this conversation first (reply_to_user: what you did with their answer), then resolve it."))
    body.resolution = await name_documents(ws.db, ws.template_id, body.resolution)
    conv.status, conv.resolution, conv.resolved_at = "resolved", body.resolution, datetime.now(timezone.utc)
    conv.agent_state = None
    ws.db.add(ChatMessage(conversation_id=conv.id, tenant_id=ws.tenant_id, role="system",
                          content=f"Resolved: {body.resolution}", evidence_citations=[], confidence=1.0,
                          agent_trace={"event_id": ws.event_id}))
    audit(ws, "AGENT_CONVERSATION_RESOLVED", "Conversation", conv.id, {"resolution": body.resolution})
    await ws.db.commit()
    await publish_ui_event(f"template:{ws.template_id}", "agent_conversation", conversation_dict(conv),
                           tenant_id=ws.tenant_id, template_id=ws.template_id)
    return conversation_dict(conv)


@router.get("/conversations/{conversation_id}/messages")
async def conversation_history(conversation_id: str, limit: int = Query(20, ge=1, le=100), ws: Workspace = Depends(get_workspace)):
    """The latest messages of a conversation, oldest first: what you and the person said, what you did before each reply
    (`actions`), and the platform's notes of what happened in it (role "system": they stopped you, it was resolved)."""
    conv = await ws.db.get(Conversation, conversation_id)
    if not conv or conv.template_id != ws.template_id:
        raise HTTPException(status_code=404, detail="Conversation not found in this workspace.")
    rows = (await ws.db.execute(
        select(ChatMessage).where(ChatMessage.conversation_id == conv.id)
        .order_by(ChatMessage.created_at.desc()).limit(limit)
    )).scalars().all()
    return [{"id": m.id, "role": m.role, "content": m.content, "at": m.created_at,
             "actions": (m.agent_trace or {}).get("actions") or []} for m in reversed(rows)]


@router.post("/conversations/{conversation_id}/messages", status_code=201)
async def reply_in_chat(conversation_id: str, body: ChatReplyBody, ws: Workspace = Depends(get_workspace)):
    """A reply in the conversation a person wrote in to wake this run. Work runs (reading documents, designing the
    template) write in no chat: their outcome shows in Activity, and questions go through flag / open_conversation."""
    conv = await ws.db.get(Conversation, conversation_id)
    if not conv or conv.template_id != ws.template_id:
        raise HTTPException(status_code=404, detail="Conversation not found in this workspace.")
    event = await ws.db.get(AgentEventOutbox, ws.event_id) if ws.event_id else None
    if event is not None and await _waking_conversation(ws) != conv.id:
        raise HTTPException(status_code=403, detail=(
            "Nobody wrote to you in that conversation in this run. You can only reply where a person wrote to you."))
    body.content = await name_documents(ws.db, ws.template_id, body.content)
    msg = ChatMessage(
        conversation_id=conv.id, tenant_id=ws.tenant_id, role="assistant", content=body.content,
        evidence_citations=body.evidence_citations, confidence=body.confidence,
        agent_trace={"event_id": ws.event_id, "reply_to": body.reply_to, **({"actions": body.actions} if body.actions else {})},
    )
    ws.db.add(msg)
    conv.updated_at = datetime.now(timezone.utc)
    await ws.db.commit()
    payload = {
        "message_id": msg.id, "conversation_id": conv.id, "reply_to": body.reply_to, "content": body.content,
        "answer": body.content, "evidence_citations": body.evidence_citations, "confidence": body.confidence,
    }
    await publish_ui_event(body.channel_id or conv.id, "agent_message", payload, tenant_id=ws.tenant_id, template_id=ws.template_id,
                           private_to=conv.user_id if conv.kind == "chat" else None)
    return payload


class SummaryBody(BaseModel):
    content: str = Field(..., min_length=1, max_length=6000)
    covers_until: datetime
    embedding: Optional[List[float]] = None


@router.get("/conversations/{conversation_id}/summary")
async def get_summary(conversation_id: str, ws: Workspace = Depends(get_workspace)):
    summary = await memory_service.conversation_summary(ws.db, ws.template_id, conversation_id)
    return {"content": summary.content, "covers_until": summary.data.get("covers_until")} if summary else {"content": None, "covers_until": None}


class TitleBody(BaseModel):
    title: str = Field(..., min_length=1, max_length=60, description="What the chat is about, in a few words")
    was: str = Field(..., description="The title you saw: the chat is renamed only if it is still that")


@router.put("/conversations/{conversation_id}/title")
async def name_chat(conversation_id: str, body: TitleBody, ws: Workspace = Depends(get_workspace)):
    """Names a person's chat by its topic after its first exchange. A chat they renamed meanwhile keeps their name."""
    conv = await ws.db.get(Conversation, conversation_id)
    if not conv or conv.template_id != ws.template_id or conv.kind != "chat":
        raise HTTPException(status_code=404, detail="Chat not found in this workspace.")
    if conv.title != body.was:
        return {"renamed": False, "title": conv.title}
    conv.title = body.title.strip()
    await ws.db.commit()
    await publish_ui_event(conv.id, "conversation_renamed", {"conversation_id": conv.id, "title": conv.title},
                           tenant_id=ws.tenant_id, template_id=ws.template_id, private_to=conv.user_id)
    return {"renamed": True, "title": conv.title}


@router.put("/conversations/{conversation_id}/summary")
async def put_summary(conversation_id: str, body: SummaryBody, ws: Workspace = Depends(get_workspace)):
    """The rolling summary of a chat's older messages (one per chat)."""
    conv = await ws.db.get(Conversation, conversation_id)
    if not conv or conv.template_id != ws.template_id:
        raise HTTPException(status_code=404, detail="Conversation not found in this workspace.")
    summary = await memory_service.conversation_summary(ws.db, ws.template_id, conversation_id)
    if summary is None:
        summary, _ = await memory_service.add(
            ws.db, tenant_id=ws.tenant_id, template_id=ws.template_id, scope="conversation", kind="summary",
            content=body.content, created_by="agent", user_id=conv.user_id, conversation_id=conv.id, importance=0.5,
            source={"type": "conversation", "conversation_id": conv.id},
        )
    else:
        summary.content, summary.needs_embedding = body.content, True
    summary.data = {**(summary.data or {}), "covers_until": body.covers_until.isoformat()}
    await ws.db.flush()
    await memory_service.set_embedding(ws.db, summary.id, body.embedding)
    await ws.db.commit()
    return {"id": summary.id}
