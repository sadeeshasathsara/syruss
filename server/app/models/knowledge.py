import uuid
import enum
from typing import Optional, List
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, Float, ForeignKey, JSON, Enum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base


class Entity(Base):
    """
    A known business object inside one template workspace
    (e.g. a Guard, a Site, a Vehicle). The agent resolves what it reads on a page to these.
    """
    __tablename__ = "knowledge_entities"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    template_id: Mapped[str] = mapped_column(String(64), ForeignKey("document_templates.id", ondelete="CASCADE"), nullable=False, index=True)

    entity_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    # Primary identifier (e.g. "employee_id" -> "75599")
    identifier_key: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    identifier_value: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)

    attributes: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)

    # "human" | "import" | "agent"
    created_by: Mapped[str] = mapped_column(String(100), default="human", nullable=False)
    source_document_id: Mapped[Optional[str]] = mapped_column(String(64), ForeignKey("documents.id", ondelete="SET NULL"), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    aliases: Mapped[List["EntityAlias"]] = relationship(
        "EntityAlias", back_populates="entity", cascade="all, delete-orphan", lazy="selectin"
    )


class EntityAlias(Base):
    """
    Another way an entity appears on documents: a nickname, abbreviation,
    or a recurring handwriting misreading confirmed by a human.
    """
    __tablename__ = "knowledge_entity_aliases"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    entity_id: Mapped[str] = mapped_column(String(64), ForeignKey("knowledge_entities.id", ondelete="CASCADE"), nullable=False, index=True)
    template_id: Mapped[str] = mapped_column(String(64), ForeignKey("document_templates.id", ondelete="CASCADE"), nullable=False, index=True)
    alias: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(255), default="agent", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    entity: Mapped["Entity"] = relationship("Entity", back_populates="aliases")


class EntityRelationship(Base):
    """Relationship between entities in a template workspace (e.g. Guard -> assigned_to -> Site)."""
    __tablename__ = "knowledge_relationships"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    template_id: Mapped[str] = mapped_column(String(64), ForeignKey("document_templates.id", ondelete="CASCADE"), nullable=False, index=True)

    source_entity_id: Mapped[str] = mapped_column(String(64), ForeignKey("knowledge_entities.id", ondelete="CASCADE"), nullable=False, index=True)
    target_entity_id: Mapped[str] = mapped_column(String(64), ForeignKey("knowledge_entities.id", ondelete="CASCADE"), nullable=False, index=True)

    relation_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    properties: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class KnowledgeProposalStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class KnowledgeProposalType(str, enum.Enum):
    TEMPLATE_CHANGE = "TEMPLATE_CHANGE"   # proposed_data: {schema_definition, validation_rules, change_summary}
    ENTITY_CREATE = "ENTITY_CREATE"       # proposed_data: {entity_type, name, identifier_key, identifier_value, attributes}
    ENTITY_MERGE = "ENTITY_MERGE"         # proposed_data: {keep_entity_id, merge_entity_ids}
    SOURCE_MAPPING = "SOURCE_MAPPING"     # proposed_data: {source_id, source_name, mapping, preview}


class KnowledgeProposal(Base):
    """
    A change the agent wants to make that needs human approval.
    Approving it applies the change (see knowledge_service.review_proposal).
    """
    __tablename__ = "knowledge_proposals"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    template_id: Mapped[str] = mapped_column(String(64), ForeignKey("document_templates.id", ondelete="CASCADE"), nullable=False, index=True)

    proposal_type: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    observation: Mapped[str] = mapped_column(String(2000), nullable=False)
    proposed_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    evidence_document_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    # What the agent checked before proposing (e.g. shadow test results on past documents)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    status: Mapped[KnowledgeProposalStatus] = mapped_column(
        Enum(KnowledgeProposalStatus, name="knowledge_proposal_status"),
        default=KnowledgeProposalStatus.PENDING,
        nullable=False,
        index=True
    )
    created_by: Mapped[str] = mapped_column(String(100), default="agent", nullable=False)
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    reviewer_notes: Mapped[Optional[str]] = mapped_column(String(2000), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
