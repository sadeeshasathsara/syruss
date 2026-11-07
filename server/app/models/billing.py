"""
Plans and billing: what Syruss sells (the catalog), what each organization has (its subscription, add-ons and overrides)
and what it used (usage events). What an organization may do is worked out from these by
app.services.billing.entitlements, never stored.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base

CATALOG_KINDS = ("plan", "addon")
CATALOG_STATUSES = ("draft", "published", "archived")
SUBSCRIPTION_STATUSES = ("trialing", "active", "canceled")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _id() -> str:
    return str(uuid.uuid4())


class CatalogItem(Base):
    """
    One version of something sold: a plan or an add-on. A published version never changes; changing it means a new
    version with the same key, and organizations stay on theirs until they are moved. Nothing is ever deleted, only
    archived.

    `limits` names limits by their keys (app.services.billing.limits). For a plan it is the value of each one (null:
    unlimited; a limit it doesn't name is unlimited too); for an add-on, what one unit adds.
    """
    __tablename__ = "billing_catalog"
    __table_args__ = (UniqueConstraint("kind", "key", "version", name="uq_billing_catalog_version"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_id)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False)
    price_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    interval: Mapped[str] = mapped_column(String(16), default="month", nullable=False)
    public: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)  # offered to customers, not only given by staff
    sort: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    limits: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    provider_ref: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)  # its product and price at the payment provider
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class OrgSubscription(Base):
    """An organization's plan (one version of it) and its current monthly period. Every organization has one."""
    __tablename__ = "org_subscriptions"

    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True)
    plan_id: Mapped[str] = mapped_column(String(64), ForeignKey("billing_catalog.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    anchor_day: Mapped[int] = mapped_column(Integer, default=1, nullable=False)  # periods start on this day of the month (or the month's last)
    trial_ends_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    pending_plan_id: Mapped[Optional[str]] = mapped_column(String(64), ForeignKey("billing_catalog.id"), nullable=True)  # from the next period
    paid_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    provider_ref: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)


class OrgSubscriptionAddon(Base):
    """An add-on on an organization's subscription; they stack by quantity."""
    __tablename__ = "org_subscription_addons"
    __table_args__ = (UniqueConstraint("tenant_id", "addon_id", name="uq_org_subscription_addon"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True)
    addon_id: Mapped[str] = mapped_column(String(64), ForeignKey("billing_catalog.id"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    added_by: Mapped[str] = mapped_column(String(100), nullable=False)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class OrgLimitOverride(Base):
    """
    A limit set by hand for one organization, or for one of its workspaces (a workspace limit only): a custom deal, or
    capacity carried over. It replaces what the plan and add-ons give, until it expires.
    """
    __tablename__ = "org_limit_overrides"
    __table_args__ = (Index("ix_org_limit_overrides_tenant_key", "tenant_id", "key"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False)
    template_id: Mapped[Optional[str]] = mapped_column(String(64), ForeignKey("document_templates.id", ondelete="CASCADE"), nullable=True)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # null: unlimited
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    set_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class UsageEvent(Base):
    """
    Something an organization used and is charged for: today, the pages of a document accepted for reading. One per
    thing (`idempotency_key`), so it is never counted twice. It only grows: the database refuses to change or remove one,
    and it has no foreign keys, so billing history outlives what it was about.
    """
    __tablename__ = "usage_events"
    __table_args__ = (Index("ix_usage_events_tenant_period", "tenant_id", "metric", "period_start"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    template_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    metric: Mapped[str] = mapped_column(String(32), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    from_credits: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # the part paid with prepaid credits, not the allowance
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
