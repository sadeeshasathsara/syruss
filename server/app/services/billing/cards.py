"""
Paying invoices by card, through Stripe (methods/card.py makes the calls):

    checkout(invoice)        a Checkout page for what is due, reused while it is open
    handle(event)            what Stripe tells us, once per event: a payment that went through is recorded, and a card
                             the customer chose to keep is remembered for renewals
    charge_saved_card(...)   a renewal paid with the kept card, without the customer present
    portal_url(...)          Stripe's page where the customer changes or removes the kept card

A payment is recorded once whichever way Stripe reports it (its charge is the payment's external reference).
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.models.billing import BillingPayment, CardCheckout, Invoice, StripeEvent
from app.services.billing import invoices, parties
from app.services.billing.methods import card as card_api
from app.services.billing.methods import method as payment_method

logger = logging.getLogger("core.billing.cards")
Paid = Optional[Tuple[Invoice, Optional[str]]]  # an invoice the event paid, and what was switched on


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _invoice_url(slug: str, invoice_id: str, paid: bool) -> str:
    return f"{settings.PUBLIC_APP_URL.rstrip('/')}/{slug}/settings/billing/invoices?invoice={invoice_id}" + ("&paid=card" if paid else "")


async def checkout(db: AsyncSession, invoice: Invoice, *, slug: str, save_card: bool = False) -> str:
    """The address of a Stripe Checkout page for what is still due on the invoice."""
    payment_method("card")
    invoice = await invoices.locked(db, invoice.id)
    if invoice.status not in invoices.PAYABLE:
        raise ValueError("This invoice isn't waiting for payment.")
    due = invoices.amount_due(invoice)
    reusable = (await db.execute(select(CardCheckout).where(
        CardCheckout.invoice_id == invoice.id, CardCheckout.status == "open", CardCheckout.amount_cents == due,
        CardCheckout.save_card.is_(save_card), CardCheckout.expires_at > _now() + timedelta(minutes=10)))).scalars().first()
    if reusable:
        return reusable.url
    profile = await parties.profile(db, invoice.tenant_id)
    session = card_api.create_checkout(
        amount_cents=due, currency=invoice.currency, name=f"Invoice {invoice.number}", invoice_id=invoice.id, tenant_id=invoice.tenant_id,
        customer=profile.stripe_customer_id if profile else None, email=(invoice.buyer or {}).get("email"), save_card=save_card,
        success_url=_invoice_url(slug, invoice.id, True), cancel_url=_invoice_url(slug, invoice.id, False))
    db.add(CardCheckout(invoice_id=invoice.id, tenant_id=invoice.tenant_id, session_id=session["id"], url=session["url"], amount_cents=due,
                        save_card=save_card, expires_at=card_api.when(session["expires_at"])))
    await db.flush()
    return session["url"]


async def _record(db: AsyncSession, invoice_id: Optional[str], amount_cents: int, charge_ref: Optional[str]) -> Paid:
    if charge_ref and (await db.execute(select(BillingPayment.id).where(BillingPayment.external_ref == charge_ref))).first():
        return None  # already recorded, the other way Stripe reports it
    invoice = await db.get(Invoice, invoice_id) if invoice_id else None
    if invoice is None or not amount_cents:
        return None
    if invoice.status not in invoices.PAYABLE:
        # Paid by card after it was voided or paid another way: the money arrived, and staff refund it.
        logger.error("Card payment %s of %s for invoice %s, which is %s: refund it from Stripe", charge_ref, amount_cents, invoice.number, invoice.status)
        return None
    _, done = await invoices.record_payment(db, invoice, method="card", amount_cents=amount_cents, by_id="stripe", by_name="Card (Stripe)",
                                            reference=charge_ref, external_ref=charge_ref, received_on=_now().date())
    return (invoice, done) if invoice.status == "paid" else None


async def _remember_card(db: AsyncSession, tenant_id: str, payment_intent: str, customer: Optional[str]) -> None:
    profile = await parties.profile(db, tenant_id)
    if profile is None:
        return
    found = card_api.card_of(payment_intent)
    profile.stripe_customer_id = found["customer"] or customer or profile.stripe_customer_id
    if found["payment_method"]:
        profile.card_payment_method, profile.card_label = found["payment_method"], found["label"]
    await db.flush()


async def handle(db: AsyncSession, event: Dict[str, Any]) -> Paid:
    """Acts on one Stripe event, once (a repeat is ignored). Returns the invoice it paid, if it paid one."""
    fresh = (await db.execute(insert(StripeEvent).values(id=event["id"], type=event["type"], received_at=_now())
                              .on_conflict_do_nothing(index_elements=["id"]).returning(StripeEvent.id))).first()
    if fresh is None:
        return None
    kind, obj = event["type"], event["data"]["object"]
    meta = obj.get("metadata") or {}
    if kind in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        page = (await db.execute(select(CardCheckout).where(CardCheckout.session_id == obj["id"]))).scalar_one_or_none()
        if obj.get("payment_status") != "paid":
            return None  # a bank debit still on its way: async_payment_succeeded follows
        if page:
            page.status = "completed"
        paid = await _record(db, meta.get("invoice_id"), int(obj.get("amount_total") or 0), obj.get("payment_intent"))
        if page and page.save_card and obj.get("payment_intent"):
            await _remember_card(db, page.tenant_id, obj["payment_intent"], obj.get("customer"))
        return paid
    if kind == "payment_intent.succeeded" and meta.get("via") == "saved_card":
        return await _record(db, meta.get("invoice_id"), int(obj.get("amount_received") or 0), obj["id"])
    if kind == "checkout.session.expired":
        page = (await db.execute(select(CardCheckout).where(CardCheckout.session_id == obj["id"]))).scalar_one_or_none()
        if page and page.status == "open":
            page.status = "expired"
    if kind == "charge.refunded" and obj.get("payment_intent"):
        from app.services.billing.corrections import refunded_by_stripe  # (corrections uses this module's card calls)
        made = await refunded_by_stripe(db, obj["payment_intent"], ((obj.get("refunds") or {}).get("data")) or [])
        if made and not any(r.credit_note_id for r in made):
            logger.warning("A refund of %s was made in Stripe without a credit note: issue one for the invoice", obj["payment_intent"])
    return None


async def charge_saved_card(db: AsyncSession, invoice: Invoice) -> Paid:
    """Pays what is due with the card the organization kept. ValueError when there is none or it was declined."""
    profile = await parties.profile(db, invoice.tenant_id)
    if not (profile and profile.card_payment_method and profile.stripe_customer_id):
        raise ValueError("No card is kept for renewals.")
    due = invoices.amount_due(invoice)
    try:
        result = card_api.charge(amount_cents=due, currency=invoice.currency, customer=profile.stripe_customer_id,
                                 payment_method=profile.card_payment_method, invoice_id=invoice.id, tenant_id=invoice.tenant_id)
    except Exception as exc:  # the card was declined, or needs the customer (3-D Secure)
        raise ValueError(f"The kept card couldn't be charged: {getattr(exc, 'user_message', None) or exc}") from exc
    if result["status"] != "succeeded":
        raise ValueError("The kept card needs the customer to confirm the payment.")
    return await _record(db, invoice.id, int(result["amount_received"]), result["id"])


async def portal_url(db: AsyncSession, tenant_id: str, slug: str) -> str:
    profile = await parties.profile(db, tenant_id)
    if not (profile and profile.stripe_customer_id):
        raise ValueError("The organization hasn't paid by card yet.")
    return card_api.portal(customer=profile.stripe_customer_id, return_url=f"{settings.PUBLIC_APP_URL.rstrip('/')}/{slug}/settings/billing")
