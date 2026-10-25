import uuid
from typing import Optional, List
from datetime import datetime, timezone
from sqlalchemy import String, Text, DateTime, Float, ForeignKey, Index, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class Conversation(Base):
    """
    A thread with the workspace agent, scoped to one template workspace.

    kind "chat"   a person's private chat with Syruss (user_id is theirs).
    kind "flag"   the thread of a quick decision Syruss flagged (agent_questions.conversation_id): the
                  answer is posted there and Syruss follows up there until it resolves it. Otherwise
                  like "agent", but listed in Review rather than the agent inbox.
    kind "agent"  a conversation Syruss opened with the people who can settle an issue (`audience`:
                  "admin" or "reviewer"). It stays open until resolved; meanwhile `agent_state`
                  holds Syruss's working state (its message history, a checkpoint) so it picks up
                  exactly where it left off whenever someone replies.
    """
    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_listing", "template_id", "kind", "updated_at"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    template_id: Mapped[str] = mapped_column(String(64), ForeignKey("document_templates.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    
    title: Mapped[str] = mapped_column(String(255), default="New Conversation", nullable=False)
    context_metadata: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    kind: Mapped[str] = mapped_column(String(16), default="chat", server_default="chat", nullable=False)
    status: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)         # agent: "open" | "resolved"
    audience: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)       # agent: "admin" | "reviewer"
    agent_state: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)         # agent: checkpoint until resolved
    resolution: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)

    messages: Mapped[List["ChatMessage"]] = relationship(
        "ChatMessage",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="ChatMessage.created_at",
        lazy="selectin"
    )

    @property
    def message_count(self) -> int:
        return len(self.messages) if self.messages is not None else 0



class ChatMessage(Base):
    """
    One message in a conversation. Assistant messages are written by the agent through the Agent Tool API.
    """
    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id: Mapped[str] = mapped_column(String(64), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)

    role: Mapped[str] = mapped_column(String(20), nullable=False) # "user" | "assistant" | "system"
    content: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_citations: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    agent_trace: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    conversation: Mapped["Conversation"] = relationship("Conversation", back_populates="messages")
