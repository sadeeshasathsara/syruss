import uuid
import enum
from typing import Optional
from datetime import datetime, timezone
from sqlalchemy import String, Text, DateTime, ForeignKey, JSON, Enum
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class AgentQuestionStatus(str, enum.Enum):
    OPEN = "OPEN"
    ANSWERED = "ANSWERED"
    DISMISSED = "DISMISSED"


class AgentQuestion(Base):
    """
    A question the workspace agent asked a human. Answering it wakes the agent
    with a `human.answered` event so it can continue the work it parked.
    """
    __tablename__ = "agent_questions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    template_id: Mapped[str] = mapped_column(String(64), ForeignKey("document_templates.id", ondelete="CASCADE"), nullable=False, index=True)

    question: Mapped[str] = mapped_column(Text, nullable=False)
    # Why the agent is asking and what it currently believes
    context: Mapped[str] = mapped_column(Text, default="", nullable=False)
    # Suggested answers the human can pick from (free text is always allowed)
    options: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    # How it is answered: "text" | "single" (one of the options) | "multiple" (any of them).
    # Choices always come with "Other", where the person can write their own answer.
    answer_type: Mapped[str] = mapped_column(String(16), default="text", server_default="text", nullable=False)
    evidence_document_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    # What the agent read that the question is about, as a table: {"title", "columns": [...], "rows": [[...], ...]}
    data_table: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # Which role should answer: "admin" (business/template owner) or "reviewer"
    target_role: Mapped[str] = mapped_column(String(32), default="admin", nullable=False)
    # "low" | "normal" | "high" — high means work is blocked on the answer
    priority: Mapped[str] = mapped_column(String(16), default="normal", nullable=False)
    related_document_id: Mapped[Optional[str]] = mapped_column(String(64), ForeignKey("documents.id", ondelete="SET NULL"), nullable=True, index=True)
    # The flag's own chat thread: the answer goes there, and Syruss follows up there until it resolves it
    conversation_id: Mapped[Optional[str]] = mapped_column(String(64), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True)

    status: Mapped[AgentQuestionStatus] = mapped_column(
        Enum(AgentQuestionStatus, name="agent_question_status"),
        default=AgentQuestionStatus.OPEN, nullable=False, index=True
    )
    answer: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # The options picked, and what they wrote under "Other"
    answer_choices: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    answer_other: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    answered_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    answered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True)
