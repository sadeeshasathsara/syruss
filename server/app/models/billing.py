"""
Plans and billing: what Syruss sells (the catalog), what each organization has (its subscription, add-ons and overrides)
and what it used (usage events). What an organization may do is worked out from these by
app.services.billing.entitlements, never stored.

Paying: every purchase is an invoice, made out from Syruss (billing settings) to the organization (its billing
profile). An invoice is numbered and fixed when it is issued; payments, however they were made, are recorded against
it, and what it sells is switched on once it is paid.
"""
import uuid
from datetime import date, datetime, timezone
from typing import Optional
from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base

CATALOG_KINDS = ("plan", "addon", "credits")
CATALOG_STATUSES = ("draft", "published", "archived")
SUBSCRIPTION_STATUSES = ("trialing", "active", "canceled")
CREDIT_KINDS = ("purchase", "grant", "usage", "refund", "adjustment")
INVOICE_KINDS = ("purchase", "renewal", "upgrade", "manual")
INVOICE_STATUSES = ("draft", "open", "paid", "void", "uncollectible")
LINE_KINDS = ("plan", "addon", "credits", "proration", "custom")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _id() -> str:
    return str(uuid.uuid4())


class CatalogItem(Base):
    """
    One version of something sold: a plan, an add-on, or a pack of prepaid credits. A published version never changes;
    changing it means a new version with the same key, and organizations stay on theirs until they are moved. Nothing is
    ever deleted, only archived.

    `limits` names limits by their keys (app.services.billing.limits). For a plan it is the value of each one (null:
    unlimited; a limit it doesn't name is unlimited too); for an add-on, what one unit adds. A credit pack has `pages`.
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
    pages: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # a credit pack: the pages it adds
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
    alerts: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)  # which usage alerts this period already sent
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


class CreditEntry(Base):
    """
    One change to an organization's prepaid credits, in pages: bought or given (+), used by a document (-), refunded or
    corrected. The balance is the sum. It only grows, like usage events: a correction is a new entry.
    """
    __tablename__ = "credit_ledger"
    __table_args__ = (Index("ix_credit_ledger_tenant_created", "tenant_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    delta: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ref: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)  # the pack, payment, request or document it came from
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class BillingPayment(Base):
    """
    Money received for an invoice, however it was paid (`method`: a bank transfer whose slip staff checked, cash or a
    cheque staff recorded, a card). It only grows: a refund is recorded on its own, never by changing the payment.
    Payments from before invoices have no invoice, and say what they paid for in item_id.
    """
    __tablename__ = "billing_payments"
    __table_args__ = (Index("ix_billing_payments_tenant_created", "tenant_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    invoice_id: Mapped[Optional[str]] = mapped_column(String(64), ForeignKey("invoices.id"), nullable=True, index=True)
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    item_id: Mapped[Optional[str]] = mapped_column(String(64), ForeignKey("billing_catalog.id"), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    received_on: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    reference: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)  # the bank transfer, cheque or receipt number
    external_ref: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)  # the charge at a card processor
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    recorded_by: Mapped[str] = mapped_column(String(100), nullable=False)
    recorded_by_name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class BillingProfile(Base):
    """Who an organization's invoices are made out to, kept by its owners. An invoice keeps a copy as it was when issued."""
    __tablename__ = "billing_profiles"

    tenant_id: Mapped[str] = mapped_column(String(64), ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True)
    legal_name: Mapped[str] = mapped_column(String(200), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)  # where invoices and receipts are sent
    address_line1: Mapped[str] = mapped_column(String(200), nullable=False)
    address_line2: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    city: Mapped[str] = mapped_column(String(120), nullable=False)
    region: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    postal_code: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    country: Mapped[str] = mapped_column(String(2), nullable=False)  # ISO 3166-1 alpha-2: decides the tax
    tax_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    po_number: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)  # printed on every invoice when set
    updated_by: Mapped[str] = mapped_column(String(100), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)


class BillingSettings(Base):
    """Syruss as the seller, one row: who invoices are from, how they are numbered and paid, and the renewal timetable."""
    __tablename__ = "billing_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # always 1
    legal_name: Mapped[str] = mapped_column(String(200), default="", nullable=False)
    address: Mapped[str] = mapped_column(Text, default="", nullable=False)  # as printed, one line per line
    country: Mapped[str] = mapped_column(String(2), default="", nullable=False)
    tax_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    email: Mapped[str] = mapped_column(String(255), default="", nullable=False)  # billing questions, and where payment slips are announced
    invoice_prefix: Mapped[str] = mapped_column(String(16), default="SYR", nullable=False)
    payment_terms_days: Mapped[int] = mapped_column(Integer, default=14, nullable=False)
    renewal_lead_days: Mapped[int] = mapped_column(Integer, default=7, nullable=False)
    grace_days: Mapped[int] = mapped_column(Integer, default=7, nullable=False)
    reminder_days: Mapped[list] = mapped_column(JSON, default=lambda: [3, 0, -3], nullable=False)  # days before the due date (negative: after)
    footer: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    updated_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)


