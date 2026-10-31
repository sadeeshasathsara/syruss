"""
Agent Tool API: the only way the agent service reads or changes business data.

Every request carries a token minted for one (tenant, template) workspace, so the agent
can only touch the workspace that woke it. Authority is enforced here, not in the prompt:

  free                read anything in the workspace, view pages, validate a draft record,
                      ask humans, reply in chat, report traces
  free, audited       commit a record that passes the template, send a document to review,
                      create an entity, add an alias, update entity attributes
  needs approval      template changes, entity merges -> only as proposals; a human approves
  not possible        anything outside the workspace, deleting data

During an evaluation run (an `eval.item` event) the agent reads a golden document as if it were
new: its record is diverted to the evaluation and scored, the expected answers are hidden from
every read, and anything else that would change the workspace is refused.
"""
import difflib
import io
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import Response
from PIL import Image
from pydantic import BaseModel, Field
from sqlalchemy import select, func, cast, String, or_
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.agent_token import AgentScope, verify_agent_token
from app.core.config import settings
from app.core.database import get_db_session
from app.core.permissions import permission_sentence, permissions_of
from app.core.scope import membership
from app.models.audit import AuditLog
from app.models.conversation import Conversation, ChatMessage
from app.models.document import Document, DocumentStatus, DocumentRole
from app.models.extraction import ExtractionResult
from app.models.knowledge import (
    Entity, EntityAlias, KnowledgeProposal, KnowledgeProposalStatus, KnowledgeProposalType,
)
from app.models.knowledge_source import KnowledgeSource
from app.models.memory import Memory
from app.models.outbox import AgentEventOutbox
from app.models.workspace_admin import WorkspaceMember
from app.models.question import AgentQuestion, AgentQuestionStatus
from app.models.review import ReviewTask, ReviewStatus
from app.models.template import DocumentTemplate
from app.models.trace import AgentExecutionTrace
from app.models.evaluation import EvalRunItem
from app.models.page_view import AgentPageView
from app.services import (
    budget_service, duplicate_check, eval_service, extraction_log, system_lessons, graph_service, memory_service, quality_service, record_query,
    settings_service, source_service, workspace_events,
)
from app.services.agent_events import agent_event_relay, enqueue_agent_event
from app.services.identity_check import normalize_id
from app.services.record_service import record_service
from app.models.screen import WorkspaceScreen
from app.services import formula, screen_engine, screen_service
from app.services.rule_engine import check_rule_definitions, normalize_schema, validate_record
from app.services.storage_service import storage_service
from app.services.template_service import template_service
from app.services.source_data import record_sets
from app.services.ui_events import publish_ui_event

router = APIRouter(tags=["Agent Tool API (internal)"])


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------

class Workspace:
    def __init__(self, scope: AgentScope, template: DocumentTemplate, db: AsyncSession,
                 eval_item: Optional[EvalRunItem] = None, hidden: Optional[set] = None):
        self.tenant_id = scope.tenant_id
        self.template_id = scope.template_id
        self.event_id = scope.event_id
        self.template = template
        self.db = db
        self.eval_item = eval_item          # set while the agent works on an evaluation run
        self.hidden = hidden or set()       # documents whose records are this evaluation's answers


# What the agent may still call while evaluating (everything else that writes is refused).
EVAL_WRITES = re.compile(r"/(documents/[^/]+/record|documents/[^/]+/status|records/validate|records/query|memories/search|traces|evals/outcome)$")


# In a person's private chat the agent acts as that person: what they may do bounds every call it makes
# (checked now, so removing them or changing their role stops the run's work at once).
CHAT_POLICY = [
    (re.compile(r"^/(documents(/|$)|records/|reviews/|quality|questions$)"), {"GET"}, "documents.view"),
    (re.compile(r"^/records/(validate|query)$"), {"POST"}, "documents.view"),
    (re.compile(r"^/documents/[^/]+/record$"), {"POST"}, "documents.review"),
    (re.compile(r"^/documents/[^/]+/status$"), {"POST"}, "documents.upload"),
    (re.compile(r"^/screens(/|$)"), {"GET"}, "documents.view"),
    (re.compile(r"^/screens/(preview|[^/]+/run)$"), {"POST"}, "documents.view"),
    (re.compile(r"^/screens(?:/(?!preview$)[^/]+)?$"), {"POST", "PATCH", "DELETE"}, "screens.manage"),
    (re.compile(r"^/(entities/search|graph/|sources/[^/]+$|proposals$)"), {"GET"}, "knowledge.view"),
    (re.compile(r"^/(entities|sources/[^/]+/mapping)"), {"POST", "PATCH", "PUT"}, "knowledge.manage"),
]


async def _private_chat_guard(request: Request, db: AsyncSession, event: AgentEventOutbox) -> None:
    actor = event.actor or {}
    if event.event_type != "human.message" or event.payload.get("conversation_kind") != "chat" or actor.get("type") != "user":
        return
    found = await membership(db, event.template_id, actor.get("id") or "")
    if found is None:
        raise HTTPException(status_code=403, detail="The person you are chatting with no longer has access to this workspace.")
    path = request.url.path.removeprefix(settings.AGENT_API_PREFIX)
    thread = re.match(r"^/conversations/([^/]+)", path)
    if thread and thread.group(1) != event.payload.get("conversation_id"):
        raise HTTPException(status_code=403, detail="In a private chat you can only use that chat's own conversation.")
    for pattern, methods, permission in CHAT_POLICY:
        if pattern.match(path) and request.method in methods and permission not in (found[1].permissions or []):
            raise HTTPException(status_code=403, detail=(
                f"{actor.get('name') or 'This person'}'s role ({found[1].name}) can't {permission_sentence(permission)}, "
                "so you can't do it for them."))


