import enum
import uuid
from typing import Optional
from datetime import datetime, timezone
from sqlalchemy import Boolean, String, Integer, DateTime, JSON, Text, ForeignKey, Index, Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import expression, text
from app.core.database import Base


class DocumentStatus(str, enum.Enum):
    PENDING = "PENDING"
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    AWAITING_INPUT = "AWAITING_INPUT"   # the agent parked it until a human answers its question
    COMPLETED = "COMPLETED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    FAILED = "FAILED"


class DocumentRole(str, enum.Enum):
    DOCUMENT = "DOCUMENT"   # a real document to process
    SAMPLE = "SAMPLE"       # a sample used to define or refine the template


class Document(Base):
    __tablename__ = "documents"
    # One sender reference, one document, per channel: sending it again is recognised, not stored twice.
    __table_args__ = (Index("uq_document_external_id", "channel_id", "external_id", unique=True, postgresql_where=text("external_id IS NOT NULL")),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    template_id: Mapped[str] = mapped_column(String(64), ForeignKey("document_templates.id", ondelete="CASCADE"), nullable=False, index=True)
    # Template version the current record was produced with
    template_version_id: Mapped[Optional[str]] = mapped_column(String(64), ForeignKey("template_versions.id", ondelete="SET NULL"), nullable=True)
    role: Mapped[DocumentRole] = mapped_column(SQLEnum(DocumentRole), default=DocumentRole.DOCUMENT, nullable=False, index=True)

    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)  # SHA-256
    minio_object_key: Mapped[str] = mapped_column(String(512), nullable=False)

    status: Mapped[DocumentStatus] = mapped_column(SQLEnum(DocumentStatus), default=DocumentStatus.PENDING, nullable=False, index=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Where it came from, when it was sent in by another system (nothing here is read from the file itself)
    channel_id: Mapped[Optional[str]] = mapped_column(String(64), ForeignKey("intake_channels.id", ondelete="SET NULL"), nullable=True, index=True)
    external_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)   # the sender's own reference for it
    source: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)              # how it arrived: {"via", "sender", "ip", "channel"}
    sender_metadata: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)     # free-form labels the sender attached
    is_test: Mapped[bool] = mapped_column(Boolean, default=False, server_default=expression.false(), nullable=False)  # sent with a test key: never published

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    template = relationship("DocumentTemplate", lazy="selectin")
    extraction = relationship("ExtractionResult", back_populates="document", uselist=False, lazy="selectin", cascade="all, delete-orphan")
    review_task = relationship("ReviewTask", back_populates="document", uselist=False, lazy="selectin", cascade="all, delete-orphan")
