"""
Every page the agent looks at (each document it views in a run), so reading pages can be capped:
records are already in the database, and looking at a page again costs a model call with an image.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class AgentPageView(Base):
    __tablename__ = "agent_page_views"
    __table_args__ = (
        Index("ix_agent_page_views_event", "event_id", "document_id"),
        Index("ix_agent_page_views_person_day", "template_id", "actor_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    template_id: Mapped[str] = mapped_column(String(64), ForeignKey("document_templates.id", ondelete="CASCADE"), nullable=False)
    event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    document_id: Mapped[str] = mapped_column(String(64), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    # Who the run works for: the person chatting, or None for the workspace's own work
    actor_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    # "chat" (answering a person) | "own" (the run's own document) | "past" (a document already recorded)
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
