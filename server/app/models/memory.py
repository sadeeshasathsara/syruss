import uuid
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Memory(Base):
    """
    What Syruss remembers, by scope:
      workspace     how this business works: lessons (procedures) and observations (what happened);
                    shared with everyone in the workspace
      personal      about one person, in this workspace only: preferences and facts they shared;
                    only ever used when talking with that person, and only they can see it
      conversation  the rolling summary of one chat's older messages

    kind: lesson | observation | preference | fact | summary

    Retrieval ranks by meaning (an `embedding` vector column, added by migration 0003 where the
    database has pgvector), then importance and recency; without pgvector it falls back to text
    search. The embedding is written by the agent service, which holds the model credentials.
    """
    __tablename__ = "memories"
    __table_args__ = (Index("ix_memories_lookup", "template_id", "scope", "user_id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    template_id: Mapped[str] = mapped_column(String(64), ForeignKey("document_templates.id", ondelete="CASCADE"), nullable=False, index=True)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    user_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    conversation_id: Mapped[Optional[str]] = mapped_column(String(64), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=True, index=True)

    content: Mapped[str] = mapped_column(Text, nullable=False)
    data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    importance: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Where it came from: {"type": "run" | "conversation" | "person" | "import", "event_id", "run_id", "document_ids"}
    source: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)  # "agent" or a user id
    # True when the text changed since its embedding was made (the agent re-embeds it).
    needs_embedding: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    use_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)
