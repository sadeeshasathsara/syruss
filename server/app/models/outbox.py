import uuid
from typing import Optional
from datetime import datetime, timezone
from sqlalchemy import String, Integer, Text, DateTime, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class AgentEventOutbox(Base):
    """
    Transactional outbox for agent events. Business changes and the event that
    wakes the agent are committed together; a relay publishes pending rows to Kafka.
    No event is lost if Kafka is down, and nothing is published for a rolled-back change.
    """
    __tablename__ = "agent_event_outbox"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    template_id: Mapped[str] = mapped_column(String(64), ForeignKey("document_templates.id", ondelete="CASCADE"), nullable=False, index=True)

    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    actor: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # The conversation the event belongs to (a chat message, or work started from a chat)
    conversation_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    # A person stopped this run: the agent checks this between steps and while the model thinks
    cancelled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
