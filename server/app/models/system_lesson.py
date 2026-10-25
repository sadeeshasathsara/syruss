"""
Syruss's own memory: how to work in this platform, whatever the workspace or organization.

Not business knowledge (that is in `memories`, per workspace) but lessons about the system itself:
how to use its tools, what a refusal means, when to commit and when to ask. Seeded lessons are
active from the start; lessons the agent proposes apply everywhere only after a platform admin
approves them, so nothing from one workspace reaches the others unchecked.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SystemLesson(Base):
    __tablename__ = "system_lessons"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    content: Mapped[str] = mapped_column(Text, nullable=False)
    why: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="proposed", nullable=False, index=True)  # active | proposed | retired
    source: Mapped[str] = mapped_column(String(16), default="agent", nullable=False)                # seed | agent | admin
    importance: Mapped[float] = mapped_column(Float, default=0.7, nullable=False)
    # Where a proposal came from (for the reviewer; never shown to other workspaces)
    template_id: Mapped[Optional[str]] = mapped_column(String(64), ForeignKey("document_templates.id", ondelete="SET NULL"), nullable=True)
    event_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    times_proposed: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_by: Mapped[str] = mapped_column(String(100), default="agent", nullable=False)
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
