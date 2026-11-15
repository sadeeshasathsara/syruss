"""
Support tickets: a customer's problem, the conversation about it with Syruss staff (in the app and by email), and the
staff's saved replies. services/support/* holds the rules; api/v1/support.py is the customer's side and
api/v1/sysadmin/support.py the staff's.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base

CATEGORIES = ("billing", "account", "technical", "other")
PRIORITIES = ("low", "normal", "high", "urgent")
# new: nobody on the staff has answered yet; open: being worked on; pending: waiting for the customer;
# solved: answered (a customer reply reopens it); closed: done for good
STATUSES = ("new", "open", "pending", "solved", "closed")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SupportTicket(Base):
    __tablename__ = "support_tickets"
    __table_args__ = (Index("ix_support_tickets_status", "status", "updated_at"), Index("ix_support_tickets_tenant", "tenant_id", "updated_at"))

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    number: Mapped[str] = mapped_column(String(16), unique=True, nullable=False)         # "T-1042"
    # The organization it is about (none for mail from an address Syruss doesn't know)
    tenant_id: Mapped[Optional[str]] = mapped_column(String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True)
    template_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    opened_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)        # the customer's user id, when known
    requester_email: Mapped[str] = mapped_column(String(255), nullable=False)
    requester_name: Mapped[str] = mapped_column(String(255), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[str] = mapped_column(String(16), default="other", nullable=False)
    priority: Mapped[str] = mapped_column(String(8), default="normal", nullable=False)
    status: Mapped[str] = mapped_column(String(12), default="new", nullable=False)
    channel: Mapped[str] = mapped_column(String(8), nullable=False)                     # agent | app | email
    unverified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)    # mail from an unknown address
    assignee_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    assignee_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    related: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)           # invoice_id, document_id, conversation_id
    first_response_due: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    resolve_due: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    first_responded_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    breached_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)   # when an SLA target was first missed
    solved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_customer_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_staff_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    merged_into: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)


class SupportMessage(Base):
    """One message on a ticket. Internal notes are for staff only; email ids thread the conversation by mail."""
    __tablename__ = "support_messages"
    __table_args__ = (Index("ix_support_messages_ticket", "ticket_id", "created_at"), Index("ix_support_messages_email", "email_message_id"))

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    ticket_id: Mapped[str] = mapped_column(String(64), ForeignKey("support_tickets.id", ondelete="CASCADE"), nullable=False)
    author_kind: Mapped[str] = mapped_column(String(10), nullable=False)               # customer | staff | agent | system
    author_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    author_name: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    internal: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    via: Mapped[str] = mapped_column(String(8), nullable=False)                         # app | email | agent
    email_message_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class SupportFile(Base):
    """A file sent with a message (checked like payment slips: PDF or a photo, 10 MB at most), kept in object storage."""
    __tablename__ = "support_files"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    ticket_id: Mapped[str] = mapped_column(String(64), ForeignKey("support_tickets.id", ondelete="CASCADE"), nullable=False, index=True)
    message_id: Mapped[str] = mapped_column(String(64), ForeignKey("support_messages.id", ondelete="CASCADE"), nullable=False, index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    object_key: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class SupportSavedReply(Base):
    """A reply staff reuse (a quick reply): inserted into the composer, with {{first_name}} and {{ticket}} filled in."""
    __tablename__ = "support_saved_replies"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    shortcut: Mapped[Optional[str]] = mapped_column(String(40), unique=True, nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_name: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)
