import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class KnowledgeSource(Base):
    """
    Reference data a workspace already has: a file (CSV, JSON) or an API the server calls.
    The agent decides how its records map onto the template's identified objects; once a
    person approves the mapping, the server imports the records as known entities.

    `config` holds what is safe to show (URL, method, query, auth type, user names);
    `secrets` holds the encrypted credentials and header values (see app/core/secrets.py).
    `data` is the latest snapshot: the parsed file, or the API's last response.
    `mapping`:
      {"records_path": "data.items",
       "objects": [{"entity_type": "Employee", "id_column": "EmpNo", "name_column": "Full name",
                    "attributes": {"rank": "Grade"}, "alias_columns": ["Known as"]}]}

    status: MAPPING (the agent is working it out) -> REVIEW (a mapping awaits approval)
            -> READY (imported); ERROR when the file or API could not be read.
    """
    __tablename__ = "knowledge_sources"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    template_id: Mapped[str] = mapped_column(String(64), ForeignKey("document_templates.id", ondelete="CASCADE"), nullable=False, index=True)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # "FILE" | "API"
    notes: Mapped[str] = mapped_column(Text, default="", nullable=False)
    config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    secrets: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    data: Mapped[Any] = mapped_column(JSON, nullable=True)
    mapping: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    status: Mapped[str] = mapped_column(String(16), default="MAPPING", nullable=False, index=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    imported_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
