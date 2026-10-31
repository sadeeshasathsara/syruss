import uuid
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _id() -> str:
    return str(uuid.uuid4())


class Destination(Base):
    """
    Somewhere approved data is sent: a webhook, a REST API, a storage bucket, a mailbox, or an accounting/ERP system. It is a
    connection (`config` and sealed `secrets`), a mapping (what the target receives) and a policy (when and how it is sent).
    """
    __tablename__ = "destinations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    template_id: Mapped[str] = mapped_column(String(64), ForeignKey("document_templates.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)               # webhook | rest | s3 | email | quickbooks | xero | netsuite | sap | dynamics | odoo
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)   # active | paused
    paused_reason: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    paused_by: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)         # user | system (the circuit breaker)
    config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)            # addresses, ids, options: nothing secret
    secrets: Mapped[Optional[str]] = mapped_column(Text, nullable=True)                 # sealed (core.secrets.seal): tokens, passwords, signing secrets
    mapping: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)           # {"mode": "record" | "mapped", "fields": [...], "lines": {...}}
    policy: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)            # send_updates, include_test, max_attempts
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_success_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_failure_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_by: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)


class Delivery(Base):
    """
    One approved record on its way to one destination. It is written in the same transaction that approves the record, so an approved
    record can never be lost on the way out. `held` rows wait until nothing is in doubt about the document.
    """
    __tablename__ = "deliveries"
    __table_args__ = (
        UniqueConstraint("destination_id", "document_id", "revision", name="uq_delivery_revision"),
        Index("ix_deliveries_due", "status", "next_attempt_at"),
        Index("ix_deliveries_workspace_time", "template_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    template_id: Mapped[str] = mapped_column(String(64), ForeignKey("document_templates.id", ondelete="CASCADE"), nullable=False)
    destination_id: Mapped[str] = mapped_column(String(64), ForeignKey("destinations.id", ondelete="CASCADE"), nullable=False, index=True)
    document_id: Mapped[str] = mapped_column(String(64), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    event: Mapped[str] = mapped_column(String(32), default="document.completed", nullable=False)   # document.completed | document.corrected
    # held | pending | sending | retrying | delivered | dead | cancelled
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    hold_reason: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    next_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    last_status_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    external_ref: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)     # the id the other system gave it (a bill number, an object key)
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)          # the document and the approved record at this revision
    payload_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)   # of the record's data: an unchanged re-approval is not sent again
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    locked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)


class DeliveryAttempt(Base):
    """One try at sending a delivery, with what was sent and what came back (shortened), for the person finding out why it failed."""
    __tablename__ = "delivery_attempts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    delivery_id: Mapped[str] = mapped_column(String(64), ForeignKey("deliveries.id", ondelete="CASCADE"), nullable=False, index=True)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    status_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    request: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    response: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
