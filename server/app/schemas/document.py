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
    display_name: Optional[str] = None  # the identification key, when the template has one and the record has its values
    content_type: str
    file_size_bytes: int
    page_count: int = 1  # what reading it is charged: a PDF's or TIFF's pages, or the sheets one picture shows
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
