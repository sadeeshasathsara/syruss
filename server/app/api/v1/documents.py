import io
import re
from collections import defaultdict
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db_session
from app.core.auth import AuthContext, get_current_auth
from app.core.scope import TemplateScope, get_template_scope, resolve_template_scope
from app.models.audit import AuditLog
from app.models.document import DocumentRole, DocumentStatus
from app.schemas.conversation import ChatMessageCreate, ConversationCreate
from app.models.trace import AgentExecutionTrace
from app.schemas.document import DocumentResponse, DocumentStatusResponse
from app.schemas.trace import AgentExecutionTraceResponse
from app.services.agent_events import enqueue_agent_event, agent_event_relay
from app.services.document_service import document_service
from app.services.conversation_service import conversation_service
from app.services.storage_service import storage_service
from app.core.permissions import Access, require
from app.core.permissions import check_permission
from app.services import extraction_log, quality_service, settings_service, workspace_events
from app.services.record_service import record_service

router = APIRouter(prefix="/documents", tags=["Documents"])


async def _log(scope, doc, stage: str, message: str, *, level: str = "info", actor: str = "System") -> None:
    await extraction_log.record(tenant_id=scope.tenant_id, template_id=scope.template_id, stage=stage, message=message, level=level,
                                document_id=doc.id, document_name=doc.original_filename, actor=actor)


@router.post("/upload", response_model=DocumentResponse, status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    template_id: Optional[str] = Form(None),
    process: Optional[bool] = Form(None, description="Hand the document to the agent right away (default: the workspace's setting)"),
    x_template_id: Optional[str] = Header(None, alias="X-Template-ID"),
    x_conversation_id: Optional[str] = Header(None, alias="X-Conversation-ID"),
    db: AsyncSession = Depends(get_db_session),
    auth: AuthContext = Depends(get_current_auth),
):
    """Stores a document in the workspace and, by default, hands it to the agent straight away."""
    scope = await resolve_template_scope(db, auth, template_id or x_template_id)
    await check_permission(db, scope, auth, "documents.upload")
    intake = (await settings_service.get_settings(db, scope.template_id)).documents
    if process is None:
        process = intake.auto_process
    if file.size is not None and file.size > intake.max_file_mb * 1_000_000:
        raise HTTPException(status_code=413, detail=f"Files can be up to {intake.max_file_mb} MB in this workspace.")
    await settings_service.check_limit(db, scope.template_id, "documents_per_month")
    if process and not scope.template.active_version_id:
        raise HTTPException(status_code=409, detail="This workspace has no published template yet.")
    doc = await document_service.store_document(
        db, tenant_id=scope.tenant_id, template_id=scope.template_id, file=file, actor=scope.user_id, allowed_types=intake.allowed_types,
    )
    await _warn_near_limit(db, scope)
    if process:
        doc.status = DocumentStatus.QUEUED
        enqueue_agent_event(
            db, event_type="document.submitted", tenant_id=scope.tenant_id, template_id=scope.template_id,
            payload={"document_id": doc.id}, actor_id=scope.user_id, actor_type="user", actor_name=scope.username,
            conversation_id=await conversation_service.linked_conversation(db, scope, x_conversation_id),
        )
    await db.commit()
    await workspace_events.dispatch(db)
    if process:
        agent_event_relay.notify()
    await db.refresh(doc)
    await _log(scope, doc, "received", f"{doc.original_filename} was uploaded.", actor=scope.username)
    if process:
        await _log(scope, doc, "queued", "Handed to the agent. It will start reading shortly.")
    return doc


async def _warn_near_limit(db: AsyncSession, scope: TemplateScope) -> None:
    """Tells the people who manage billing when the month's documents reach 80% and 100% of the plan."""
    plan = settings_service.PLANS[(await settings_service.get_settings(db, scope.template_id)).plan]
    used = (await settings_service.usage(db, scope.template_id))["documents_this_month"]
    limit = plan["documents_per_month"]
    if used in (int(limit * 0.8), limit):
        await workspace_events.emit(
            db, tenant_id=scope.tenant_id, template_id=scope.template_id, type="usage.limit",
            severity="warning" if used < limit else "error", path="settings/general/billing",
            title=f"{used} of {limit} documents used this month",
            body=f"The {plan['name']} plan allows {limit} documents a month." + (" New uploads are refused until next month." if used >= limit else ""),
        )


