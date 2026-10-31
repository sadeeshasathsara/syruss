"""
The endpoints other systems call to send documents in. Everything here is authenticated by an intake key (see core/auth/intake_keys.py):
the key fixes the organization, workspace, channel and mode, and nothing in the request can change them.

    POST /intake/documents              send a file (multipart); answers 202 with where to check on it
    GET  /intake/documents/{id}         where it is now (and the approved record once it is approved)
    GET  /intake/documents              look up by your own reference: ?external_id=INV-1001
    POST /intake/uploads                for big files: get a one-time link that sends the file straight to storage
    POST /intake/uploads/{id}/complete  say the file was sent; it is then checked and queued like any other
    GET  /intake/me                     what this key is, and what the channel accepts
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, File, Form, Header, Query, Request, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.auth.intake_keys import IntakeCaller, intake_caller
from app.core.config import settings
from app.core.database import get_db_session
from app.models.document import Document, DocumentStatus
from app.models.intake import IntakeUpload
from app.services import intake_service, settings_service
from app.services.intake_rules import IntakeRejected, SUPPORTED, problem, read_capped
from app.services.storage_service import storage_service

router = APIRouter(prefix="/intake", tags=["Intake"])


def _status_url(doc_id: str) -> str:
    return f"{settings.PUBLIC_API_URL.rstrip('/')}{settings.API_V1_PREFIX}/intake/documents/{doc_id}"


def _view(doc: Document, *, duplicate: bool = False, with_record: bool = False) -> Dict[str, Any]:
    """A document as its sender sees it: simple words, its own reference, never internals."""
    out: Dict[str, Any] = {
        "id": doc.id, "status": intake_service.sender_status(doc), "external_id": doc.external_id, "filename": doc.original_filename,
        "metadata": doc.sender_metadata or {}, "received_at": doc.created_at, "updated_at": doc.updated_at, "test": doc.is_test,
        "status_url": _status_url(doc.id),
    }
    if duplicate:
        out["duplicate"] = True
    if doc.status == DocumentStatus.FAILED:
        out["error"] = doc.error_message or "The document could not be read."
    if with_record and doc.status == DocumentStatus.COMPLETED and doc.extraction is not None:
        out["record"] = {"data": doc.extraction.extracted_data, "confidence": doc.extraction.overall_confidence,
                         "approved_at": doc.extraction.updated_at}
    return out


async def _limits(db: AsyncSession, caller: IntakeCaller) -> Dict[str, Any]:
    prefs = (await settings_service.get_settings(db, caller.template.id)).documents
    return intake_service.channel_limits(caller.channel, prefs)


def _where(caller: IntakeCaller) -> Dict[str, str]:
    return {"tenant_id": caller.template.tenant_id, "template_id": caller.template.id, "channel_id": caller.channel.id}


def _refusal(rejected: IntakeRejected) -> Exception:
    return problem(rejected.status, rejected.reason, rejected.code)


@router.get("/me")
async def who_am_i(caller: IntakeCaller = Depends(intake_caller), db: AsyncSession = Depends(get_db_session)):
    """Lets a sender check its key before it sends anything: the channel, the mode and what is accepted."""
    limits = await _limits(db, caller)
    return {
        "channel": {"id": caller.channel.id, "name": caller.channel.name, "slug": caller.channel.slug, "kind": caller.channel.kind},
        "workspace": {"id": caller.template.id, "name": caller.template.name},
        "mode": caller.key.mode,
        "accepts": {"content_types": limits["allowed_types"], "max_file_bytes": limits["max_bytes"]},
        "limits": {"requests_per_minute": caller.key.rate_limit_per_minute, "documents_per_day": caller.key.daily_quota},
        "upload_url": f"{settings.PUBLIC_API_URL.rstrip('/')}{settings.API_V1_PREFIX}/intake/documents",
    }


@router.post("/documents", status_code=202)
async def send_document(
    request: Request,
    response: Response,
    file: UploadFile = File(...),
    external_id: Optional[str] = Form(None, description="Your own reference for this document (an invoice number, a ticket id)"),
    metadata: Optional[str] = Form(None, description="A JSON object of labels to keep with the document"),
    process: Optional[bool] = Form(None, description="false stores it without reading it yet"),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key", description="Send the same value when retrying, so a retry never makes a second document"),
    caller: IntakeCaller = Depends(intake_caller),
    db: AsyncSession = Depends(get_db_session),
):
    """Sends one document. It is checked, stored and queued; the answer says where to look for the result."""
    sender = caller.sender()
    where = _where(caller)  # read before any rollback expires the objects it comes from
    try:
        limits = await _limits(db, caller)
        content = await read_capped(file, limits["max_bytes"])
        done = await intake_service.accept(
            db, channel=caller.channel, template=caller.template, content=content, filename=file.filename, sender=sender,
            external_id=external_id, metadata=metadata, process=process, idempotency_key=idempotency_key,
            daily_quota=caller.key.daily_quota, actor_id=f"apikey:{caller.key.id}",
        )
        await db.commit()
    except IntakeRejected as rejected:
        await db.rollback()
        await intake_service.reject(**where, sender=sender, reason=rejected.reason, filename=file.filename or "", external_id=external_id)
        raise _refusal(rejected)
    await intake_service.finish(done, caller.channel, sender)
    if done.duplicate:
        response.status_code = 200
        response.headers["Idempotent-Replayed"] = "true"
    return _view(done.document, duplicate=done.duplicate)


async def _own_document(db: AsyncSession, caller: IntakeCaller, document_id: str) -> Document:
    """A document of this key's channel, or 404: other channels' and workspaces' documents are never revealed."""
    doc = await db.get(Document, document_id)
    if doc is None or doc.channel_id != caller.channel.id:
        raise problem(404, "No document with that id was sent through this channel.")
    return doc