async def get_workspace(
    request: Request,
    authorization: str = Header(..., description="Bearer <agent token>"),
    db: AsyncSession = Depends(get_db_session),
) -> Workspace:
    token = authorization.removeprefix("Bearer ").strip()
    try:
        scope = verify_agent_token(token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid agent token: {exc}")
    template = await db.get(DocumentTemplate, scope.template_id)
    if not template or template.tenant_id != scope.tenant_id:
        raise HTTPException(status_code=404, detail="Workspace no longer exists.")
    event = await db.get(AgentEventOutbox, scope.event_id)
    if event is not None:
        if (event.tenant_id, event.template_id) != (scope.tenant_id, scope.template_id):
            raise HTTPException(status_code=401, detail="Invalid agent token: it does not match its event.")
        await _private_chat_guard(request, db, event)
    item = await eval_service.item_for_event(db, scope.event_id)
    if item is None:
        return Workspace(scope, template, db)
    if request.method != "GET" and not EVAL_WRITES.search(request.url.path):
        raise HTTPException(status_code=403, detail=(
            "Not during an evaluation: this run only measures how you read the document, so it can't change "
            "the workspace (no entities, aliases, questions, memories, proposals or messages). Submit your record."))
    return Workspace(scope, template, db, eval_item=item, hidden=await eval_service.run_documents(db, item.run_id))


async def _document(ws: Workspace, document_id: str) -> Document:
    doc = await ws.db.get(Document, document_id)
    if not doc or doc.template_id != ws.template_id or doc.tenant_id != ws.tenant_id:
        raise HTTPException(status_code=404, detail="Document not found in this workspace.")
    return doc


def _audit(ws: Workspace, event_type: str, entity_type: str, entity_id: str, details: Dict[str, Any]) -> None:
    ws.db.add(AuditLog(
        tenant_id=ws.tenant_id, template_id=ws.template_id, event_type=event_type,
        entity_type=entity_type, entity_id=entity_id, actor="agent", details=details,
    ))


def _question_dict(q: AgentQuestion) -> Dict[str, Any]:
    return {
        "id": q.id, "question": q.question, "context": q.context, "options": q.options, "answer_type": q.answer_type,
        "status": q.status.value, "answer": q.answer, "answer_choices": q.answer_choices, "answer_other": q.answer_other,
        "answered_by": q.answered_by,
        "target_role": q.target_role, "priority": q.priority,
        "related_document_id": q.related_document_id, "evidence_document_ids": q.evidence_document_ids,
        "data_table": q.data_table, "conversation_id": q.conversation_id,
        "created_at": q.created_at.isoformat() if q.created_at else None,
    }


def _proposal_dict(p: KnowledgeProposal) -> Dict[str, Any]:
    return {
        "id": p.id, "proposal_type": p.proposal_type, "observation": p.observation,
        "proposed_data": p.proposed_data, "status": p.status.value, "reviewer_notes": p.reviewer_notes,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


def _entity_dict(e: Entity) -> Dict[str, Any]:
    return {
        "id": e.id, "entity_type": e.entity_type, "name": e.name,
        "identifier_key": e.identifier_key, "identifier_value": e.identifier_value,
        "attributes": e.attributes, "aliases": [a.alias for a in e.aliases], "created_by": e.created_by,
    }


# ---------------------------------------------------------------------------
# Workspace overview
# ---------------------------------------------------------------------------

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
        "open_questions": [_question_dict(q) for q in open_questions],
        "pending_proposals": [_proposal_dict(p) for p in pending],
        "open_conversations": [_conversation_dict(c) for c in conversations],
        "knowledge_sources": [{"id": s.id, "name": s.name, "kind": s.kind, "status": s.status} for s in sources],
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


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

@router.get("/documents")
async def list_documents(
    status: Optional[DocumentStatus] = None,
    role: DocumentRole = DocumentRole.DOCUMENT,
    limit: int = Query(20, le=100),
    offset: int = 0,
    ws: Workspace = Depends(get_workspace),
):
    stmt = select(Document).where(Document.template_id == ws.template_id, Document.role == role)
    if status:
        stmt = stmt.where(Document.status == status)
    docs = (await ws.db.execute(stmt.order_by(Document.created_at.desc()).offset(offset).limit(limit))).scalars().all()
    return [{
        "id": d.id, "filename": d.original_filename, "status": d.status.value, "role": d.role.value,
        "content_type": d.content_type, "created_at": d.created_at.isoformat(),
    } for d in docs]


@router.get("/documents/{document_id}")
async def get_document(document_id: str, ws: Workspace = Depends(get_workspace)):
    doc = await _document(ws, document_id)
    width = height = None
    try:
        with Image.open(io.BytesIO(storage_service.download_file(doc.minio_object_key))) as img:
            width, height = img.size
    except Exception:
        pass
    record = None
    if doc.extraction and doc.id not in ws.hidden:
        record = {
            "data": doc.extraction.extracted_data, "confidence": doc.extraction.overall_confidence,
            "evidence": doc.extraction.evidence_records, "validation": doc.extraction.validation,
            "notes": doc.extraction.agent_notes, "template_version_id": doc.extraction.template_version_id,
        }
    review = None
    if doc.review_task and doc.id not in ws.hidden:
        r = doc.review_task
        review = {
            "id": r.id, "status": r.status.value, "reason": r.reason, "corrected_data": r.corrected_data,
            "reviewer_notes": r.reviewer_notes, "decided_by": r.decided_by,
        }
    if doc.id in ws.hidden:  # an evaluation: the document is to be read as if it had just arrived
        return {"id": doc.id, "filename": doc.original_filename, "status": "QUEUED", "role": doc.role.value,
                "content_type": doc.content_type, "image_size": {"width": width, "height": height},
                "record": None, "review": None}
    return {
        "id": doc.id, "filename": doc.original_filename, "status": doc.status.value, "role": doc.role.value,
        "content_type": doc.content_type, "image_size": {"width": width, "height": height},
        "record": record, "review": review,
    }


async def _page_budget(ws: Workspace) -> Dict[str, Any]:
    """
    How many more pages the agent may look at in this run. Pages cost a model call with an image,
    and what was read is already in the database, so looking at pages is capped (workspace
    settings, Agent > Autonomy):
      * answering a chat message: a few pages per message, and per person per day;
      * while working: documents that already have a record ("already read") a couple of times per
        run. The run's own document, and documents not read yet, are free.
    The agent is shown this budget when it wakes and after every page it looks at.
    """
    prefs = (await settings_service.get_settings(ws.db, ws.template_id)).agent
    event = await ws.db.get(AgentEventOutbox, ws.event_id) if ws.event_id else None
    payload, actor = (event.payload or {}, event.actor or {}) if event else ({}, {})

    async def views(*conditions) -> int:
        return (await ws.db.execute(select(func.count(func.distinct(AgentPageView.document_id))).where(*conditions))).scalar() or 0

    def allowance(limit: int, used: int) -> Dict[str, int]:
        return {"limit": limit, "used": used, "left": max(0, limit - used)}

    if event and event.event_type == "human.message":
        person = actor.get("id")
        day = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        today = (await ws.db.execute(select(func.count()).select_from(AgentPageView).where(
            AgentPageView.template_id == ws.template_id, AgentPageView.actor_id == person,
            AgentPageView.purpose == "chat", AgentPageView.created_at >= day))).scalar() or 0
        this_message = allowance(prefs.pages_per_chat_request, await views(AgentPageView.event_id == ws.event_id))
        person_today = allowance(prefs.pages_per_person_per_day, today)
        return {"kind": "chat", "person": person, "this_message": this_message, "person_today": person_today,
                "left": min(this_message["left"], person_today["left"])}
    own = {payload.get("document_id"), payload.get("related_document_id"), *(payload.get("sample_document_ids") or [])} - {None}
    already_read = allowance(prefs.past_pages_per_run,
                             await views(AgentPageView.event_id == ws.event_id, AgentPageView.purpose == "past"))
    return {"kind": "work", "own_documents": sorted(own), "already_read_this_run": already_read, "left": already_read["left"]}


async def _allow_page_view(ws: Workspace, doc: Document) -> Dict[str, Any]:
    """Records a page view within the budget (or refuses it) and returns the budget left.
    Zooming into a page already viewed in this run is free."""
    budget = await _page_budget(ws)
    seen = (await ws.db.execute(select(AgentPageView.id).where(
        AgentPageView.event_id == ws.event_id, AgentPageView.document_id == doc.id).limit(1))).first()
    if seen:
        return budget
    person = None
    if budget["kind"] == "chat":
        purpose, person = "chat", budget["person"]
        if not budget["this_message"]["left"]:
            raise HTTPException(status_code=429, detail=(
                f"You have looked at {budget['this_message']['limit']} pages for this message, the most allowed. The records "
                "are in the database: answer from get_document and search_past_records, and say plainly what you couldn't check."))
        if not budget["person_today"]["left"]:
            raise HTTPException(status_code=429, detail=(
                f"Today's limit of {budget['person_today']['limit']} pages for this person's chats is reached. "
                "Answer from the records in the database (get_document, search_past_records)."))
    elif doc.id in budget["own_documents"] or doc.extraction is None:
        purpose = "own"
    else:
        purpose = "past"
        if not budget["already_read_this_run"]["left"]:
            raise HTTPException(status_code=429, detail=(
                f"{doc.original_filename} was already read: its record is in the database. You have looked at "
                f"{budget['already_read_this_run']['limit']} already-read pages in this run, the most allowed; use "
                "get_document or search_past_records instead."))
    ws.db.add(AgentPageView(tenant_id=ws.tenant_id, template_id=ws.template_id, event_id=ws.event_id or "",
                            document_id=doc.id, actor_id=person, purpose=purpose))
    await ws.db.commit()
    return await _page_budget(ws)


@router.get("/system-lessons")
async def platform_lessons(ws: Workspace = Depends(get_workspace)):
    """How to work in this platform: lessons that hold in every workspace."""
    return [{"id": lesson.id, "content": lesson.content} for lesson in await system_lessons.active(ws.db)]


class SystemLessonBody(BaseModel):
    content: str = Field(..., min_length=10, max_length=600,
                         description="How to work in the platform: tools, refusals, commits. Never names, IDs or business facts.")
    why: str = Field("", max_length=1000, description="What happened that taught you this")


@router.post("/system-lessons", status_code=201)
async def propose_system_lesson(body: SystemLessonBody, ws: Workspace = Depends(get_workspace)):
    """Proposes a lesson about working in the platform. It applies everywhere once an admin approves it."""
    return await system_lessons.propose(ws.db, content=body.content, why=body.why, template_id=ws.template_id,
                                        event_id=ws.event_id)


@router.get("/run/cancelled")
async def run_cancelled(ws: Workspace = Depends(get_workspace)):
    """Whether a person stopped this run. Checked between steps and while the model thinks."""
    event = await ws.db.get(AgentEventOutbox, ws.event_id) if ws.event_id else None
    return {"cancelled": bool(event and event.cancelled_at), "by": event.cancelled_by if event else None}


@router.get("/page-budget")
async def page_budget(ws: Workspace = Depends(get_workspace)):
    """How many more pages you may look at in this run (and today, for a chat)."""
    return await _page_budget(ws)


@router.get("/documents/{document_id}/image")
async def view_document_image(
    document_id: str,
    region: Optional[str] = Query(None, description="ymin,xmin,ymax,xmax on a 0-1000 scale"),
    max_side: int = Query(1600, ge=256, le=3000),
    ws: Workspace = Depends(get_workspace),
):
    """Returns the page (or a zoomed region) as JPEG for the agent to look at, within the page caps."""
    doc = await _document(ws, document_id)
    budget = await _allow_page_view(ws, doc)
    try:
        img = Image.open(io.BytesIO(storage_service.download_file(doc.minio_object_key)))
        img.load()
    except Exception:
        raise HTTPException(status_code=415, detail=f"Cannot render '{doc.content_type}' as an image.")
    img = img.convert("RGB")
    if region:
        try:
            ymin, xmin, ymax, xmax = [max(0.0, min(1000.0, float(v))) for v in region.split(",")]
        except ValueError:
            raise HTTPException(status_code=400, detail="region must be 'ymin,xmin,ymax,xmax' on a 0-1000 scale")
        if ymax <= ymin or xmax <= xmin:
            raise HTTPException(status_code=400, detail="region is empty")
        w, h = img.size
        img = img.crop((int(xmin * w / 1000), int(ymin * h / 1000), int(xmax * w / 1000), int(ymax * h / 1000)))
    # Upscale small crops so handwriting is legible; downscale big pages to keep requests small.
    scale = max_side / max(img.size)
    if scale < 1 or (region and scale > 1):
        img = img.resize((max(1, int(img.width * min(scale, 4))), max(1, int(img.height * min(scale, 4)))), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return Response(content=buf.getvalue(), media_type="image/jpeg",
                    headers={"X-Page-Budget": json.dumps({k: v for k, v in budget.items() if k != "person"})})


class DocumentStatusUpdate(BaseModel):
    status: DocumentStatus
    message: Optional[str] = None


@router.post("/documents/{document_id}/status")
async def set_document_status(document_id: str, body: DocumentStatusUpdate, ws: Workspace = Depends(get_workspace)):
    """The agent may mark work in progress or a failure it cannot recover from; outcomes go through /record."""
    if body.status not in (DocumentStatus.PROCESSING, DocumentStatus.AWAITING_INPUT, DocumentStatus.FAILED):
        raise HTTPException(status_code=400, detail="Use /record to complete a document or send it to review.")
    doc = await _document(ws, document_id)
    if ws.eval_item:
        if doc.id != ws.eval_item.document_id:
            raise HTTPException(status_code=403, detail="Not during an evaluation: only the document being evaluated.")
        if body.status == DocumentStatus.FAILED:
            await eval_service.record_outcome(ws.db, ws.eval_item, "FAILED", body.message)
            await ws.db.commit()
            await workspace_events.dispatch(ws.db)
        return {"status": body.status.value, "evaluation": True}
    doc.status = body.status
    doc.error_message = body.message if body.status == DocumentStatus.FAILED else None
    _audit(ws, f"DOCUMENT_{body.status.value}", "Document", doc.id, {"message": body.message})
    if body.status == DocumentStatus.FAILED:
        await workspace_events.emit(
            ws.db, tenant_id=ws.tenant_id, template_id=ws.template_id, type="document.failed", severity="error",
            title=f"Couldn't process {doc.original_filename}", body=body.message or "", path=f"documents/{doc.id}",
            data={"document_id": doc.id},
        )
    await ws.db.commit()
    await workspace_events.dispatch(ws.db)
    await publish_ui_event(doc.id, "document_status", {"status": doc.status.value, "message": body.message},
                           tenant_id=ws.tenant_id, template_id=ws.template_id)
    stage, level, text = {
        DocumentStatus.PROCESSING: ("reading", "info", "The agent started reading this document."),
        DocumentStatus.AWAITING_INPUT: ("waiting", "warning", f"Paused until someone answers the agent's question. {body.message or ''}".strip()),
        DocumentStatus.FAILED: ("failed", "error", f"Couldn't be processed. {body.message or ''}".strip()),
    }[body.status]
    await extraction_log.record(tenant_id=ws.tenant_id, template_id=ws.template_id, stage=stage, level=level, message=text,
                                document_id=doc.id, document_name=doc.original_filename, actor="Agent")
    return {"status": doc.status.value}


class ProcessBody(BaseModel):
    reason: str = Field("", description="What the person asked for, in a few words")


@router.post("/documents/{document_id}/process")
async def process_for_person(document_id: str, body: ProcessBody, ws: Workspace = Depends(get_workspace)):
    """
    Starts reading a document because the person you are chatting with asked for it. It runs as
    their request (their role must allow processing) and is linked to the conversation.
    """
    event = await ws.db.get(AgentEventOutbox, ws.event_id)
    actor = (event.actor if event else None) or {}
    if event is None or event.event_type != "human.message" or actor.get("type") != "user":
        raise HTTPException(status_code=403, detail="Only a person chatting with you can ask for documents to be processed.")
    member = (await ws.db.execute(select(WorkspaceMember).where(
        WorkspaceMember.template_id == ws.template_id, WorkspaceMember.user_id == actor.get("id"),
    ))).scalar_one_or_none()
    if member is None or "documents.upload" not in await permissions_of(ws.db, member.role):
        raise HTTPException(status_code=403, detail=f"{actor.get('name') or 'This person'}'s role can't start document processing.")
    doc = await _document(ws, document_id)
    if doc.role != DocumentRole.DOCUMENT:
        raise HTTPException(status_code=400, detail="Samples are not processed into records.")
    if doc.status in (DocumentStatus.QUEUED, DocumentStatus.PROCESSING):
        raise HTTPException(status_code=409, detail=f"{doc.original_filename} is already {doc.status.value.lower()}.")
    if not ws.template.active_version_id:
        raise HTTPException(status_code=409, detail="The workspace has no published template yet.")
    doc.status, doc.error_message = DocumentStatus.QUEUED, None
    enqueue_agent_event(
        ws.db, event_type="document.submitted", tenant_id=ws.tenant_id, template_id=ws.template_id,
        payload={"document_id": doc.id}, actor_id=actor["id"], actor_type="user", actor_name=actor.get("name"),
        source="chat", conversation_id=actor.get("conversation_id"),
    )
    _audit(ws, "DOCUMENT_QUEUED_FROM_CHAT", "Document", doc.id, {"for": actor.get("id"), "reason": body.reason})
    await ws.db.commit()
    agent_event_relay.notify()
    return {"document_id": doc.id, "filename": doc.original_filename, "status": doc.status.value}


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

class RecordSubmission(BaseModel):
    data: Dict[str, Any] = Field(..., description="Records keyed by object type")
    decision: str = Field(..., description="COMMIT or REVIEW")
    confidence: float = Field(..., ge=0, le=1)
    evidence: List[Dict[str, Any]] = Field(default_factory=list)
    notes: Optional[str] = None
    review_reason: Optional[str] = None
    uncertain_fields: List[Dict[str, Any]] = Field(default_factory=list)
    acknowledged_signals: List[Dict[str, Any]] = Field(
        default_factory=list, description="[{id, reason}]: identity signals you checked on the page and stand by")


async def _questions_text(ws: Workspace, status: AgentQuestionStatus) -> str:
    """Everything said in flags with this status: their question, context and answer."""
    rows = (await ws.db.execute(select(AgentQuestion.question, AgentQuestion.context, AgentQuestion.answer).where(
        AgentQuestion.template_id == ws.template_id, AgentQuestion.status == status))).all()
    return " ".join(" ".join(filter(None, r)) for r in rows)


async def _unknown_identities(ws: Workspace, data: Dict[str, Any]) -> List[Dict[str, str]]:
    """People and things a record names that the workspace doesn't know yet (no entity with their ID)."""
    objects = normalize_schema(ws.template.schema_definition)
    found: Dict[str, Dict[str, str]] = {}
    for obj_type in objects:
        for row in graph_service.rows_of(data, obj_type):
            for thing, _ in graph_service.things_in_row(objects, data, obj_type, row):
                if thing.key not in found or (thing.label and not found[thing.key]["name"]):
                    found[thing.key] = {"type": thing.type, "id": thing.key.split(":", 1)[1], "name": thing.label}
    if not found:
        return []
    known = {f"{t}:{normalize_id(v)}" for t, v in (await ws.db.execute(
        select(Entity.entity_type, Entity.identifier_value).where(Entity.template_id == ws.template_id)
    )).all() if v}
    return [t for key, t in found.items() if key not in known]


def _listed(things: List[Dict[str, str]]) -> str:
    return "; ".join(f"{t['type']} {t['id']}" + (f" ({t['name']})" if t.get("name") else "") for t in things)


async def _refuse_unapproved_identities(ws: Workspace, doc: Document, data: Dict[str, Any]) -> None:
    """
    Registration off: a record naming people or things the workspace doesn't know can't be committed
    until a person has decided about them (a flag they answered that names them). The refusal says
    what to do, so the agent asks; it learns from the refusal rather than being nudged.
    """
    unknown = await _unknown_identities(ws, data)
    if not unknown:
        return
    decided = await _questions_text(ws, AgentQuestionStatus.ANSWERED)
    waiting = [t for t in unknown if t["id"] not in decided]
    if not waiting:
        return
    pending = (await ws.db.execute(select(AgentQuestion).where(
        AgentQuestion.template_id == ws.template_id, AgentQuestion.status == AgentQuestionStatus.OPEN,
    ).order_by(AgentQuestion.created_at))).scalars().all()
    asked = next((q for q in pending if any(t["id"] in f"{q.question} {q.context}" for t in waiting)), None)
    how = (f'You already asked: "{asked.question}". Park this document on that question: flag exactly the same '
           f'question again with waiting_document_ids ["{doc.id}"] (no duplicate is created).') if asked else (
        "Ask a person: flag ONE single-choice question listing them (type, ID, name) and asking whether to register "
        "them, or open a conversation if it needs discussion, with this document in waiting_document_ids. When they "
        "answer, register what they approved (create_entity with source_question_id) and commit.")
    raise HTTPException(status_code=422, detail={
        "message": "Record refused: it names people or things this workspace doesn't know, and its settings don't let "
                   "you register them on your own.",
        "errors": [{"code": "unregistered_identity", "path": "record",
                    "message": f"Not known yet: {_listed(waiting)}. {how}"}],
        "unregistered": waiting,
    })


async def _new_identities(ws: Workspace, data: Dict[str, Any]) -> List[Dict[str, str]]:
    """Registration on: who and what a committed record names that isn't registered yet."""
    return await _unknown_identities(ws, data)


@router.post("/documents/{document_id}/record")
async def submit_record(document_id: str, body: RecordSubmission, ws: Workspace = Depends(get_workspace)):
    doc = await _document(ws, document_id)
    if doc.role != DocumentRole.DOCUMENT:
        raise HTTPException(status_code=400, detail="Samples are not processed into records.")
    threshold = (await settings_service.get_settings(ws.db, ws.template_id)).documents.commit_threshold
    if ws.eval_item:
        return await _evaluate_record(ws, doc, body, threshold)
    if body.decision.upper() == "COMMIT" and body.confidence < threshold:
        raise HTTPException(status_code=422, detail={"message": "Workspace policy", "problems": [
            f"This workspace commits only records the agent is at least {threshold:.0%} sure of "
            f"(you said {body.confidence:.0%}). Send it to review with a specific reason instead."]})
    may_register = (await settings_service.get_settings(ws.db, ws.template_id)).agent.may_register_entities
    if body.decision.upper() == "COMMIT" and not may_register:
        await _refuse_unapproved_identities(ws, doc, body.data)
    outcome = await record_service.submit(
        ws.db, template=ws.template, document=doc, data=body.data, decision=body.decision,
        confidence=body.confidence, evidence=body.evidence, notes=body.notes,
        review_reason=body.review_reason, uncertain_fields=body.uncertain_fields,
        acknowledged_signals=body.acknowledged_signals,
    )
    if outcome["status"] == "NEEDS_REVIEW":
        await workspace_events.emit(
            ws.db, tenant_id=ws.tenant_id, template_id=ws.template_id, type="document.needs_review", severity="warning",
            title=f"{doc.original_filename} needs review", body=body.review_reason or "", path=f"documents/{doc.id}",
            data={"document_id": doc.id},
        )
    elif outcome["status"] == "COMPLETED":
        await workspace_events.emit(
            ws.db, tenant_id=ws.tenant_id, template_id=ws.template_id, type="document.completed", severity="success",
            title=f"{doc.original_filename} was read and saved", path=f"documents/{doc.id}",
            data={"document_id": doc.id, "confidence": body.confidence},
        )
    await ws.db.commit()
    await workspace_events.dispatch(ws.db)
    await publish_ui_event(doc.id, "agent_completed", {
        "status": outcome["status"], "confidence": body.confidence, "reason": body.review_reason,
    }, tenant_id=ws.tenant_id, template_id=ws.template_id)
    if outcome["status"] == "NEEDS_REVIEW":
        await extraction_log.record(
            tenant_id=ws.tenant_id, template_id=ws.template_id, stage="review", level="warning", actor="Agent", document_id=doc.id,
            document_name=doc.original_filename, message=f"Sent to review. {body.review_reason or ''}".strip(),
            details={"confidence": body.confidence})
    elif outcome["status"] == "COMPLETED":
        await extraction_log.record(
            tenant_id=ws.tenant_id, template_id=ws.template_id, stage="saved", level="success", actor="Agent", document_id=doc.id,
            document_name=doc.original_filename, message="Read and saved.", details={"confidence": body.confidence})
    if outcome["status"] == "COMPLETED" and may_register:
        new = await _new_identities(ws, body.data)
        if new:
            outcome["new_identities"] = new
            outcome["registration"] = "you_may_register"
            outcome["next"] = ("These aren't registered yet. Register each one you are sure of with create_entity "
                               f"(source_document_id {doc.id}) so you recognize them on later documents.")
    return outcome


async def _evaluate_record(ws: Workspace, doc: Document, body: RecordSubmission, threshold: float) -> Dict[str, Any]:
    """The same checks a real commit gets, then the record is scored instead of stored."""
    if doc.id != ws.eval_item.document_id:
        raise HTTPException(status_code=403, detail="Not during an evaluation: only the document being evaluated.")
    decision = body.decision.upper()
    if decision not in ("COMMIT", "REVIEW"):
        raise HTTPException(status_code=400, detail="decision must be COMMIT or REVIEW")
    validation = validate_record(ws.template.schema_definition, body.data)
    signals = await graph_service.identity_signals(
        ws.db, template_id=ws.template_id, schema=ws.template.schema_definition, data=body.data, document_id=doc.id,
    ) + await duplicate_check.duplicate_signals(
        ws.db, template_id=ws.template_id, schema=ws.template.schema_definition, data=body.data, document=doc)
    acknowledged = {a.get("id") for a in body.acknowledged_signals if isinstance(a, dict) and str(a.get("reason") or "").strip()}
    blocking = [s for s in signals if s["blocking"] and s["id"] not in acknowledged]
    if decision == "COMMIT" and body.confidence < threshold:
        validation = {**validation, "valid": False, "errors": validation["errors"] + [
            {"code": "below_commit_threshold", "path": "", "message": f"Confidence {body.confidence:.0%} is under {threshold:.0%}."}]}
    if blocking:
        validation = {**validation, "valid": False, "errors": validation["errors"] + [
            {"code": s["code"], "path": s["path"], "message": s["message"]} for s in blocking]}
    outcome = await eval_service.record_answer(
        ws.db, ws.eval_item, data=body.data, decision=decision, confidence=body.confidence,
        validation={**validation, "signals": signals}, notes=body.notes,
    )
    await ws.db.commit()
    await workspace_events.dispatch(ws.db)
    return outcome


class EvalOutcome(BaseModel):
    note: Optional[str] = None


@router.post("/evals/outcome")
async def evaluation_outcome(body: EvalOutcome, ws: Workspace = Depends(get_workspace)):
    """Closes this run's evaluation document when no record was submitted (called by the runtime)."""
    if not ws.eval_item:
        raise HTTPException(status_code=400, detail="This run isn't an evaluation.")
    await eval_service.record_outcome(ws.db, ws.eval_item, "NO_ANSWER", body.note)
    await ws.db.commit()
    await workspace_events.dispatch(ws.db)
    return {"status": ws.eval_item.status}


@router.get("/quality")
async def workspace_quality(days: int = Query(30, ge=1, le=365), ws: Workspace = Depends(get_workspace)):
    """How the agent has been doing: its decisions, people's corrections by field, and calibration."""
    return await quality_service.live(ws.db, ws.template_id, days)


class RecordDraft(BaseModel):
    data: Dict[str, Any]
    document_id: Optional[str] = Field(None, description="The document the draft is for (its earlier record is then not counted)")
    schema_definition: Optional[Dict[str, Any]] = Field(None, description="Validate against this schema instead of the active one")


@router.post("/records/validate")
async def validate_draft_record(body: RecordDraft, ws: Workspace = Depends(get_workspace)):
    """Dry run: checks a draft against the active (or a proposed) template without storing anything.
    Against the active template it also returns the workspace graph's identity signals."""
    result = validate_record(body.schema_definition or ws.template.schema_definition, body.data)
    if not body.schema_definition:
        document = await _document(ws, body.document_id) if body.document_id else None
        result["signals"] = await graph_service.identity_signals(
            ws.db, template_id=ws.template_id, schema=ws.template.schema_definition, data=body.data,
            document_id=body.document_id or "",
        ) + await duplicate_check.duplicate_signals(
            ws.db, template_id=ws.template_id, schema=ws.template.schema_definition, data=body.data, document=document,
        )
    return result


@router.post("/records/query")
async def query_records(body: record_query.RecordQuery, ws: Workspace = Depends(get_workspace)):
    """Rows of one template object across every saved record, filtered and grouped in one call
    (e.g. the unique people who worked a day shift). The answer to list, count and filter questions."""
    return await record_query.query(ws.db, template_id=ws.template_id, schema=ws.template.schema_definition,
                                    q=body, hidden=ws.hidden)


@router.get("/records/search")
async def search_records(
    q: str = Query(..., min_length=1, description="Text to look for in committed records"),
    limit: int = Query(10, le=50),
    ws: Workspace = Depends(get_workspace),
):
    """Finds past records mentioning a value: how a name was written before, who usually appears, etc."""
    stmt = (
        select(ExtractionResult, Document)
        .join(Document, Document.id == ExtractionResult.document_id)
        .where(
            ExtractionResult.template_id == ws.template_id,
            Document.status == DocumentStatus.COMPLETED,
            Document.id.not_in(ws.hidden),
            cast(ExtractionResult.extracted_data, String).ilike(f"%{q}%"),
        )
        .order_by(ExtractionResult.updated_at.desc())
        .limit(limit)
    )
    rows = (await ws.db.execute(stmt)).all()
    return [{
        "document_id": doc.id, "filename": doc.original_filename,
        "date": ext.updated_at.isoformat(), "data": ext.extracted_data,
    } for ext, doc in rows]


@router.get("/reviews/history")
async def review_history(limit: int = Query(10, le=50), ws: Workspace = Depends(get_workspace)):
    """Recent human review decisions in this workspace: what people corrected and why."""
    rows = (await ws.db.execute(
        select(ReviewTask).where(ReviewTask.template_id == ws.template_id, ReviewTask.status != ReviewStatus.PENDING,
                                 ReviewTask.document_id.not_in(ws.hidden))
        .order_by(ReviewTask.decided_at.desc()).limit(limit)
    )).scalars().all()
    return [{
        "review_id": r.id, "document_id": r.document_id, "status": r.status.value, "reason": r.reason,
        "original_data": r.original_data, "corrected_data": r.corrected_data, "notes": r.reviewer_notes,
        "decided_at": r.decided_at.isoformat() if r.decided_at else None,
    } for r in rows]


# ---------------------------------------------------------------------------
# Entities (the workspace's known world)
# ---------------------------------------------------------------------------

def _similarity(query: str, candidate: str) -> float:
    a, b = query.lower().strip(), candidate.lower().strip()
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    tokens_a, tokens_b = set(a.split()), set(b.split())
    overlap = len(tokens_a & tokens_b) / max(len(tokens_a), len(tokens_b))
    contains = 0.9 if (a in b or b in a) and min(len(a), len(b)) >= 3 else 0.0
    return max(ratio, overlap, contains)


@router.get("/entities/search")
async def search_entities(
    q: str = Query(..., min_length=1, description="Name, identifier, or text as read from the page"),
    entity_type: Optional[str] = None,
    limit: int = Query(5, le=25),
    ws: Workspace = Depends(get_workspace),
):
    """
    Ranked candidates, not a single answer: the agent decides which candidate the ink
    refers to, using the scores together with context (what else is on the page, history).
    """
    stmt = select(Entity).where(Entity.template_id == ws.template_id)
    if entity_type:
        stmt = stmt.where(Entity.entity_type == entity_type)
    candidates = []
    for e in (await ws.db.execute(stmt)).scalars().all():
        fields = [("name", e.name), ("identifier", e.identifier_value or "")] + [("alias", a.alias) for a in e.aliases]
        best_field, best = max(((f, _similarity(q, v)) for f, v in fields if v), key=lambda x: x[1], default=("", 0.0))
        if best >= 0.4:
            candidates.append({**_entity_dict(e), "score": round(best, 3), "matched_on": best_field})
    candidates.sort(key=lambda c: c["score"], reverse=True)
    return {"query": q, "candidates": candidates[:limit]}


class EntityCreateBody(BaseModel):
    entity_type: str
    name: str
    identifier_key: Optional[str] = None
    identifier_value: Optional[str] = None
    attributes: Dict[str, Any] = Field(default_factory=dict)
    source_document_id: Optional[str] = Field(None, description="A COMPLETED document that names this entity")
    source_question_id: Optional[str] = Field(None, description="An answered question in which a person confirmed it")
    reason: str = Field(..., description="Why the agent believes this is a new, real entity")


@router.post("/entities", status_code=201)
async def create_entity(body: EntityCreateBody, ws: Workspace = Depends(get_workspace)):
    approved = None
    if body.source_question_id:
        approved = await ws.db.get(AgentQuestion, body.source_question_id)
        if not approved or approved.template_id != ws.template_id or approved.status != AgentQuestionStatus.ANSWERED:
            raise HTTPException(status_code=409, detail="source_question_id must be a question a person answered.")
    if approved is None and not (await settings_service.get_settings(ws.db, ws.template_id)).agent.may_register_entities:
        raise HTTPException(status_code=403, detail=(
            "This workspace's settings don't let you register people and things on your own. Flag one question listing "
            "them and asking whether to register them; when a person agrees, register them with source_question_id."))
    # The agent registers only the kinds of things the template identifies.
    identified = {o["type"]: o["identity"] for o in (ws.template.schema_definition or {}).get("objects", [])
                  if isinstance(o, dict) and isinstance(o.get("identity"), dict)}
    if body.entity_type not in identified:
        raise HTTPException(status_code=422, detail=(
            f"'{body.entity_type}' is not something this template identifies. Entity types: {sorted(identified) or 'none'}. "
            "If it is a real, recurring thing, propose a template change that gives it an identity."))
    body.identifier_key = body.identifier_key or identified[body.entity_type].get("id_field")
    if body.identifier_value:
        existing = (await ws.db.execute(select(Entity).where(
            Entity.template_id == ws.template_id, Entity.entity_type == body.entity_type,
            Entity.identifier_value == body.identifier_value,
        ))).scalar_one_or_none()
        if existing:
            raise HTTPException(status_code=409, detail={"message": "Entity already exists", "entity": _entity_dict(existing)})
    # Entities come from confirmed facts only, so the agent cannot create its own confirmation.
    if body.source_document_id:
        source = await _document(ws, body.source_document_id)
        if source.status != DocumentStatus.COMPLETED:
            raise HTTPException(status_code=409, detail="Register entities from committed records: commit the document first.")
    elif approved is None:
        raise HTTPException(status_code=422, detail="Give source_document_id (a committed document) or source_question_id (an answered question).")
    entity = Entity(
        tenant_id=ws.tenant_id, template_id=ws.template_id, entity_type=body.entity_type, name=body.name,
        identifier_key=body.identifier_key, identifier_value=body.identifier_value, attributes=body.attributes,
        confidence=0.8, created_by="agent", source_document_id=body.source_document_id,
    )
    ws.db.add(entity)
    await ws.db.flush()
    _audit(ws, "ENTITY_CREATED", "Entity", entity.id, {"name": body.name, "reason": body.reason})
    await ws.db.commit()
    await ws.db.refresh(entity)
    return _entity_dict(entity)


class EntityUpdateBody(BaseModel):
    attributes: Dict[str, Any] = Field(..., description="Attributes to set (merged into existing)")
    reason: str


@router.patch("/entities/{entity_id}")
async def update_entity(entity_id: str, body: EntityUpdateBody, ws: Workspace = Depends(get_workspace)):
    entity = await ws.db.get(Entity, entity_id)
    if not entity or entity.template_id != ws.template_id:
        raise HTTPException(status_code=404, detail="Entity not found in this workspace.")
    entity.attributes = {**(entity.attributes or {}), **body.attributes}
    _audit(ws, "ENTITY_UPDATED", "Entity", entity.id, {"attributes": body.attributes, "reason": body.reason})
    await ws.db.commit()
    await ws.db.refresh(entity)
    return _entity_dict(entity)


class AliasBody(BaseModel):
    alias: str
    source: str = Field(..., description="Where this was learned, e.g. 'review r_123' or 'answer to question q_9'")


@router.post("/entities/{entity_id}/aliases", status_code=201)
async def add_alias(entity_id: str, body: AliasBody, ws: Workspace = Depends(get_workspace)):
    entity = await ws.db.get(Entity, entity_id)
    if not entity or entity.template_id != ws.template_id:
        raise HTTPException(status_code=404, detail="Entity not found in this workspace.")
    if not (await settings_service.get_settings(ws.db, ws.template_id)).agent.may_learn_aliases:
        raise HTTPException(status_code=403, detail="This workspace's settings don't let you add spellings on your own; ask a person.")
    alias = body.alias.strip()
    if alias.lower() not in {a.alias.lower() for a in entity.aliases} and alias.lower() != entity.name.lower():
        ws.db.add(EntityAlias(entity_id=entity.id, template_id=ws.template_id, alias=alias, source=body.source))
        _audit(ws, "ENTITY_ALIAS_ADDED", "Entity", entity.id, {"alias": alias, "source": body.source})
        await ws.db.commit()
        await ws.db.refresh(entity)
    return _entity_dict(entity)


# ---------------------------------------------------------------------------
# Talking to humans
# ---------------------------------------------------------------------------

@router.get("/graph/related")
async def related(
    object_type: str = Query(..., alias="object", description="The object type, e.g. Guard"),
    identifier: str = Query(..., alias="id", description="Its ID as written"),
    ws: Workspace = Depends(get_workspace),
):
    """The workspace graph around one thing: what it appears with in committed records, most often
    first, and the things of its kind that share those contexts."""
    key = graph_service.thing_key(object_type, identifier)
    if not key:
        raise HTTPException(status_code=400, detail="Give the thing's ID.")
    neighbours = await graph_service.appears_with(ws.db, ws.template_id, key, limit=30)
    alike = await graph_service.peers(ws.db, ws.template_id, key, limit=10)
    return {
        "thing": {"key": key, "type": object_type, "id": key.split(":", 1)[1]},
        "appears_with": [{"type": n.type, "id": n.key.split(":", 1)[1], "name": n.label, "times": n.count} for n in neighbours],
        "shares_contexts_with": [{"id": n.key.split(":", 1)[1], "name": n.label, "contexts_shared": n.count} for n in alike],
        "graph": await graph_service.backend(ws.db, ws.template_id),
        "note": None if neighbours else "Not seen in any committed record yet.",
    }


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
    question: str
    context: str = Field(..., description="Why you are asking and what you currently believe")
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
        await _document(ws, doc_id)
    duplicate = (await ws.db.execute(select(AgentQuestion).where(
        AgentQuestion.template_id == ws.template_id, AgentQuestion.status == AgentQuestionStatus.OPEN,
        func.lower(AgentQuestion.question) == body.question.strip().lower(),
    ))).scalar_one_or_none()
    if duplicate:
        return {**_question_dict(duplicate), "duplicate": True}
    q = AgentQuestion(
        tenant_id=ws.tenant_id, template_id=ws.template_id, question=body.question.strip(), context=body.context,
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
    _audit(ws, "AGENT_QUESTION_ASKED", "AgentQuestion", q.id, {"question": q.question})
    await workspace_events.emit(
        ws.db, tenant_id=ws.tenant_id, template_id=ws.template_id, type="agent.question",
        severity="warning" if q.priority == "high" else "info", title="The agent has a question", body=q.question,
        path=f"review?flag={q.id}", data={"question_id": q.id},
    )
    await ws.db.commit()
    await workspace_events.dispatch(ws.db)
    payload = _question_dict(q)
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
    return [_question_dict(q) for q in rows]


class ChatReplyBody(BaseModel):
    content: str
    reply_to: Optional[str] = Field(None, description="The user message id this answers")
    evidence_citations: List[Dict[str, Any]] = Field(default_factory=list)
    confidence: float = 1.0
    channel_id: Optional[str] = Field(None, description="Live channel to deliver on (defaults to the conversation id)")


# Who an agent conversation is for, by the permission their role must have.
AUDIENCE_PERMISSION = {"admin": "template.publish", "reviewer": "documents.review"}


def _conversation_dict(c: Conversation) -> Dict[str, Any]:
    return {"id": c.id, "title": c.title, "kind": c.kind, "status": c.status, "audience": c.audience,
            "document_ids": (c.context_metadata or {}).get("document_ids", []), "resolution": c.resolution,
            "updated_at": c.updated_at.isoformat() if c.updated_at else None}


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
        await _document(ws, doc_id)
    same = (await ws.db.execute(select(Conversation).where(
        Conversation.template_id == ws.template_id, Conversation.kind == "agent", Conversation.status == "open",
        func.lower(Conversation.title) == body.title.strip().lower(),
    ))).scalars().first()
    if same:
        return {**_conversation_dict(same), "already_open": True,
                "note": "You already have an open conversation about this; continue it with reply_to_user."}
    conv = Conversation(
        tenant_id=ws.tenant_id, template_id=ws.template_id, user_id="agent", kind="agent", status="open",
        audience=body.audience, title=body.title.strip(),
        context_metadata={"document_ids": body.document_ids, "priority": body.priority, "opened_by_event": ws.event_id},
    )
    ws.db.add(conv)
    await ws.db.flush()
    ws.db.add(ChatMessage(conversation_id=conv.id, tenant_id=ws.tenant_id, role="assistant", content=body.message,
                          evidence_citations=[], confidence=1.0, agent_trace={"event_id": ws.event_id}))
    _audit(ws, "AGENT_CONVERSATION_OPENED", "Conversation", conv.id, {"title": conv.title, "audience": body.audience})
    await workspace_events.emit(
        ws.db, tenant_id=ws.tenant_id, template_id=ws.template_id, type="agent.conversation",
        severity="warning" if body.priority == "high" else "info", title=f"Syruss: {conv.title}",
        body=body.message[:300], path=f"agent?conversation={conv.id}", data={"conversation_id": conv.id},
        permission=AUDIENCE_PERMISSION[body.audience],
    )
    await ws.db.commit()
    await workspace_events.dispatch(ws.db)
    await publish_ui_event(f"template:{ws.template_id}", "agent_conversation", _conversation_dict(conv),
                           tenant_id=ws.tenant_id, template_id=ws.template_id)
    return _conversation_dict(conv)


class CheckpointBody(BaseModel):
    messages: List[Dict[str, Any]] = Field(..., description="The agent's message history, serialized")


@router.get("/conversations/{conversation_id}/state")
async def get_checkpoint(conversation_id: str, ws: Workspace = Depends(get_workspace)):
    """Your saved working state for a conversation you opened (until it is resolved)."""
    conv = await _agent_conversation(ws, conversation_id)
    return {**_conversation_dict(conv), "state": conv.agent_state}


@router.put("/conversations/{conversation_id}/state")
async def save_checkpoint(conversation_id: str, body: CheckpointBody, ws: Workspace = Depends(get_workspace)):
    conv = await _agent_conversation(ws, conversation_id)
    if conv.status != "open":
        return {"saved": False, "reason": "The conversation is resolved; its state is no longer kept."}
    conv.agent_state = {"messages": body.messages, "saved_at": datetime.now(timezone.utc).isoformat(), "event_id": ws.event_id}
    await ws.db.commit()
    return {"saved": True, "messages": len(body.messages)}


class ResolveBody(BaseModel):
    resolution: str = Field(..., min_length=3, description="What was decided or done")


@router.post("/conversations/{conversation_id}/resolve")
async def resolve_conversation(conversation_id: str, body: ResolveBody, ws: Workspace = Depends(get_workspace)):
    """Marks a conversation you opened as resolved. Its saved working state is cleared."""
    conv = await _agent_conversation(ws, conversation_id)
    conv.status, conv.resolution, conv.resolved_at = "resolved", body.resolution, datetime.now(timezone.utc)
    conv.agent_state = None
    ws.db.add(ChatMessage(conversation_id=conv.id, tenant_id=ws.tenant_id, role="system",
                          content=f"Resolved: {body.resolution}", evidence_citations=[], confidence=1.0,
                          agent_trace={"event_id": ws.event_id}))
    _audit(ws, "AGENT_CONVERSATION_RESOLVED", "Conversation", conv.id, {"resolution": body.resolution})
    await ws.db.commit()
    await publish_ui_event(f"template:{ws.template_id}", "agent_conversation", _conversation_dict(conv),
                           tenant_id=ws.tenant_id, template_id=ws.template_id)
    return _conversation_dict(conv)


@router.get("/conversations/{conversation_id}/messages")
async def conversation_history(conversation_id: str, limit: int = Query(20, ge=1, le=100), ws: Workspace = Depends(get_workspace)):
    """The latest messages of a conversation, oldest first (what you and the person said)."""
    conv = await ws.db.get(Conversation, conversation_id)
    if not conv or conv.template_id != ws.template_id:
        raise HTTPException(status_code=404, detail="Conversation not found in this workspace.")
    rows = (await ws.db.execute(
        select(ChatMessage).where(ChatMessage.conversation_id == conv.id, ChatMessage.role.in_(("user", "assistant")))
        .order_by(ChatMessage.created_at.desc()).limit(limit)
    )).scalars().all()
    return [{"id": m.id, "role": m.role, "content": m.content, "at": m.created_at} for m in reversed(rows)]


@router.post("/conversations/{conversation_id}/messages", status_code=201)
async def reply_in_chat(conversation_id: str, body: ChatReplyBody, ws: Workspace = Depends(get_workspace)):
    conv = await ws.db.get(Conversation, conversation_id)
    if not conv or conv.template_id != ws.template_id:
        raise HTTPException(status_code=404, detail="Conversation not found in this workspace.")
    msg = ChatMessage(
        conversation_id=conv.id, tenant_id=ws.tenant_id, role="assistant", content=body.content,
        evidence_citations=body.evidence_citations, confidence=body.confidence,
        agent_trace={"event_id": ws.event_id, "reply_to": body.reply_to},
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


# ---------------------------------------------------------------------------
# Custom screens
# ---------------------------------------------------------------------------

MAX_PENDING_SUGGESTIONS = 5


class ScreenBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: str = Field("", max_length=2000)
    definition: Dict[str, Any] = Field(..., description="The screen: source, columns, filters, sort, group_by, totals")
    rationale: Optional[str] = Field(None, max_length=1000, description="Why this screen is worth having (shown with a suggestion)")


class ScreenChange(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=120)
    description: Optional[str] = Field(None, max_length=2000)
    definition: Optional[Dict[str, Any]] = None


class ScreenRunBody(BaseModel):
    definition: Optional[Dict[str, Any]] = Field(None, description="Only for /screens/preview: the unsaved screen to try")
    offset: int = Field(0, ge=0)
    limit: int = Field(25, ge=1, le=200)
    filters: List[screen_engine.Filter] = Field(default_factory=list, max_length=10)
    sort: Optional[List[screen_engine.Sort]] = Field(None, max_length=3)
    search: str = Field("", max_length=200, description="Words that must all appear somewhere in a row")


async def _on_a_persons_request(ws: Workspace) -> Optional[Dict[str, Any]]:
    """Who the agent is acting for in a private chat (screens it builds there are theirs to keep), else None."""
    event = await ws.db.get(AgentEventOutbox, ws.event_id)
    if event and event.event_type == "human.message" and event.payload.get("conversation_kind") == "chat" and (event.actor or {}).get("type") == "user":
        return event.actor
    return None


@router.get("/screens")
async def agent_list_screens(ws: Workspace = Depends(get_workspace)):
    """The workspace's screens with their definitions (suggested ones too), so you can build on them rather than repeat them."""
    return [screen_service.detail(s) for s in await screen_service.list_screens(ws.db, ws.template_id)]


@router.get("/screens/catalogue")
async def agent_screen_catalogue(ws: Workspace = Depends(get_workspace)):
    return {**screen_engine.catalogue(ws.template.schema_definition), "functions": formula.functions_help(),
            "aggregates": list(screen_engine.AGGREGATES), "filter_ops": list(screen_engine.FILTER_OPS)}


@router.post("/screens/preview")
async def agent_preview_screen(body: ScreenRunBody, ws: Workspace = Depends(get_workspace)):
    """Tries a screen without saving it: what ad-hoc analysis is. Problems come back as sentences to fix."""
    if body.definition is None:
        raise HTTPException(status_code=422, detail="Send the definition to try.")
    parsed = screen_service.check(body.definition, ws.template.schema_definition)
    return await screen_engine.run_screen(ws.db, template_id=ws.template_id, schema=ws.template.schema_definition, definition=parsed, hidden=ws.hidden,
                                          offset=body.offset, limit=body.limit, extra_filters=body.filters, extra_sort=body.sort)


@router.post("/screens/{slug}/run")
async def agent_run_screen(slug: str, body: ScreenRunBody, ws: Workspace = Depends(get_workspace)):
    """A saved screen's rows now, with extra filters or another order for this look only: how you answer from a screen."""
    s = await screen_service.get(ws.db, ws.template_id, slug)
    result = await screen_engine.run_screen(
        ws.db, template_id=ws.template_id, schema=ws.template.schema_definition, definition=screen_engine.ScreenDefinition.model_validate(s.definition),
        hidden=ws.hidden, offset=body.offset, limit=body.limit, extra_filters=body.filters, extra_sort=body.sort, search=body.search)
    return {"screen": screen_service.summary(s), **result}


@router.post("/screens", status_code=201)
async def agent_save_screen(body: ScreenBody, ws: Workspace = Depends(get_workspace)):
    """
    Saves a screen. For a person who asked for it in their chat it is theirs at once (it appears in the sidebar). One you thought of
    yourself is only a suggestion: it waits, with your reason, until a person keeps it.
    """
    actor = await _on_a_persons_request(ws)
    if actor is None:
        pending = (await ws.db.execute(select(func.count()).select_from(WorkspaceScreen).where(
            WorkspaceScreen.template_id == ws.template_id, WorkspaceScreen.status == "suggested"))).scalar_one()
        if pending >= MAX_PENDING_SUGGESTIONS:
            raise HTTPException(status_code=409, detail=f"{pending} suggestions are already waiting for a person. Wait for them to be kept or dismissed.")
    s = await screen_service.create(
        ws.db, tenant_id=ws.tenant_id, template_id=ws.template_id, schema=ws.template.schema_definition, name=body.name, description=body.description,
        definition=body.definition, actor="agent", actor_kind="agent", status="active" if actor else "suggested", rationale=body.rationale)
    if actor is None:
        await workspace_events.emit(
            ws.db, tenant_id=ws.tenant_id, template_id=ws.template_id, type="agent.screen", title="Syruss suggests a screen", body=s.name,
            path="screens/suggested", data={"screen": s.slug})
    await ws.db.commit()
    await screen_service.announce(s, "suggested" if actor is None else "created")
    await workspace_events.dispatch(ws.db)
    return screen_service.detail(s)


@router.patch("/screens/{slug}")
async def agent_change_screen(slug: str, body: ScreenChange, ws: Workspace = Depends(get_workspace)):
    if await _on_a_persons_request(ws) is None:
        raise HTTPException(status_code=403, detail="You change a screen only when a person asks you to, in their chat. Suggest a new one instead.")
    s = await screen_service.get(ws.db, ws.template_id, slug)
    if body.name is not None:
        s.name = body.name.strip()[:120] or s.name
    if body.description is not None:
        s.description = body.description.strip()
    if body.definition is not None:
        s.definition = screen_service.check(body.definition, ws.template.schema_definition).model_dump(exclude_none=True)
    _audit(ws, "SCREEN_UPDATED", "Screen", s.id, {"name": s.name})
    await ws.db.commit()
    await screen_service.announce(s, "updated")
    return screen_service.detail(s)


@router.delete("/screens/{slug}", status_code=204)
async def agent_remove_screen(slug: str, ws: Workspace = Depends(get_workspace)):
    if await _on_a_persons_request(ws) is None:
        raise HTTPException(status_code=403, detail="You remove a screen only when a person asks you to, in their chat.")
    s = await screen_service.get(ws.db, ws.template_id, slug)
    _audit(ws, "SCREEN_DELETED", "Screen", s.id, {"name": s.name})
    await screen_service.announce(s, "deleted")
    await ws.db.delete(s)
    await ws.db.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Proposals (changes that need a human's approval)
# ---------------------------------------------------------------------------

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
        await _document(ws, doc_id)

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
    _audit(ws, "PROPOSAL_CREATED", "KnowledgeProposal", proposal.id, {"type": body.proposal_type.value})
    await workspace_events.emit(
        ws.db, tenant_id=ws.tenant_id, template_id=ws.template_id, type="agent.proposal",
        title="The agent proposes a template change" if body.proposal_type == KnowledgeProposalType.TEMPLATE_CHANGE else "The agent proposes a change",
        body=body.observation[:300], path="setup" if body.proposal_type == KnowledgeProposalType.TEMPLATE_CHANGE else "agent",
        data={"proposal_id": proposal.id},
    )
    await ws.db.commit()
    await workspace_events.dispatch(ws.db)

    payload = _proposal_dict(proposal)
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


# ---------------------------------------------------------------------------
# Knowledge sources (reference data: the agent sees the records, never the credentials)
# ---------------------------------------------------------------------------

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
    _audit(ws, "PROPOSAL_CREATED", "KnowledgeProposal", proposal.id, {"type": proposal.proposal_type, "source_id": source.id})
    await workspace_events.emit(
        ws.db, tenant_id=ws.tenant_id, template_id=ws.template_id, type="agent.proposal",
        title=f"A mapping for '{source.name}' is ready to review", body=body.observation[:300],
        path="knowledge?tab=sources", data={"proposal_id": proposal.id, "source_id": source.id},
    )
    await ws.db.commit()
    await workspace_events.dispatch(ws.db)
    payload = _proposal_dict(proposal)
    await publish_ui_event(f"template:{ws.template_id}", "agent_proposal", payload, tenant_id=ws.tenant_id, template_id=ws.template_id)
    return {"proposal": payload, "preview": preview}


# ---------------------------------------------------------------------------
# Memory (privacy follows the run's origin: personal memory only for the person it's about)
# ---------------------------------------------------------------------------

async def _actor(ws: Workspace) -> Dict[str, Any]:
    """Who the run is for: a person chatting (with their conversation), or nobody in particular."""
    event = await ws.db.get(AgentEventOutbox, ws.event_id)
    return (event.actor or {}) if event else {}


class RememberBody(BaseModel):
    scope: str = Field("workspace", pattern="^(workspace|personal)$")
    kind: str = Field(..., pattern="^(lesson|observation|preference|fact)$")
    content: str = Field(..., min_length=3, max_length=2000)
    importance: float = Field(0.5, ge=0, le=1)
    data: Dict[str, Any] = Field(default_factory=dict)
    document_ids: List[str] = Field(default_factory=list)
    embedding: Optional[List[float]] = None


@router.post("/memories", status_code=201)
async def remember(body: RememberBody, ws: Workspace = Depends(get_workspace)):
    actor = await _actor(ws)
    user_id = None
    if body.scope == "personal":
        if actor.get("type") != "user" or actor.get("source") != "chat":
            raise HTTPException(status_code=403, detail="Personal memories come only from the person's own conversations with you.")
        if body.kind not in ("preference", "fact"):
            raise HTTPException(status_code=422, detail="Personal memories are preferences or facts about the person.")
        user_id = actor.get("id")
    elif body.kind == "preference":
        raise HTTPException(status_code=422, detail="Preferences belong to a person: use scope personal.")
    memory, created = await memory_service.add(
        ws.db, tenant_id=ws.tenant_id, template_id=ws.template_id, scope=body.scope, kind=body.kind, content=body.content,
        created_by="agent", user_id=user_id, importance=body.importance, data=body.data, embedding=body.embedding,
        source={"type": "conversation" if actor.get("source") == "chat" else "run", "event_id": ws.event_id,
                "conversation_id": actor.get("conversation_id"), "document_ids": body.document_ids},
    )
    _audit(ws, "MEMORY_SAVED" if created else "MEMORY_REINFORCED", "Memory", memory.id, {"scope": body.scope, "kind": body.kind})
    await ws.db.commit()
    return {**memory_service.memory_dict(memory), "created": created}


class RecallBody(BaseModel):
    query: str = ""
    embedding: Optional[List[float]] = None
    scopes: List[str] = Field(default_factory=lambda: ["workspace"])
    kinds: Optional[List[str]] = None
    limit: int = Field(8, ge=1, le=40)


@router.post("/memories/search")
async def recall(body: RecallBody, ws: Workspace = Depends(get_workspace)):
    actor = await _actor(ws)
    in_chat = actor.get("type") == "user" and actor.get("source") == "chat"
    found = await memory_service.search(
        ws.db, template_id=ws.template_id, query=body.query, embedding=body.embedding, scopes=body.scopes,
        user_id=actor.get("id") if in_chat else None, conversation_id=actor.get("conversation_id") if in_chat else None,
        kinds=body.kinds, limit=body.limit,
    )
    await ws.db.commit()
    return [memory_service.memory_dict(m, score) for m, score in found]


class MemoryPatch(BaseModel):
    content: Optional[str] = Field(None, min_length=3, max_length=2000)
    importance: Optional[float] = Field(None, ge=0, le=1)
    archived: Optional[bool] = None
    data: Optional[Dict[str, Any]] = None
    embedding: Optional[List[float]] = None


@router.patch("/memories/{memory_id}")
async def update_memory(memory_id: str, body: MemoryPatch, ws: Workspace = Depends(get_workspace)):
    memory = await ws.db.get(Memory, memory_id)
    actor = await _actor(ws)
    if not memory or memory.template_id != ws.template_id or (memory.scope != "workspace" and memory.user_id != actor.get("id")):
        raise HTTPException(status_code=404, detail="Memory not found.")
    if body.content is not None and body.content != memory.content:
        memory.content, memory.needs_embedding = body.content.strip(), True
    for field in ("importance", "archived"):
        if getattr(body, field) is not None:
            setattr(memory, field, getattr(body, field))
    if body.data is not None:
        memory.data = {**(memory.data or {}), **body.data}
    await ws.db.flush()
    await memory_service.set_embedding(ws.db, memory.id, body.embedding)
    await ws.db.commit()
    return memory_service.memory_dict(memory)


@router.get("/memories/embedding-queue")
async def embedding_queue(limit: int = Query(20, ge=1, le=100), ws: Workspace = Depends(get_workspace)):
    """Memories whose text has no up-to-date embedding (empty when the database has no pgvector)."""
    return [{"id": m.id, "content": m.content} for m in await memory_service.needing_embeddings(ws.db, ws.template_id, limit)]


class EmbeddingBody(BaseModel):
    embedding: List[float]


@router.put("/memories/{memory_id}/embedding")
async def store_embedding(memory_id: str, body: EmbeddingBody, ws: Workspace = Depends(get_workspace)):
    memory = await ws.db.get(Memory, memory_id)
    if not memory or memory.template_id != ws.template_id:
        raise HTTPException(status_code=404, detail="Memory not found.")
    stored = await memory_service.set_embedding(ws.db, memory.id, body.embedding)
    await ws.db.commit()
    return {"stored": stored}


class LegacyNote(BaseModel):
    kind: str = Field(..., pattern="^(lesson|observation)$")
    content: str
    data: Dict[str, Any] = Field(default_factory=dict)


class ImportBody(BaseModel):
    notes: List[LegacyNote]


@router.post("/memories/import")
async def import_notes(body: ImportBody, ws: Workspace = Depends(get_workspace)):
    """Moves the agent's older notes (kept in its own database before memory lived here)."""
    created = 0
    for note in body.notes:
        _, new = await memory_service.add(
            ws.db, tenant_id=ws.tenant_id, template_id=ws.template_id, scope="workspace", kind=note.kind,
            content=note.content, created_by="agent", data=note.data, source={"type": "import"},
            importance=0.6 if note.kind == "lesson" else 0.4,
        )
        created += new
    await ws.db.commit()
    return {"imported": created}


class SummaryBody(BaseModel):
    content: str = Field(..., min_length=1, max_length=6000)
    covers_until: datetime
    embedding: Optional[List[float]] = None


@router.get("/conversations/{conversation_id}/summary")
async def get_summary(conversation_id: str, ws: Workspace = Depends(get_workspace)):
    summary = await memory_service.conversation_summary(ws.db, ws.template_id, conversation_id)
    return {"content": summary.content, "covers_until": summary.data.get("covers_until")} if summary else {"content": None, "covers_until": None}


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


@router.get("/proposals")
async def list_proposals(
    status: Optional[KnowledgeProposalStatus] = None, limit: int = Query(20, le=100), ws: Workspace = Depends(get_workspace),
):
    stmt = select(KnowledgeProposal).where(KnowledgeProposal.template_id == ws.template_id)
    if status:
        stmt = stmt.where(KnowledgeProposal.status == status)
    rows = (await ws.db.execute(stmt.order_by(KnowledgeProposal.created_at.desc()).limit(limit))).scalars().all()
    return [_proposal_dict(p) for p in rows]


# ---------------------------------------------------------------------------
# Traces
# ---------------------------------------------------------------------------

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


@router.post("/traces", status_code=201)
async def report_trace(body: TraceBody, ws: Workspace = Depends(get_workspace)):
    if body.document_id:
        await _document(ws, body.document_id)
    trace = AgentExecutionTrace(
        id=str(uuid.uuid4()), tenant_id=ws.tenant_id, template_id=ws.template_id, document_id=body.document_id,
        profile=body.trigger, model=body.model, status=body.status, started_at=body.started_at,
        completed_at=body.completed_at,
        total_duration_ms=(body.completed_at - body.started_at).total_seconds() * 1000,
        steps=body.steps, final_thought=body.final_thought, usage=body.usage, error_message=body.error_message,
        run_id=body.run_id, event_id=ws.event_id,
        origin_kind=body.origin.get("origin") or "system", origin_source=body.origin.get("source") or "system",
        actor_type=body.origin.get("type") or "system", actor_id=body.origin.get("id"), actor_name=body.origin.get("name"),
        conversation_id=body.origin.get("conversation_id"), model_calls=body.model_calls,
        input_tokens=int(body.usage.get("input_tokens") or 0), output_tokens=int(body.usage.get("output_tokens") or 0),
        cost_usd=body.cost_usd,
    )
    ws.db.add(trace)
    await ws.db.flush()
    await budget_service.check_alerts(ws.db, tenant_id=ws.tenant_id, template_id=ws.template_id)
    await ws.db.commit()
    await workspace_events.dispatch(ws.db)
    budget_service.forget(ws.template_id)
    return {"id": trace.id}
