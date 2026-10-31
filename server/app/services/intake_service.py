"""
Documents coming in from other systems. Every way in (REST upload, presigned upload, e-mail, SFTP) ends here, so every document is held to the
same rules and is tied to the channel that received it. The workspace is never named by the sender: it comes from the credential or address.

    accept(...)   checks, de-duplicates, stores, queues for the agent, and logs; raises IntakeRejected with a readable reason
    reject(...)   records a refused attempt (in its own transaction, so it survives the refusal)
"""
import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import AsyncSessionLocal
from app.models.document import Document, DocumentStatus
from app.models.intake import IdempotencyRecord, IntakeChannel, IntakeEvent
from app.models.template import DocumentTemplate
from app.services import extraction_log, settings_service
from app.services.agent_events import agent_event_relay, enqueue_agent_event
from app.services.document_service import document_service
from app.services.intake_rules import IntakeRejected, SUPPORTED, scan_for_malware
from app.services.ui_events import publish_ui_event

EXTERNAL_ID = re.compile(r"^[\w .:/#@+=,-]{1,200}$")
MAX_METADATA_BYTES = 4000
MAX_METADATA_KEYS = 20


@dataclass
class Sender:
    """Who is sending, as the platform knows it (never as the file says)."""
    label: str                   # the key's name, the e-mail address, the SFTP user
    via: str = "api"             # api | upload | email | sftp
    ip: str = ""
    key_id: Optional[str] = None
    test: bool = False


@dataclass
class Accepted:
    document: Document
    duplicate: bool = False


def channel_limits(channel: IntakeChannel, workspace_documents) -> Dict[str, Any]:
    """What this channel allows: it can only narrow what the workspace allows, never widen it."""
    cfg = channel.config or {}
    allowed = [t for t in (cfg.get("allowed_types") or workspace_documents.allowed_types) if t in workspace_documents.allowed_types and t in SUPPORTED]
    max_mb = min(int(cfg.get("max_mb") or workspace_documents.max_file_mb), workspace_documents.max_file_mb)
    process = cfg.get("process")
    return {"allowed_types": allowed or list(workspace_documents.allowed_types), "max_bytes": max_mb * 1_000_000,
            "process": workspace_documents.auto_process if process is None else bool(process)}


def clean_metadata(raw: Any) -> Dict[str, Any]:
    """The sender's labels: a small flat object of text, numbers and booleans, or a refusal."""
    if raw in (None, "", {}):
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            raise IntakeRejected(422, "metadata must be a JSON object, for example {\"branch\": \"North\"}.")
    if not isinstance(raw, dict) or len(raw) > MAX_METADATA_KEYS:
        raise IntakeRejected(422, f"metadata must be a JSON object with at most {MAX_METADATA_KEYS} entries.")
    for k, v in raw.items():
        if not isinstance(v, (str, int, float, bool)) or len(str(k)) > 60:
            raise IntakeRejected(422, "metadata values must be text, numbers or true/false.")
    if len(json.dumps(raw)) > MAX_METADATA_BYTES:
        raise IntakeRejected(422, f"metadata is larger than {MAX_METADATA_BYTES} characters.")
    return {str(k): v for k, v in raw.items()}


def clean_external_id(value: Optional[str]) -> Optional[str]:
    value = (value or "").strip()
    if not value:
        return None
    if not EXTERNAL_ID.match(value):
        raise IntakeRejected(422, "external_id may use letters, numbers and . _ : / # @ + = , - and spaces, up to 200 characters.")
    return value


async def reject(*, tenant_id: str, template_id: str, channel_id: Optional[str], sender: Sender, reason: str, filename: str = "",
                 size: int = 0, external_id: Optional[str] = None) -> None:
    """Writes down a refused attempt. Never raises: a log that fails must not change the answer."""
    try:
        async with AsyncSessionLocal() as db:
            db.add(IntakeEvent(tenant_id=tenant_id, template_id=template_id, channel_id=channel_id, outcome="rejected", reason=reason[:300],
                               filename=(filename or "")[:255], size_bytes=size, external_id=external_id, sender=sender.label[:255], ip=sender.ip[:64],
                               key_id=sender.key_id, via=sender.via))
            await db.commit()
        await publish_ui_event(f"template:{template_id}", "intake_received", {"outcome": "rejected", "reason": reason, "channel_id": channel_id},
                               tenant_id=tenant_id, template_id=template_id)
    except Exception:  # noqa: BLE001
        pass


