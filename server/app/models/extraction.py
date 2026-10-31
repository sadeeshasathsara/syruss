import uuid
from typing import Optional
from datetime import datetime, timezone
from sqlalchemy import String, Float, DateTime, Text, ForeignKey, Integer, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class ExtractionResult(Base):
    """The structured record the agent produced for one document."""
    __tablename__ = "extraction_results"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    template_id: Mapped[str] = mapped_column(String(64), ForeignKey("document_templates.id", ondelete="CASCADE"), nullable=False, index=True)
    template_version_id: Mapped[Optional[str]] = mapped_column(String(64), ForeignKey("template_versions.id", ondelete="SET NULL"), nullable=True)
    document_id: Mapped[str] = mapped_column(String(64), ForeignKey("documents.id", ondelete="CASCADE"), unique=True, nullable=False, index=True)

    # Records keyed by object type: {"Guard": [{...}, ...], "Roster": [{...}]}
    extracted_data: Mapped[dict] = mapped_column(JSON, nullable=False)
    overall_confidence: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    # Per-field evidence: [{field, value, raw_text, confidence, entity_id, note}]
    evidence_records: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    # Deterministic rule check results computed by the server at submit time
    validation: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    agent_notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # How many times the record has been approved: 0 while it is only a draft, 1 for the first approval, 2 once a correction is approved
    revision: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    document = relationship("Document", back_populates="extraction")
