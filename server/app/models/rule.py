import uuid
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import DateTime, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class TemplateRule(Base):
    """
    A check that runs on every record after a document is read, written in the rule language (services/rule_lang.py)
    over the template's own objects and fields. People write them, or ask Syruss to; Syruss also suggests them when it
    notices a relation that holds. A suggestion does nothing until a person adds it.

    status: suggested | active | disabled | broken. A rule that no longer compiles after the template changed is broken
    (`broken_reason` says why) and is not run until someone fixes it. severity "error" stops a record from being committed
    (the document goes to review); "warning" is only reported.
    """
    __tablename__ = "template_rules"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    template_id: Mapped[str] = mapped_column(String(64), ForeignKey("document_templates.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    scope: Mapped[str] = mapped_column(String(100), default="document", nullable=False)  # "document" or an object type
    check: Mapped[str] = mapped_column(Text, nullable=False)
    when: Mapped[str] = mapped_column(Text, default="", nullable=False)
    severity: Mapped[str] = mapped_column(String(16), default="error", nullable=False)
    message: Mapped[str] = mapped_column(Text, default="", nullable=False)  # shown when the rule fails
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False, index=True)
    created_by: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    created_by_kind: Mapped[str] = mapped_column(String(16), default="person", nullable=False)  # person | agent
    rationale: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # why Syruss suggests it
    source_document_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    source_conversation_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    checked_against_version_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    broken_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_dry_run: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # how it did on saved records when last checked
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)
