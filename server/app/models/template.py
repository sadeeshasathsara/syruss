import uuid
from typing import Optional
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, Text, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class DocumentTemplate(Base):
    """
    A template is also the workspace: documents, knowledge, reviews, questions
    and the agent itself are all scoped to one template.

    `schema_definition` always mirrors the ACTIVE version (see template_service.activate_version).
    Format:
      {
        "document_type": "...", "description": "...",
        "objects": [
          {"type": "Customer", "description": "...", "multiple": false,
           "identity": {"id_field": "account_no", "label_field": "name"},
           "fields": [{"name": "account_no", "type": "string", "required": true},
                      {"name": "name", "type": "string", "required": true}]},
          {"type": "Order", "multiple": false,
           "fields": [{"name": "customer", "type": "string", "resolves_to": "Customer"}, ...]}
        ],
    Objects with an identity are real, recurring things (the entities the agent recognizes);
    objects without one are the facts a document records, pointing at things via resolves_to.
        "validation_rules": [ ...see app/services/rule_engine.py... ]
      }
    """
    __tablename__ = "document_templates"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    document_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    schema_definition: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    active_version_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