async def _daily_count(db: AsyncSession, key_id: str) -> int:
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return (await db.execute(select(func.count()).select_from(IntakeEvent).where(
        IntakeEvent.key_id == key_id, IntakeEvent.outcome == "accepted", IntakeEvent.created_at >= start))).scalar_one()


async def accept(db: AsyncSession, *, channel: IntakeChannel, template: DocumentTemplate, content: bytes, filename: Optional[str], sender: Sender,
                 external_id: Optional[str] = None, metadata: Any = None, process: Optional[bool] = None, idempotency_key: Optional[str] = None,
                 daily_quota: Optional[int] = None, actor_id: Optional[str] = None, actor_type: str = "api_key") -> Accepted:
    """Checks one file and, if it passes, stores it and hands it to the agent. The caller commits (and calls `finish`)."""
    if channel.status != "active":
        raise IntakeRejected(403, "This channel is paused. Ask the workspace owner to turn it back on.", "channel_paused")
    prefs = (await settings_service.get_settings(db, template.id)).documents
    limits = channel_limits(channel, prefs)
    if len(content) > limits["max_bytes"]:
        raise IntakeRejected(413, f"The file is larger than the {limits['max_bytes'] // 1_000_000} MB this channel allows.")
    external_id = clean_external_id(external_id)
    meta = clean_metadata(metadata)
    file_hash = hashlib.sha256(content).hexdigest()
    request_hash = hashlib.sha256(f"{file_hash}|{external_id or ''}|{json.dumps(meta, sort_keys=True)}".encode()).hexdigest()

    # The same request again returns the same document: a retry never makes a second one.
    if idempotency_key:
        idempotency_key = idempotency_key.strip()[:120]
        stored = (await db.execute(select(IdempotencyRecord).where(
            IdempotencyRecord.channel_id == channel.id, IdempotencyRecord.key == idempotency_key))).scalar_one_or_none()
        if stored:
            if stored.request_hash != request_hash:
                raise IntakeRejected(422, "This Idempotency-Key was already used with a different file or details. Use a new key for a new request.")
            existing = await db.get(Document, stored.document_id)
            if existing:
                return Accepted(existing, duplicate=True)
    if external_id:
        existing = (await db.execute(select(Document).where(Document.channel_id == channel.id, Document.external_id == external_id))).scalar_one_or_none()
        if existing:
            if existing.file_hash != file_hash:
                raise IntakeRejected(409, f"external_id '{external_id}' was already used for a different file in this channel.")
            return Accepted(existing, duplicate=True)

    if daily_quota is not None and sender.key_id and await _daily_count(db, sender.key_id) >= daily_quota:
        raise IntakeRejected(429, f"This key's limit of {daily_quota} documents a day has been reached. It resets at midnight UTC.", "quota_exceeded")
    if not sender.test:
        try:
            await settings_service.check_limit(db, template.id, "documents_per_month")
        except Exception as exc:  # the plan's monthly allowance
            raise IntakeRejected(402, getattr(exc, "detail", "The plan's monthly document allowance has been reached."))
    want_processing = limits["process"] if process is None else process
    if want_processing and not template.active_version_id:
        raise IntakeRejected(409, "This workspace has no published template yet, so it cannot read documents.")

    await scan_for_malware(content)
    source = {"via": sender.via, "sender": sender.label, "ip": sender.ip, "channel": channel.slug}
    doc = await document_service.store_bytes(
        db, tenant_id=template.tenant_id, template_id=template.id, content=content, filename=filename, actor=sender.label,
        allowed_types=limits["allowed_types"], channel_id=channel.id, external_id=external_id, source=source, sender_metadata=meta or None,
        is_test=sender.test,
    )
    if want_processing:
        doc.status = DocumentStatus.QUEUED
        enqueue_agent_event(
            db, event_type="document.submitted", tenant_id=template.tenant_id, template_id=template.id, payload={"document_id": doc.id},
            actor_id=actor_id or f"intake:{channel.id}", actor_type=actor_type, actor_name=sender.label,
        )
    if idempotency_key:
        db.add(IdempotencyRecord(tenant_id=template.tenant_id, channel_id=channel.id, key=idempotency_key, request_hash=request_hash, document_id=doc.id))
    db.add(IntakeEvent(
        tenant_id=template.tenant_id, template_id=template.id, channel_id=channel.id, outcome="accepted", filename=doc.original_filename,
        size_bytes=len(content), external_id=external_id, document_id=doc.id, sender=sender.label[:255], ip=sender.ip[:64], key_id=sender.key_id,
        via=sender.via,
    ))
    doc._queued = want_processing  # type: ignore[attr-defined]
    return Accepted(doc)


