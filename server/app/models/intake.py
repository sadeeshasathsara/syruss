import uuid
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _id() -> str:
    return str(uuid.uuid4())


class IntakeChannel(Base):
    """
    A way documents come into one workspace: a named channel with its own rules and its own keys, so every document knows where it came
    from. `kind` is api (REST and presigned upload), email (a built-in receiver) or sftp (a built-in server). Who sent a file is never
    taken from the file: it comes from the credential or address that reached this channel.
    """
    __tablename__ = "intake_channels"
    __table_args__ = (UniqueConstraint("template_id", "slug", name="uq_intake_channel_slug"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    template_id: Mapped[str] = mapped_column(String(64), ForeignKey("document_templates.id", ondelete="CASCADE"), nullable=False, index=True)
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), default="api", nullable=False)          # api | email | sftp
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)      # active | paused
    # allowed_types, max_mb, process (null = the workspace's setting), sender_allowlist, email_token, sftp_username, sftp_secret_hash, sftp_public_key
    config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_by: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)


class IntakeEvent(Base):
    """Every attempt to send a document in, accepted or not, with the reason: what a person looks at when a sender says it did not arrive."""
    __tablename__ = "intake_events"
    __table_args__ = (Index("ix_intake_events_workspace_time", "template_id", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    template_id: Mapped[str] = mapped_column(String(64), ForeignKey("document_templates.id", ondelete="CASCADE"), nullable=False)
    channel_id: Mapped[Optional[str]] = mapped_column(String(64), ForeignKey("intake_channels.id", ondelete="SET NULL"), nullable=True, index=True)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)                       # accepted | rejected | duplicate
    reason: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    filename: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    external_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    document_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    sender: Mapped[str] = mapped_column(String(255), default="", nullable=False)           # key name, e-mail address or SFTP user
    ip: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    key_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    via: Mapped[str] = mapped_column(String(16), default="api", nullable=False)            # api | upload | email | sftp
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False, index=True)


class IdempotencyRecord(Base):
    """What a sender's Idempotency-Key produced, so sending the same request again returns the same answer instead of a second document."""
    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("channel_id", "key", name="uq_idempotency_key"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    channel_id: Mapped[str] = mapped_column(String(64), ForeignKey("intake_channels.id", ondelete="CASCADE"), nullable=False)
    key: Mapped[str] = mapped_column(String(120), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    document_id: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class IntakeUpload(Base):
    """A large file on its way in: the sender asked for an upload link, sends the file straight to storage, then says it is done."""
    __tablename__ = "intake_uploads"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    template_id: Mapped[str] = mapped_column(String(64), ForeignKey("document_templates.id", ondelete="CASCADE"), nullable=False, index=True)
    channel_id: Mapped[str] = mapped_column(String(64), ForeignKey("intake_channels.id", ondelete="CASCADE"), nullable=False)
    key_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    object_key: Mapped[str] = mapped_column(String(512), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    declared_type: Mapped[str] = mapped_column(String(64), nullable=False)
    max_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    external_id: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    sender_metadata: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    process: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    state: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)       # pending | completed | rejected | expired
    document_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