@router.get("/documents/{document_id}")
async def document_status(document_id: str, caller: IntakeCaller = Depends(intake_caller), db: AsyncSession = Depends(get_db_session)):
    return _view(await _own_document(db, caller, document_id), with_record=True)


@router.get("/documents")
async def find_documents(
    external_id: Optional[str] = Query(None, description="Your own reference"),
    status: Optional[str] = Query(None, description="received | processing | needs_attention | approved | failed"),
    limit: int = Query(20, ge=1, le=100),
    caller: IntakeCaller = Depends(intake_caller),
    db: AsyncSession = Depends(get_db_session),
):
    stmt = select(Document).where(Document.channel_id == caller.channel.id)
    if external_id:
        stmt = stmt.where(Document.external_id == external_id)
    rows = (await db.execute(stmt.order_by(Document.created_at.desc()).limit(200 if status else limit))).scalars().all()
    items = [_view(d) for d in rows]
    if status:
        items = [i for i in items if i["status"] == status][:limit]
    return {"items": items}


# --- large files: straight to storage ----------------------------------------------------------------------

class UploadIn(BaseModel):
    filename: str = Field(..., min_length=1, max_length=255)
    content_type: str = Field(..., description="image/jpeg, image/png, image/tiff or image/webp")
    size_bytes: int = Field(..., ge=1, description="The file's exact size; storage refuses anything larger")
    external_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None
    process: Optional[bool] = None


