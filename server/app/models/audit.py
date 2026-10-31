import uuid
from typing import Optional
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    template_id: Mapped[Optional[str]] = mapped_column(String(64), ForeignKey("document_templates.id", ondelete="CASCADE"), nullable=True, index=True)

    event_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)  # e.g. "DOCUMENT_UPLOADED", "RECORD_COMMITTED"
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)             # e.g. "Document", "ReviewTask", "Entity"
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    actor: Mapped[str] = mapped_column(String(100), default="system", nullable=False)  # user id, "agent", "system"
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)
