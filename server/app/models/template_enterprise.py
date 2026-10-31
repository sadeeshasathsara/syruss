import uuid
import enum
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from sqlalchemy import String, DateTime, Text, ForeignKey, JSON, Enum, Integer, Float, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class TemplateLifecycleStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    STAGED = "STAGED"
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class DocumentTemplateVersion(Base):
    """
    Immutable version of a DocumentTemplate schema and rule definition.
    Enforces the Draft -> Staged -> Active lifecycle.
    """
    __tablename__ = "template_versions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    template_id: Mapped[str] = mapped_column(String(64), ForeignKey("document_templates.id", ondelete="CASCADE"), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(32), nullable=False)  # e.g., "1.0.0", "1.1.0"
    status: Mapped[TemplateLifecycleStatus] = mapped_column(
        Enum(TemplateLifecycleStatus), default=TemplateLifecycleStatus.DRAFT, nullable=False, index=True
    )
    
    # Complete strict JSONSchema (Draft-07 compliant)
    schema_definition: Mapped[dict] = mapped_column(JSON, nullable=False)
    
    # Declarative AST validation rules (e.g. arithmetic, regex, bounds checks)
    validation_rules: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    
    # Confidence routing policies per field (e.g., {"national_id": {"min_confidence": 0.95, "action": "ESCALATE"}})
    confidence_policy: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    
    # Pre-deployment benchmark score summary
    benchmark_score: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    
    change_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(100), default="system")
    approved_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    activated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    evaluation_runs: Mapped[List["TemplateEvaluationRun"]] = relationship("TemplateEvaluationRun", back_populates="version", cascade="all, delete-orphan")


class GoldenDatasetItem(Base):
    """
    Curated reference document and expected ground-truth extraction
    for automated EvalOps and regression testing.
    """
    __tablename__ = "golden_dataset_items"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    template_id: Mapped[str] = mapped_column(String(64), ForeignKey("document_templates.id", ondelete="CASCADE"), nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)
    ground_truth_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    complexity_tag: Mapped[str] = mapped_column(String(64), default="standard")  # standard, noisy, rotated, handwritten
    # The workspace document it was made from (its verified record is the ground truth); shadow
    # evaluations re-read this document.
    document_id: Mapped[Optional[str]] = mapped_column(String(64), ForeignKey("documents.id", ondelete="CASCADE"), nullable=True, index=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class TemplateEvaluationRun(Base):
    """
    Evaluation benchmark execution report for a template version against a Golden Dataset.
    """
    __tablename__ = "template_evaluation_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    version_id: Mapped[str] = mapped_column(String(64), ForeignKey("template_versions.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), default="COMPLETED")  # RUNNING, COMPLETED, FAILED
    
    # Metrics object: {"precision": 0.98, "recall": 0.95, "f1_score": 0.965, "exact_match_rate": 0.92, "rule_pass_rate": 1.0}
    metrics: Mapped[dict] = mapped_column(JSON, nullable=False)
    failed_items_count: Mapped[int] = mapped_column(Integer, default=0)
    execution_duration_ms: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    version: Mapped["DocumentTemplateVersion"] = relationship("DocumentTemplateVersion", back_populates="evaluation_runs")
