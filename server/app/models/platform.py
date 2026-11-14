"""
The people who run the platform and what they did. Platform staff work across every organization from the system
admin app; their roles live here, not in Keycloak, and every action they take is written down for good.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import DateTime, ForeignKey, Index, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class PlatformStaff(Base):
    """
    Someone on the platform staff and their role (app.core.platform_permissions). The people named in
    PLATFORM_ADMIN_USER_IDS are owners without a row here, so the platform always has someone who can add staff.
    """
    __tablename__ = "platform_staff"

    user_id: Mapped[str] = mapped_column(String(100), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    added_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)


class PlatformAuditEntry(Base):
    """
    One action by platform staff. Rows are only ever added: the database refuses changes and deletions (migration
    0023), and the entry outlives the organization or person it was about, so ids are kept without foreign keys.
    """
    __tablename__ = "platform_audit_log"
    __table_args__ = (Index("ix_platform_audit_tenant_created", "tenant_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    actor_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    actor_name: Mapped[str] = mapped_column(String(255), nullable=False)
    # What was done, e.g. "staff.added", "tenant.suspended"
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[str] = mapped_column(String(100), nullable=False)
    # The organization the action touched, when it touched one
    tenant_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # Why, in the staff member's words (required for anything that reaches into a customer's data)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    ip: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False, index=True)


class PlatformAccessGrant(Base):
    """
    A staff member's access to one organization's content (documents, records, chats, what the agent thought and did),
    for a stated reason until a set time. Without one, staff see facts only: what ran, where, when, for how long, at
    what cost. Opening and ending one is in the platform audit log and in the organization's own activity.
    """
    __tablename__ = "platform_access_grants"
    __table_args__ = (Index("ix_platform_access_staff_tenant", "staff_id", "tenant_id", "expires_at"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    staff_id: Mapped[str] = mapped_column(String(100), nullable=False)
    staff_name: Mapped[str] = mapped_column(String(255), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
