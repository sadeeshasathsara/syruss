import uuid
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class WorkspaceScreen(Base):
    """
    A custom screen: a saved table over the workspace's saved records (see services/screen_engine.py for the
    definition). People build them; the agent can too. One the agent thinks of on its own is "suggested" until a person
    accepts it, so nothing appears in the sidebar without somebody agreeing.
    """
    __tablename__ = "workspace_screens"
    __table_args__ = (UniqueConstraint("template_id", "slug", name="uq_screen_slug"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    template_id: Mapped[str] = mapped_column(String(64), ForeignKey("document_templates.id", ondelete="CASCADE"), nullable=False, index=True)
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    definition: Mapped[dict] = mapped_column(JSON, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)  # active | suggested
    created_by: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    created_by_kind: Mapped[str] = mapped_column(String(16), default="person", nullable=False)  # person | agent
    rationale: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # why the agent suggests it
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)
