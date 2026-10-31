"""
People and organizations. Keycloak proves who someone is; these tables hold what they may do:
their account, the organizations they belong to, and the roles workspaces define.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _id() -> str:
    return str(uuid.uuid4())


class User(Base):
    """A Syruss account. `id` is the Keycloak subject; email always comes from the verified token."""
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    phone: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    job_title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    avatar_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), default="UTC", nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)  # active | disabled
    onboarded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # The organization they were last in: where signing in opens.
    last_org_id: Mapped[Optional[str]] = mapped_column(String(64), ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    @property
    def display_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


class OrgMember(Base):
    """Membership of an organization (tenant) with an org role: owner, admin or member."""
    __tablename__ = "org_members"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", name="uq_org_member"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    invited_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class EmailVerification(Base):
    """
    A one-time code emailed to confirm an address before an account exists. One row per person and address; the code
    is kept only as a keyed hash, and cleared once used. `verified_at` is the proof the address was confirmed.
    """
    __tablename__ = "email_verifications"
    __table_args__ = (UniqueConstraint("user_id", "email", name="uq_email_verification"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_id)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)  # the Keycloak subject
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class TrustedDevice(Base):
    """
    A browser a person has signed in from. The browser keeps a long random token; only its hash is stored here. A browser
    is `pending` until the person types the code we emailed, then `trusted` until `trusted_until` (renewed while they use
    it); `revoked` ones must start over. The first browser an account ever uses is trusted without a code.
    """
    __tablename__ = "trusted_devices"
    __table_args__ = (UniqueConstraint("user_id", "token_hash", name="uq_trusted_device"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_id)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)  # the Keycloak subject
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(120), nullable=False)          # "Chrome on Windows"
    user_agent: Mapped[str] = mapped_column(String(300), default="", nullable=False)
    ip: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    status: Mapped[str] = mapped_column(String(12), default="pending", nullable=False)  # pending | trusted | revoked
    code_hash: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    code_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    code_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    remember: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)  # trusted for days, or just for this visit
    trusted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    trusted_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class SecurityEvent(Base):
    """What happened to a person's sign-in, in words they can read: new browsers, codes, revocations."""
    __tablename__ = "security_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_id)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    summary: Mapped[str] = mapped_column(String(300), nullable=False)
    device: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    ip: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)


class OrgInvitation(Base):
    """An invite link into an organization. Only a hash of the link's token is stored."""
    __tablename__ = "org_invitations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
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


class WorkspaceRole(Base):
    """
    What a member of a workspace may do. System roles (owner, admin, editor, reviewer, viewer) have
    no workspace, an id equal to their key, and cannot be edited. Custom roles belong to one workspace.
    `description` is the job title this role stands for.
    """
    __tablename__ = "workspace_roles"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_id)
    tenant_id: Mapped[Optional[str]] = mapped_column(String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True)
    template_id: Mapped[Optional[str]] = mapped_column(String(64), ForeignKey("document_templates.id", ondelete="CASCADE"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    permissions: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    created_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class WsTicket(Base):
    """A single-use, short-lived pass to open one WebSocket channel as one person."""
    __tablename__ = "ws_tickets"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(100), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    channel: Mapped[str] = mapped_column(String(255), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    used_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