@router.post("/uploads", status_code=201)
async def start_upload(body: UploadIn, caller: IntakeCaller = Depends(intake_caller), db: AsyncSession = Depends(get_db_session)):
    """
    For big files: returns a one-time form post that sends the file straight to storage (no size limit of this API in the way). Storage itself
    accepts only this object, of this type, no larger than size_bytes, for 15 minutes. Then call /complete.
    """
    limits = await _limits(db, caller)
    sender = caller.sender("upload")
    where = _where(caller)
    try:
        if body.content_type not in SUPPORTED or body.content_type not in limits["allowed_types"]:
            raise IntakeRejected(415, f"{body.content_type} is not accepted here. This channel accepts: {', '.join(limits['allowed_types'])}.")
        if body.size_bytes > limits["max_bytes"]:
            raise IntakeRejected(413, f"The file is larger than the {limits['max_bytes'] // 1_000_000} MB this channel allows.")
        intake_service.clean_external_id(body.external_id)
        meta = intake_service.clean_metadata(body.metadata)
    except IntakeRejected as rejected:
        await intake_service.reject(**where, sender=sender, reason=rejected.reason, filename=body.filename, size=body.size_bytes, external_id=body.external_id)
        raise _refusal(rejected)
    upload_id = str(uuid.uuid4())
    object_key = f"tenants/{caller.template.tenant_id}/intake-staging/{upload_id}"
    expires = datetime.now(timezone.utc) + timedelta(minutes=settings.INTAKE_UPLOAD_MINUTES)
    form = await asyncio.to_thread(storage_service.presigned_upload_form, object_key, content_type=body.content_type,
                                   max_bytes=body.size_bytes, expires_minutes=settings.INTAKE_UPLOAD_MINUTES)
    db.add(IntakeUpload(
        id=upload_id, tenant_id=caller.template.tenant_id, template_id=caller.template.id, channel_id=caller.channel.id, key_id=caller.key.id,
        object_key=object_key, filename=body.filename[:255], declared_type=body.content_type, max_bytes=body.size_bytes,
        external_id=body.external_id, sender_metadata=meta, process=body.process, expires_at=expires,
    ))
    await db.commit()
    return {
        "upload_id": upload_id, "expires_at": expires, "method": "POST", "url": form["url"], "fields": form["fields"],
        "file_field": "file", "complete_url": f"{settings.PUBLIC_API_URL.rstrip('/')}{settings.API_V1_PREFIX}/intake/uploads/{upload_id}/complete",
        "note": "Post the fields exactly as given, then the file as the last field named 'file'. Then call complete_url.",
    }


@router.post("/uploads/{upload_id}/complete")
async def finish_upload(
    upload_id: str, response: Response,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    caller: IntakeCaller = Depends(intake_caller), db: AsyncSession = Depends(get_db_session),
):
    """Checks the stored file as strictly as a direct upload (its real type, size and picture), then queues it."""
    upload = await db.get(IntakeUpload, upload_id)
    if upload is None or upload.channel_id != caller.channel.id:
        raise problem(404, "No such upload for this channel.")
    if upload.state == "completed" and upload.document_id:
        doc = await db.get(Document, upload.document_id)
        response.headers["Idempotent-Replayed"] = "true"
        return _view(doc, duplicate=True)
    if upload.state != "pending":
        raise problem(409, upload.notes or "This upload was refused.", "upload_refused")
    if upload.expires_at < datetime.now(timezone.utc):
        upload.state = "expired"
        await db.commit()
        await asyncio.to_thread(storage_service.remove_object, upload.object_key)
        raise problem(410, "This upload link has expired. Ask for a new one.")

    sender = caller.sender("upload")
    where = _where(caller)
    object_key, filename, external_id = upload.object_key, upload.filename, upload.external_id
    size = await asyncio.to_thread(storage_service.object_size, object_key)
    if size is None:
        raise problem(409, "The file has not been received yet. Send it to the upload url first.", "not_uploaded")
    try:
        content = await asyncio.to_thread(storage_service.read_object, object_key, upload.max_bytes)
        done = await intake_service.accept(
            db, channel=caller.channel, template=caller.template, content=content, filename=upload.filename, sender=sender,
            external_id=upload.external_id, metadata=upload.sender_metadata, process=upload.process,
            idempotency_key=idempotency_key or f"upload:{upload.id}", daily_quota=caller.key.daily_quota, actor_id=f"apikey:{caller.key.id}",
        )
        upload.state, upload.document_id = "completed", done.document.id
        await db.commit()
    except (IntakeRejected, ValueError) as rejected:
        reason = rejected.reason if isinstance(rejected, IntakeRejected) else "The file is larger than it was declared to be."
        await db.rollback()
        upload = await db.get(IntakeUpload, upload_id)
        upload.state, upload.notes = "rejected", reason
        await db.commit()
        await asyncio.to_thread(storage_service.remove_object, object_key)
        await intake_service.reject(**where, sender=sender, reason=reason, filename=filename, size=size, external_id=external_id)
        raise _refusal(rejected if isinstance(rejected, IntakeRejected) else IntakeRejected(413, reason))
    await asyncio.to_thread(storage_service.remove_object, object_key)
    await intake_service.finish(done, caller.channel, sender)
    if done.duplicate:
        response.headers["Idempotent-Replayed"] = "true"
    return _view(done.document, duplicate=done.duplicate)
