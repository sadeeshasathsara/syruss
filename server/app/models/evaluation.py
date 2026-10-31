"""
Shadow evaluations: the agent re-reads golden documents (records people verified) as if they
had just arrived. Its answers are scored against the verified records and nothing it does
changes the workspace (app.services.eval_service).
"""
import uuid
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _id() -> str:
    return str(uuid.uuid4())


class EvalRun(Base):
    __tablename__ = "eval_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    template_id: Mapped[str] = mapped_column(String(64), ForeignKey("document_templates.id", ondelete="CASCADE"), nullable=False, index=True)
    version_id: Mapped[Optional[str]] = mapped_column(String(64), ForeignKey("template_versions.id", ondelete="SET NULL"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="RUNNING", nullable=False)  # RUNNING | COMPLETED | CANCELLED
    item_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    requested_by: Mapped[str] = mapped_column(String(100), nullable=False)
    requested_by_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class EvalRunItem(Base):
    """One golden document in a run. `event_id` is the agent event that reads it."""
    __tablename__ = "eval_run_items"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_id)
    run_id: Mapped[str] = mapped_column(String(64), ForeignKey("eval_runs.id", ondelete="CASCADE"), nullable=False, index=True)
    golden_item_id: Mapped[Optional[str]] = mapped_column(String(64), ForeignKey("golden_dataset_items.id", ondelete="SET NULL"), nullable=True)
    document_id: Mapped[str] = mapped_column(String(64), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    event_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # QUEUED | ANSWERED (a record) | FAILED (marked unreadable) | NO_ANSWER (the run ended without one)
    status: Mapped[str] = mapped_column(String(16), default="QUEUED", nullable=False)
    expected: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    answer: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    decision: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)  # COMMIT | REVIEW, as the agent decided
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    scores: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
