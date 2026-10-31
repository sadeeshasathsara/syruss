import uuid
from typing import Optional
from datetime import datetime, timezone
from sqlalchemy import String, Float, DateTime, Text, ForeignKey, JSON, Integer, Index
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class AgentExecutionTrace(Base):
    """
    The run log: one agent run (one wake-up) as reported by the agent service, with where it came
    from (origin), who triggered it, what it did step by step, and what it cost.

    origin_kind:   "user" (a person or their API key asked for it) | "system" (the platform did)
    origin_source: "chat" | "dashboard" | "api" | "schedule" | "agent" | "system"
    """
    __tablename__ = "agent_execution_traces"
    __table_args__ = (Index("ix_agent_runs_workspace_started", "template_id", "started_at"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    template_id: Mapped[str] = mapped_column(String(64), ForeignKey("document_templates.id", ondelete="CASCADE"), nullable=False, index=True)
    document_id: Mapped[Optional[str]] = mapped_column(String(64), ForeignKey("documents.id", ondelete="SET NULL"), nullable=True, index=True)

    # The event that woke the agent, e.g. "document.submitted", "human.answered"
    profile: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="COMPLETED", nullable=False)  # COMPLETED | FAILED | BUDGET_EXCEEDED

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    total_duration_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # [{step_number, step_started_at, step_completed_at, duration_ms, thought,
    #   tool_calls: [{tool_name, arguments, observation, duration_ms, status}]}]
    steps: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    final_thought: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    usage: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Where the run came from
    run_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)  # matches live telemetry
    event_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    origin_kind: Mapped[str] = mapped_column(String(16), default="system", nullable=False, index=True)
    origin_source: Mapped[str] = mapped_column(String(16), default="system", nullable=False)
    actor_type: Mapped[str] = mapped_column(String(16), default="system", nullable=False)
    actor_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    actor_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    conversation_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)

    # What it cost
    model_calls: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cost_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # None when the model's price isn't configured
