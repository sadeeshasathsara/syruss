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
from typing import Any, Dict, Optional
from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
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


class RecordObject(Base):
    """
    What one committed record contains, object by object: the index behind object and thing profiles.

    One row per (document, object type, thing). A thing is an identified instance of an object with an identity
    (`thing_key` "<ObjectType>:<normalized id>"), whether the record has a row of it or only refers to it; an object
    without identity has one row per document with `thing_key` null. Written when a record is committed, removed when
    it isn't any more, and rebuilt for a whole workspace when its template changes (app.services.object_index).
    """
    __tablename__ = "record_objects"
    __table_args__ = (
        UniqueConstraint("document_id", "object_type", "thing_key", name="uq_record_objects_document_object_thing"),
        Index("ix_record_objects_object", "template_id", "object_type", "seen_at"),
        Index("ix_record_objects_thing", "template_id", "thing_key"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    template_id: Mapped[str] = mapped_column(String(64), ForeignKey("document_templates.id", ondelete="CASCADE"), nullable=False)
    document_id: Mapped[str] = mapped_column(String(64), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    object_type: Mapped[str] = mapped_column(String(100), nullable=False)
    thing_key: Mapped[Optional[str]] = mapped_column(String(400), nullable=True)
    label: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    # How many rows of this object (of this thing) the record has; 0 when it only refers to the thing
    rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # The thing's own row as this record read it (its attributes), when the record has one
    values: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    # When the document arrived: profiles count and order by it
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
