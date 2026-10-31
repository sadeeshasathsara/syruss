import uuid
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, Index, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class ExtractionLogEntry(Base):
    """
    One line of a workspace's extraction log: what happened to a document, in plain words. Written by the system as it
    happens, read by every member of the workspace, edited by nobody. `seq` only ever grows, so a reader pages back
    with "entries before this number" no matter how long the log gets.
    """
    __tablename__ = "extraction_log"
    __table_args__ = (Index("ix_extraction_log_workspace_seq", "template_id", "seq"),)

    seq: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    id: Mapped[str] = mapped_column(String(64), default=lambda: str(uuid.uuid4()), unique=True, nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    template_id: Mapped[str] = mapped_column(String(64), ForeignKey("document_templates.id", ondelete="CASCADE"), nullable=False)
    document_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    document_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)  # kept even if the document is deleted
    stage: Mapped[str] = mapped_column(String(24), nullable=False)   # received | queued | reading | waiting | saved | review | failed | decided | corrected
    level: Mapped[str] = mapped_column(String(10), default="info", nullable=False)  # info | success | warning | error
    message: Mapped[str] = mapped_column(Text, nullable=False)
    actor: Mapped[str] = mapped_column(String(100), default="system", nullable=False)  # a person's name, "Agent" or "System"
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)
