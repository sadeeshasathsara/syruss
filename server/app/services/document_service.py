import hashlib
import uuid
from typing import Any, Dict, Iterable, List, Optional
from fastapi import UploadFile, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.audit import AuditLog
from app.models.document import Document, DocumentStatus, DocumentRole
from app.services.intake_rules import IntakeRejected, SUPPORTED, clean_filename, inspect_image
from app.services.storage_service import storage_service

SUPPORTED_CONTENT_TYPES = set(SUPPORTED)


class DocumentService:
    @staticmethod
    async def store_document(
        db: AsyncSession,
        *,
        tenant_id: str,
        template_id: str,
        file: UploadFile,
        role: DocumentRole = DocumentRole.DOCUMENT,
        actor: str = "user",
        allowed_types: Optional[Iterable[str]] = None,
    ) -> Document:
        """A file from the web app: stored after the same checks every other way in goes through (caller commits)."""
        content = await file.read()
        try:
            return await DocumentService.store_bytes(
                db, tenant_id=tenant_id, template_id=template_id, content=content, filename=file.filename,
                role=role, actor=actor, allowed_types=allowed_types,
            )
        except IntakeRejected as rejected:
            raise HTTPException(status_code=rejected.status, detail=rejected.reason)

    @staticmethod
    async def store_bytes(
        db: AsyncSession,
        *,
        tenant_id: str,
        template_id: str,
        content: bytes,
        filename: Optional[str],
        role: DocumentRole = DocumentRole.DOCUMENT,
        actor: str = "user",
        allowed_types: Optional[Iterable[str]] = None,
        channel_id: Optional[str] = None,
        external_id: Optional[str] = None,
        source: Optional[Dict[str, Any]] = None,
        sender_metadata: Optional[Dict[str, Any]] = None,
        is_test: bool = False,
    ) -> Document:
        """
        Saves a file to object storage and records it in the workspace (caller commits). The file's type is read from its bytes, never
        from what the sender called it, and the storage key is built from the new id and the real type, never from the file name.
        Raises IntakeRejected with a sentence the sender can act on.
        """
        content_type, ext = inspect_image(content, allowed_types or SUPPORTED)
        doc_id = str(uuid.uuid4())
        key = f"tenants/{tenant_id}/templates/{template_id}/{role.value.lower()}s/{doc_id}.{ext}"
        storage_service.upload_file(object_name=key, data=content, content_type=content_type)
        name = clean_filename(filename) if filename else f"{doc_id}.{ext}"

        document = Document(
            id=doc_id,
            tenant_id=tenant_id,
            template_id=template_id,
            role=role,
            original_filename=name,
            content_type=content_type,
            file_size_bytes=len(content),
            file_hash=hashlib.sha256(content).hexdigest(),
            minio_object_key=key,
            status=DocumentStatus.PENDING,
            channel_id=channel_id,
            external_id=external_id,
            source=source,
            sender_metadata=sender_metadata,
            is_test=is_test,
        )
        db.add(document)
        db.add(AuditLog(
            tenant_id=tenant_id, template_id=template_id, event_type="DOCUMENT_UPLOADED",
            entity_type="Document", entity_id=doc_id, actor=actor,
            details={"filename": document.original_filename, "size": len(content), "role": role.value,
                     **({"channel_id": channel_id} if channel_id else {})},
        ))
        await db.flush()
        return document

    @staticmethod
    async def get_document(db: AsyncSession, tenant_id: str, template_id: str, document_id: str) -> Document:
        doc = await db.get(Document, document_id)
        if not doc or doc.tenant_id != tenant_id or doc.template_id != template_id:
            raise HTTPException(status_code=404, detail="Document not found.")
        return doc

    @staticmethod
    async def list_documents(
        db: AsyncSession, tenant_id: str, template_id: str, *, status: Optional[DocumentStatus] = None,
        role: DocumentRole = DocumentRole.DOCUMENT, limit: int = 50, offset: int = 0,
    ) -> List[Document]:
        stmt = select(Document).where(
            Document.tenant_id == tenant_id, Document.template_id == template_id, Document.role == role,
        )
        if status is not None:
            stmt = stmt.where(Document.status == status)
        stmt = stmt.order_by(Document.created_at.desc()).limit(limit).offset(offset)
        return list((await db.execute(stmt)).scalars().all())


document_service = DocumentService()
