"""
The workspace graph: which identified things appear together in committed records.

One row is one sighting: in this document, `subject` appeared with `context` (a guard on a
site's roster, a customer on an order from a branch). Each pair is stored in both directions,
so "who appears with S12" and "where does 75599 appear" are both one indexed lookup.
Counting sightings gives the graph's edges; where Apache AGE is installed they are mirrored
into a per-workspace graph (app.services.graph_service).
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class EntitySighting(Base):
    __tablename__ = "entity_sightings"
    __table_args__ = (
        Index("ix_entity_sightings_context", "template_id", "context_key"),
        Index("ix_entity_sightings_subject", "template_id", "subject_key"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    template_id: Mapped[str] = mapped_column(String(64), ForeignKey("document_templates.id", ondelete="CASCADE"), nullable=False)
    document_id: Mapped[str] = mapped_column(String(64), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)

    # "<ObjectType>:<normalized id>", with the type and the name as last written for display
    subject_key: Mapped[str] = mapped_column(String(400), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(100), nullable=False)
    subject_label: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    context_key: Mapped[str] = mapped_column(String(400), nullable=False)
    context_type: Mapped[str] = mapped_column(String(100), nullable=False)
    context_label: Mapped[str] = mapped_column(String(255), default="", nullable=False)

    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
