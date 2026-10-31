from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict
from app.models.document import DocumentStatus, DocumentRole


class DocumentResponse(BaseModel):
    id: str
    tenant_id: str
    template_id: str
    template_version_id: Optional[str] = None
    role: DocumentRole
    original_filename: str
    content_type: str
    file_size_bytes: int
    file_hash: str
    status: DocumentStatus
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DocumentStatusResponse(BaseModel):
    id: str
    status: DocumentStatus
    error_message: Optional[str] = None
    has_extraction: bool
    needs_human_review: bool
    updated_at: datetime