@router.get("", response_model=List[DocumentResponse], dependencies=[Depends(require("documents.view"))])
async def list_documents(
    status: Optional[DocumentStatus] = None,
    role: DocumentRole = DocumentRole.DOCUMENT,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db_session),
    scope: TemplateScope = Depends(get_template_scope),
):
    """Documents in the workspace; `role=SAMPLE` lists the samples the template was designed from."""
    return await document_service.list_documents(
        db, scope.tenant_id, scope.template_id, status=status, role=role, limit=limit, offset=offset,
    )


@router.get("/{document_id}", response_model=DocumentResponse, dependencies=[Depends(require("documents.view"))])
async def get_document(
    document_id: str,
    db: AsyncSession = Depends(get_db_session),
    scope: TemplateScope = Depends(get_template_scope),
):
    return await document_service.get_document(db, scope.tenant_id, scope.template_id, document_id)


@router.get("/{document_id}/file", dependencies=[Depends(require("documents.view"))])
async def download_document_file(
    document_id: str,
    db: AsyncSession = Depends(get_db_session),
    scope: TemplateScope = Depends(get_template_scope),
):
    doc = await document_service.get_document(db, scope.tenant_id, scope.template_id, document_id)
    return StreamingResponse(
        io.BytesIO(storage_service.download_file(doc.minio_object_key)),
        media_type=doc.content_type,
        headers={"Content-Disposition": f'inline; filename="{doc.original_filename}"'},
    )


@router.post("/{document_id}/process", status_code=202)
async def process_document(
    document_id: str,
    x_conversation_id: Optional[str] = Header(None, alias="X-Conversation-ID"),
    db: AsyncSession = Depends(get_db_session),
    scope: TemplateScope = Depends(get_template_scope),
    _access: Access = Depends(require("documents.upload")),
):
    """Hands the document to the workspace agent."""
    doc = await document_service.get_document(db, scope.tenant_id, scope.template_id, document_id)
    if doc.status in (DocumentStatus.QUEUED, DocumentStatus.PROCESSING):
        raise HTTPException(status_code=409, detail=f"Document is already {doc.status.value.lower()}.")
    if not scope.template.active_version_id:
        raise HTTPException(status_code=409, detail="This template has no active version yet. Finish the template first.")
    doc.status = DocumentStatus.QUEUED
    doc.error_message = None
    enqueue_agent_event(
        db, event_type="document.submitted", tenant_id=scope.tenant_id, template_id=scope.template_id,
        payload={"document_id": doc.id}, actor_id=scope.user_id, actor_type="user", actor_name=scope.username,
        conversation_id=await conversation_service.linked_conversation(db, scope, x_conversation_id),
    )
    await db.commit()
    agent_event_relay.notify()
    await _log(scope, doc, "queued", "Sent to the agent to be read again.", actor=scope.username)
    return {"message": "Document handed to the workspace agent", "document_id": doc.id, "status": doc.status.value}


class ObjectCorrection(BaseModel):
    object: str
    reason: str = Field("", max_length=2000, description="What was wrong, in the person's words")


class CorrectionIn(BaseModel):
    data: Dict[str, Any] = Field(..., description="The whole corrected record")
    reasons: List[ObjectCorrection] = Field(default_factory=list)


