"""
Documents: reading them (within a page budget), their status and identification, and starting work on them.
"""
import io
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from PIL import Image
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from app.core.permissions import permissions_of
from app.models.document import Document, DocumentStatus, DocumentRole
from app.models.outbox import AgentEventOutbox
from app.models.workspace_admin import WorkspaceMember
from app.models.page_view import AgentPageView
from app.services import eval_service, extraction_log, system_lessons, settings_service, workspace_events
from app.services.agent_events import agent_event_relay, enqueue_agent_event
from app.services.document_key import document_label, identification_key, identification_spec
from app.services.storage_service import storage_service
from app.services.live.ui_events import publish_ui_event
from app.api.internal.agent_tools.scope import Workspace, get_workspace
from app.api.internal.agent_tools.common import audit, document_in


router = APIRouter()


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
        "id": d.id, "name": document_label(d), "filename": d.original_filename, "status": d.status.value, "role": d.role.value,
        "content_type": d.content_type, "created_at": d.created_at.isoformat(),
    } for d in docs]


@router.get("/documents/{document_id}")
async def get_document(document_id: str, ws: Workspace = Depends(get_workspace)):
    doc = await document_in(ws, document_id)
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
        return {"id": doc.id, "name": doc.original_filename, "filename": doc.original_filename, "status": "QUEUED", "role": doc.role.value,
                "content_type": doc.content_type, "image_size": {"width": width, "height": height},
                "record": None, "review": None}
    return {
        "id": doc.id, "name": document_label(doc), "filename": doc.original_filename, "status": doc.status.value, "role": doc.role.value,
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
                f"{document_label(doc)} was already read: its record is in the database. You have looked at "
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
    doc = await document_in(ws, document_id)
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


class IdentifyBody(BaseModel):
    details: Dict[str, Any] = Field(..., description='What the page says, shaped like a record: {"Head": {"name": "...", "date": "2026-07-22"}}')


@router.post("/documents/{document_id}/identification")
async def identify_document(document_id: str, body: IdentifyBody, ws: Workspace = Depends(get_workspace)):
    """The agent read what names this document (the template's identification key) before it has a record to save."""
    doc = await document_in(ws, document_id)
    if ws.eval_item:
        raise HTTPException(status_code=403, detail="Not during an evaluation.")
    if not identification_spec(ws.template.schema_definition)["parts"]:
        raise HTTPException(status_code=409, detail="This template has no identification key; the document is named by its file name.")
    key = identification_key(ws.template.schema_definition, body.details)
    if not key:
        raise HTTPException(status_code=422, detail="None of the values the template's identification key uses were given. "
                            "Give them shaped like a record, for example " + _key_example(ws.template.schema_definition))
    doc.identified_key = key
    await ws.db.commit()
    await publish_ui_event(doc.id, "document_status", {"status": doc.status.value}, tenant_id=ws.tenant_id, template_id=ws.template_id)
    return {"document_id": doc.id, "name": key}


def _key_example(schema: Dict[str, Any]) -> str:
    example: Dict[str, Dict[str, str]] = {}
    for part in identification_spec(schema)["parts"]:
        example.setdefault(part["object"], {})[part["field"]] = "…"
    return json.dumps(example)


@router.post("/documents/{document_id}/status")
async def set_document_status(document_id: str, body: DocumentStatusUpdate, ws: Workspace = Depends(get_workspace)):
    """The agent may mark work in progress or a failure it cannot recover from; outcomes go through /record."""
    if body.status not in (DocumentStatus.PROCESSING, DocumentStatus.AWAITING_INPUT, DocumentStatus.FAILED):
        raise HTTPException(status_code=400, detail="Use /record to complete a document or send it to review.")
    doc = await document_in(ws, document_id)
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
    audit(ws, f"DOCUMENT_{body.status.value}", "Document", doc.id, {"message": body.message})
    if body.status == DocumentStatus.FAILED:
        await workspace_events.emit(
            ws.db, tenant_id=ws.tenant_id, template_id=ws.template_id, type="document.failed", severity="error",
            title=f"Couldn't process {document_label(doc)}", body=body.message or "", path=f"documents/{doc.id}",
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
                                document_id=doc.id, document_name=document_label(doc), actor="Agent", event_id=ws.event_id)
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
    doc = await document_in(ws, document_id)
    if doc.role != DocumentRole.DOCUMENT:
        raise HTTPException(status_code=400, detail="Samples are not processed into records.")
    if doc.status in (DocumentStatus.QUEUED, DocumentStatus.PROCESSING):
        raise HTTPException(status_code=409, detail=f"{document_label(doc)} is already {doc.status.value.lower()}.")
    if not ws.template.active_version_id:
        raise HTTPException(status_code=409, detail="The workspace has no published template yet.")
    doc.status, doc.error_message = DocumentStatus.QUEUED, None
    enqueue_agent_event(
        ws.db, event_type="document.submitted", tenant_id=ws.tenant_id, template_id=ws.template_id,
        payload={"document_id": doc.id}, actor_id=actor["id"], actor_type="user", actor_name=actor.get("name"),
        source="chat", conversation_id=actor.get("conversation_id"),
    )
    audit(ws, "DOCUMENT_QUEUED_FROM_CHAT", "Document", doc.id, {"for": actor.get("id"), "reason": body.reason})
    await ws.db.commit()
    agent_event_relay.notify()
    return {"document_id": doc.id, "name": document_label(doc), "filename": doc.original_filename, "status": doc.status.value}