async def finish(accepted: Accepted, channel: IntakeChannel, sender: Sender) -> None:
    """After the commit: the log line people read, the live update, and waking the agent."""
    doc = accepted.document
    if accepted.duplicate:
        return
    await extraction_log.record(
        tenant_id=doc.tenant_id, template_id=doc.template_id, stage="received", document_id=doc.id, document_name=doc.original_filename,
        message=f"{doc.original_filename} arrived through {channel.name} ({sender.label}).", actor=sender.label,
    )
    if getattr(doc, "_queued", False):
        await extraction_log.record(tenant_id=doc.tenant_id, template_id=doc.template_id, stage="queued", document_id=doc.id,
                                    document_name=doc.original_filename, message="Handed to the agent. It will start reading shortly.")
        agent_event_relay.notify()
    await publish_ui_event(f"template:{doc.template_id}", "intake_received", {"outcome": "accepted", "channel_id": channel.id, "document_id": doc.id},
                           tenant_id=doc.tenant_id, template_id=doc.template_id)


def new_token(nbytes: int = 18) -> str:
    return secrets.token_urlsafe(nbytes)


def sender_status(doc: Document, *, held: bool = False) -> str:
    """The state a sender is told: simple words, no internals."""
    s = doc.status
    if s in (DocumentStatus.PENDING, DocumentStatus.QUEUED):
        return "received"
    if s == DocumentStatus.PROCESSING:
        return "processing"
    if s == DocumentStatus.COMPLETED:
        return "approved"
    if s == DocumentStatus.FAILED:
        return "failed"
    return "needs_attention"  # in review, or waiting for an answer: a person is looking at it


async def ingest(channel_id: str, *, content: bytes, filename: str, sender: Sender, external_id: Optional[str] = None, metadata: Any = None) -> Accepted:
    """
    A file that arrived outside the web API (an e-mail attachment, an SFTP upload): checked and queued exactly like one sent through the API,
    in a transaction of its own. A refusal is written to the channel's activity and raised as IntakeRejected for the receiver to answer with.
    """
    async with AsyncSessionLocal() as db:
        channel = await db.get(IntakeChannel, channel_id)
        template = await db.get(DocumentTemplate, channel.template_id) if channel else None
        if channel is None or template is None:
            raise IntakeRejected(404, "This address is not in use.")
        where = {"tenant_id": template.tenant_id, "template_id": template.id, "channel_id": channel.id}
        try:
            done = await accept(db, channel=channel, template=template, content=content, filename=filename, sender=sender, external_id=external_id,
                                metadata=metadata, actor_id=f"intake:{channel.id}", actor_type="system")
            await db.commit()
        except IntakeRejected as rejected:
            await db.rollback()
            await reject(**where, sender=sender, reason=rejected.reason, filename=filename, size=len(content), external_id=external_id)
            raise
    await finish(done, channel, sender)
    return done