def _flat(value: Any, prefix: str = "") -> Dict[str, str]:
    """Every value in a record by its path ("Guard[1].name"), exactly as written."""
    out: Dict[str, str] = {}
    if isinstance(value, dict):
        for k, v in value.items():
            out.update(_flat(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            out.update(_flat(v, f"{prefix}[{i}]"))
    elif value not in (None, ""):
        out[prefix] = str(value).strip()
    return out


def _changes(before: Dict[str, Any], after: Dict[str, Any]) -> List[Dict[str, Any]]:
    """What a person changed: case and spacing inside a value count."""
    old, new = _flat(before), _flat(after)
    return [{"path": p, "field": quality_service.field_of(p), "from": old.get(p), "to": new.get(p)}
            for p in sorted(set(old) | set(new)) if old.get(p) != new.get(p)]


def _humanize(name: str) -> str:
    s = re.sub(r"[_-]+", " ", name).strip()
    return s[:1].upper() + s[1:]


def _correction_message(filename: str, changes: List[Dict[str, Any]], reasons: Dict[str, str]) -> str:
    """What a person changed and why, as their chat message to Syruss."""
    by_object: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for c in changes:
        by_object[re.split(r"[\[.]", c["path"], maxsplit=1)[0]].append(c)
    lines = [f"I corrected the saved record of **{filename}**."]
    for obj, items in by_object.items():
        why = reasons.get(obj, "").strip()
        lines.append(f"\n**{_humanize(obj)}**: {why if why else '(no reason given)'}")
        for c in items:
            row = re.search(r"\[(\d+)\]", c["path"])
            where = f"{_humanize(c['field'].split('.')[-1])}" + (f" (row {int(row.group(1)) + 1})" if row else "")
            lines.append(f"- {where}: \u201c{c['from'] or 'empty'}\u201d \u2192 \u201c{c['to'] or 'empty'}\u201d")
    return "\n".join(lines)


@router.post("/{document_id}/corrections")
async def correct_record(
    document_id: str,
    body: CorrectionIn,
    x_conversation_id: Optional[str] = Header(None, alias="X-Conversation-ID"),
    db: AsyncSession = Depends(get_db_session),
    scope: TemplateScope = Depends(get_template_scope),
    _access: Access = Depends(require("documents.review")),
):
    """
    A person corrects a saved record. The corrected record must pass the template like any other.
    What they changed, and why, goes to Syruss as their message in the chat they have open (or a
    new one), so it can learn from it and answer.
    """
    doc = await document_service.get_document(db, scope.tenant_id, scope.template_id, document_id)
    if doc.status != DocumentStatus.COMPLETED or doc.extraction is None:
        raise HTTPException(status_code=409, detail="Only saved records can be corrected here. A document waiting for review is corrected in Review.")
    before = doc.extraction.extracted_data or {}
    changes = _changes(before, body.data)
    if not changes:
        raise HTTPException(status_code=400, detail="Nothing changed.")
    await record_service.submit(
        db, template=scope.template, document=doc, data=body.data, decision="COMMIT", confidence=1.0,
        evidence=doc.extraction.evidence_records, notes=doc.extraction.agent_notes, actor=scope.user_id,
        discard_pending_review=False, check_identities=False,
    )
    reasons = {r.object: r.reason for r in body.reasons}
    db.add(AuditLog(
        tenant_id=scope.tenant_id, template_id=scope.template_id, event_type="RECORD_CORRECTED", entity_type="Document",
        entity_id=doc.id, actor=scope.user_id, details={"changes": changes, "reasons": reasons},
    ))
    await _log(scope, doc, "corrected", f"{scope.username} corrected {len(changes)} field{'s' if len(changes) != 1 else ''}.", actor=scope.username)
    conv_id = await conversation_service.linked_conversation(db, scope, x_conversation_id)
    if conv_id is None:
        conv_id = (await conversation_service.create_conversation(
            db, scope, ConversationCreate(title=f"Correction: {doc.original_filename}"[:120]))).id
    conv = await conversation_service.get_conversation(db, scope, conv_id)
    msg = await conversation_service.post_user_message(db, scope, conv, ChatMessageCreate(
        content=_correction_message(doc.original_filename, changes, reasons),
        context={"current_page": f"documents/{doc.id}", "kind": "record_correction", "document_id": doc.id},
    ))
    return {"status": doc.status.value, "conversation_id": conv.id, "message_id": msg.id, "changes": changes}


@router.get("/{document_id}/status", response_model=DocumentStatusResponse, dependencies=[Depends(require("documents.view"))])
async def get_document_status(
    document_id: str,
    db: AsyncSession = Depends(get_db_session),
    scope: TemplateScope = Depends(get_template_scope),
):
    doc = await document_service.get_document(db, scope.tenant_id, scope.template_id, document_id)
    return DocumentStatusResponse(
        id=doc.id,
        status=doc.status,
        error_message=doc.error_message,
        has_extraction=doc.extraction is not None,
        needs_human_review=doc.status == DocumentStatus.NEEDS_REVIEW,
        updated_at=doc.updated_at,
    )


@router.get("/{document_id}/trace", response_model=AgentExecutionTraceResponse, dependencies=[Depends(require("documents.view"))])
async def get_document_trace(
    document_id: str,
    db: AsyncSession = Depends(get_db_session),
    scope: TemplateScope = Depends(get_template_scope),
):
    """The most recent agent run that worked on this document."""
    await document_service.get_document(db, scope.tenant_id, scope.template_id, document_id)
    trace = (await db.execute(
        select(AgentExecutionTrace)
        .where(AgentExecutionTrace.document_id == document_id, AgentExecutionTrace.template_id == scope.template_id)
        .order_by(AgentExecutionTrace.started_at.desc())
    )).scalars().first()
    if not trace:
        raise HTTPException(status_code=404, detail="No agent run recorded for this document yet.")
    return trace