class BankAccount(Base):
    """An account customers pay into by bank transfer; an invoice shows the active ones in its currency."""
    __tablename__ = "billing_bank_accounts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_id)
    bank: Mapped[str] = mapped_column(String(120), nullable=False)
    account_name: Mapped[str] = mapped_column(String(200), nullable=False)
    account_number: Mapped[str] = mapped_column(String(64), nullable=False)
    branch: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    swift: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    instructions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class TaxRate(Base):
    """
    The tax on an invoice to a buyer in `country` ("*": any country without its own). With reverse_charge_with_tax_id,
    a buyer who gives a tax ID accounts for the tax themselves and the invoice says so instead of charging it.
    """
    __tablename__ = "tax_rates"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_id)
    country: Mapped[str] = mapped_column(String(2), nullable=False)
    name: Mapped[str] = mapped_column(String(60), nullable=False)  # as printed: "VAT", "GST"
    rate_bp: Mapped[int] = mapped_column(Integer, nullable=False)  # basis points: 1800 is 18%
    reverse_charge_with_tax_id: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class BillingSequence(Base):
    """The last number given in a series ("invoice:2026"). Taken under a row lock as an invoice is issued, so numbers have no gaps."""
    __tablename__ = "billing_sequences"

    series: Mapped[str] = mapped_column(String(40), primary_key=True)
    last: Mapped[int] = mapped_column(Integer, nullable=False)


class Invoice(Base):
    """
    What an organization is asked to pay. A draft can change; issuing it gives it its number and copies in the seller,
    the buyer, the bank accounts and the tax as they are then, and from then on the database refuses to change any of
    that (a mistake is voided, or corrected with a credit note). It has no foreign key to the organization: invoices are
    kept for as long as the law asks, whatever happens to the organization.
    """
    __tablename__ = "invoices"
    __table_args__ = (
        Index("ix_invoices_tenant_created", "tenant_id", "created_at"),
        Index("ix_invoices_status_due", "status", "due_at"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_id)
    number: Mapped[Optional[str]] = mapped_column(String(40), nullable=True, unique=True)  # set when issued
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="draft", nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    subtotal_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tax_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    amount_paid_cents: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    issued_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    due_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    voided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    void_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    period_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)  # a renewal: the period it pays for
    period_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    seller: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    buyer: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    bank: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    tax_lines: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # printed on the invoice (a purchase order, what the customer asked)
    pdf_key: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    receipt_key: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    fulfilled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)  # what it sells was switched on
    switched_on_early_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    switched_on_early_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)


class InvoiceLine(Base):
    """One line of an invoice: a catalog item (a plan for a period, an add-on, a credit pack) or a line staff wrote."""
    __tablename__ = "invoice_lines"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_id)
    invoice_id: Mapped[str] = mapped_column(String(64), ForeignKey("invoices.id", ondelete="CASCADE"), nullable=False, index=True)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    item_id: Mapped[Optional[str]] = mapped_column(String(64), ForeignKey("billing_catalog.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    description: Mapped[str] = mapped_column(String(300), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    unit_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    tax_rate_bp: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    period_start: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    period_end: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class PaymentSubmission(Base):
    """
    A payment a customer reports for an invoice, with proof (the bank slip): counted only once staff approve it, for
    what actually arrived. Rejected ones keep the reason the customer was given; one waiting to be checked can be withdrawn.
    """
    __tablename__ = "payment_submissions"
    __table_args__ = (Index("ix_payment_submissions_status_created", "status", "created_at"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_id)
    invoice_id: Mapped[str] = mapped_column(String(64), ForeignKey("invoices.id"), nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    paid_on: Mapped[date] = mapped_column(Date, nullable=False)
    payer_reference: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)  # the reference on the customer's bank transfer
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="submitted", nullable=False)
    submitted_by: Mapped[str] = mapped_column(String(100), nullable=False)
    submitted_by_name: Mapped[str] = mapped_column(String(255), nullable=False)
    reviewed_by: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    reviewed_by_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # shown to the customer
    payment_id: Mapped[Optional[str]] = mapped_column(String(64), ForeignKey("billing_payments.id"), nullable=True)  # once approved
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class PaymentFile(Base):
    """A file of proof: a customer's bank slip, or a receipt staff attached to a payment they recorded. Kept, never changed."""
    __tablename__ = "payment_files"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_id)
    tenant_id: Mapped[str] = mapped_column(String(64), nullable=False)
    submission_id: Mapped[Optional[str]] = mapped_column(String(64), ForeignKey("payment_submissions.id"), nullable=True, index=True)
    payment_id: Mapped[Optional[str]] = mapped_column(String(64), ForeignKey("billing_payments.id"), nullable=True, index=True)
    object_key: Mapped[str] = mapped_column(String(300), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)  # the same slip sent twice shows up
    uploaded_by: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
