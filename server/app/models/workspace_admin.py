"""
Workspace administration: settings, members and invitations (RBAC), API keys and webhooks.
Each lives in its own table, keyed by the workspace (= template) id.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _id() -> str:
    return str(uuid.uuid4())


class WorkspaceSettings(Base):
    """Validated by app.services.settings_service.WorkspaceSettingsData; missing keys take defaults."""
    __tablename__ = "workspace_settings"

    template_id: Mapped[str] = mapped_column(String(64), ForeignKey("document_templates.id", ondelete="CASCADE"), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    updated_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class WorkspaceMember(Base):
    """A person's role (a workspace_roles id) in one workspace. `notification_prefs` are theirs for this workspace."""
    __tablename__ = "workspace_members"
    __table_args__ = (UniqueConstraint("template_id", "user_id", name="uq_workspace_member"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    template_id: Mapped[str] = mapped_column(String(64), ForeignKey("document_templates.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    role: Mapped[str] = mapped_column(String(64), ForeignKey("workspace_roles.id"), nullable=False, index=True)
    notification_prefs: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    invited_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class WorkspaceInvitation(Base):
    """An invite link for one email address and role. Only a hash of the link's token is stored."""
    __tablename__ = "workspace_invitations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    template_id: Mapped[str] = mapped_column(String(64), ForeignKey("document_templates.id", ondelete="CASCADE"), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(64), ForeignKey("workspace_roles.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)  # PENDING | ACCEPTED | REVOKED
    invited_by: Mapped[str] = mapped_column(String(100), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    # How the invitation email went: NOT_SENT (copied link only) | SENDING | SENT | FAILED
    email_status: Mapped[str] = mapped_column(String(16), default="NOT_SENT", nullable=False)
    email_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    email_error: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    note: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)  # a personal line from the inviter
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ApiKey(Base):
    """
    Programmatic access to one workspace. The key is shown once; only its SHA-256 is kept.
    scope "read" acts as a viewer, "write" as an editor.
    """
    __tablename__ = "workspace_api_keys"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    template_id: Mapped[str] = mapped_column(String(64), ForeignKey("document_templates.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    scope: Mapped[str] = mapped_column(String(16), default="read", nullable=False)    # read | write | intake
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # Intake keys: bound to one channel, which fixes where everything sent with it lands
    channel_id: Mapped[Optional[str]] = mapped_column(String(64), ForeignKey("intake_channels.id", ondelete="CASCADE"), nullable=True, index=True)
    mode: Mapped[str] = mapped_column(String(8), default="live", server_default="live", nullable=False)   # live | test (test documents are never published)
    ip_allowlist: Mapped[list] = mapped_column(JSON, default=list, nullable=False)                        # empty: any address
    rate_limit_per_minute: Mapped[int] = mapped_column(Integer, default=60, server_default="60", nullable=False)
    daily_quota: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    replaced_by: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)  # set when the key was rotated: it works until it expires


class Webhook(Base):
    """Workspace events POSTed to a URL, signed with the webhook's secret (stored encrypted)."""
    __tablename__ = "workspace_webhooks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    template_id: Mapped[str] = mapped_column(String(64), ForeignKey("document_templates.id", ondelete="CASCADE"), nullable=False, index=True)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    events: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    secret: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class WebhookDelivery(Base):
    __tablename__ = "workspace_webhook_deliveries"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_id)
    webhook_id: Mapped[str] = mapped_column(String(64), ForeignKey("workspace_webhooks.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    status_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
