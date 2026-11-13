"""
Telling people about invoices, once the change has committed, in its own transaction (as alerts.py does), so a change
that was rolled back never sends anything: an invoice was issued (its PDF attached) or paid (the receipt attached). The
email goes to the organization's billing address; the people who manage its billing see it in the app too.
"""
import asyncio
import logging
from typing import List, Optional
from sqlalchemy import select
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.billing import BillingRefund, CreditNote, Invoice, PaymentSubmission
from app.models.identity import OrgMember, User
from app.models.tenant import Tenant
from app.services.billing import invoices, parties
from app.services.billing.invoices import lines_of
from app.services.billing.methods import METHODS
from app.services.billing.pdf import money
from app.services.notifications import Recipient, inapp, notify
from app.services.notifications.kinds import (
    CREDIT_NOTE_ISSUED, INVOICE_ISSUED, INVOICE_REMINDER, PAYMENT_RECEIVED, PAYMENT_REJECTED, PAYMENT_SUBMITTED, PLAN_LAPSED, STAFF_ATTENTION, Kind,
)
from app.services.storage_service import storage_service

logger = logging.getLogger("core.billing.notices")


def billing_url(slug: str, invoice_id: Optional[str] = None) -> str:
    """Where the organization's billing (or one invoice) opens in the main app."""
    base = f"{settings.PUBLIC_APP_URL.rstrip('/')}/{slug}/settings/billing"
    return f"{base}/invoices?invoice={invoice_id}" if invoice_id else base


async def _billers(db, tenant_id: str) -> List[User]:
    from app.services.billing.alerts import BILLERS
    return list((await db.execute(select(User).join(OrgMember, OrgMember.user_id == User.id).where(
        OrgMember.tenant_id == tenant_id, OrgMember.role.in_(BILLERS)))).scalars().all())


async def _send(kind: Kind, invoice_id: str, data_for, attach_key_of) -> None:
    try:
        async with AsyncSessionLocal() as db:
            invoice = await db.get(Invoice, invoice_id)
            tenant = await db.get(Tenant, invoice.tenant_id) if invoice else None
            if invoice is None or tenant is None:
                return
            data = {"organization": tenant.name, "number": invoice.number, "url": billing_url(tenant.slug, invoice.id), "invoice": invoice.id,
                    **await data_for(db, invoice)}
            key = attach_key_of(invoice)
            files = []
            if key:
                body = await asyncio.to_thread(storage_service.download_file, key)
                files = [(f"{'Receipt' if key.endswith('-receipt.pdf') else 'Invoice'} {invoice.number}.pdf", body, "application/pdf")]
            if (invoice.buyer or {}).get("email"):
                await notify(kind, Recipient(email=invoice.buyer["email"]), data, channels={"email"}, attachments=files)
            for person in await _billers(db, invoice.tenant_id):
                await notify(kind, Recipient(user_id=person.id, tenant_id=invoice.tenant_id), data, channels={"in_app"}, db=db)
            await db.commit()
            await inapp.push(db)
    except Exception:
        logger.exception("Telling %s about invoice %s failed", kind.key, invoice_id)


async def invoice_issued(invoice_id: str) -> None:
    """The invoice and how to pay it, with its PDF. Never raises."""
    async def data(db, invoice):
        items = ", ".join(f"{line.quantity} × {line.description}" if line.quantity > 1 else line.description for line in await lines_of(db, invoice.id))
        return {"total": money(invoice.total_cents, invoice.currency), "due_on": f"{invoice.due_at:%d %B %Y}", "items": items}
    await _send(INVOICE_ISSUED, invoice_id, data, lambda i: i.pdf_key)


async def invoice_paid(invoice_id: str, done: Optional[str]) -> None:
    """The invoice is paid and what was switched on, with the receipt. Never raises."""
    async def data(db, invoice):
        return {"amount": money(invoice.amount_paid_cents, invoice.currency), "done": done}
    await _send(PAYMENT_RECEIVED, invoice_id, data, lambda i: i.receipt_key)


def _reminder_words(offset: Optional[int], number: str, due_on: str) -> tuple:
    if offset is None:
        return f"Invoice {number} needs paying by hand", None
    if offset > 0:
        return f"Invoice {number} is due in {offset} day{'s' if offset != 1 else ''}", f"It's due by {due_on}."
    if offset == 0:
        return f"Invoice {number} is due today", f"It's due today, {due_on}."
    days = -offset
    return f"Invoice {number} is overdue", f"It was due {days} day{'s' if days != 1 else ''} ago, on {due_on}."


async def invoice_reminder(invoice_id: str, offset: Optional[int], note: Optional[str] = None) -> None:
    """Before or after the due date (offset: days before it; negative after), or when a kept card was declined. Never raises."""
    async def data(db, invoice):
        due_on = f"{invoice.due_at:%d %B %Y}"
        headline, when = _reminder_words(offset, invoice.number, due_on)
        lapses = ""
        if invoice.kind == "renewal" and offset is not None and offset <= 0:
            lapses = f" If it isn't paid within {(await parties.seller(db)).grace_days} days of the due date, the organization moves to the Free plan."
        body = " ".join(p for p in (note, when, "Pay by bank transfer and send the slip from the invoice page." + lapses) if p)
        return {"due": money(invoices.amount_due(invoice), invoice.currency), "due_on": due_on, "headline": headline, "body": body}
    await _send(INVOICE_REMINDER, invoice_id, data, lambda i: None)


async def plan_lapsed(invoice_id: str, plan: str) -> None:
    """A renewal went unpaid past the grace period and the organization moved to Free. Never raises."""
    async def data(db, invoice):
        return {"plan": plan}
    await _send(PLAN_LAPSED, invoice_id, data, lambda i: None)


async def credit_note_issued(note_id: str) -> None:
    """A credit note, with its PDF, to the organization's billing address. Never raises."""
    try:
        async with AsyncSessionLocal() as db:
            note = await db.get(CreditNote, note_id)
            invoice = await db.get(Invoice, note.invoice_id) if note else None
            tenant = await db.get(Tenant, note.tenant_id) if note else None
            if not (note and invoice and tenant and (invoice.buyer or {}).get("email")):
                return
            refunded = sum(r.amount_cents for r in (await db.execute(select(BillingRefund).where(BillingRefund.credit_note_id == note.id))).scalars())
            files = [(f"Credit note {note.number}.pdf", await asyncio.to_thread(storage_service.download_file, note.pdf_key), "application/pdf")] if note.pdf_key else []
            await notify(CREDIT_NOTE_ISSUED, Recipient(email=invoice.buyer["email"]), {
                "organization": tenant.name, "number": note.number, "invoice_number": invoice.number, "amount": money(note.amount_cents, note.currency),
                "reason": note.reason, "url": billing_url(tenant.slug, invoice.id), "refunded": money(refunded, note.currency) if refunded else None},
                attachments=files)
    except Exception:
        logger.exception("Telling about credit note %s failed", note_id)


async def staff_attention(tenant_id: Optional[str], title: str, body: str) -> None:
    """Tells Syruss's billing address about something only a person can sort out (a disputed card payment). Never raises."""
    try:
        async with AsyncSessionLocal() as db:
            seller = await parties.seller(db)
            tenant = await db.get(Tenant, tenant_id) if tenant_id else None
            if not seller.email:
                logger.warning("No billing address is set for Syruss; not sent: %s. %s", title, body)
                return
            base = settings.PUBLIC_ADMIN_URL.rstrip("/")
            await notify(STAFF_ATTENTION, Recipient(email=seller.email), {
                "title": title, "body": body, "organization": tenant.name if tenant else "Unknown",
                "url": f"{base}/billing/{tenant.id}" if tenant else f"{base}/billing"})
    except Exception:
        logger.exception("Telling Syruss's billing address failed: %s", title)


async def payment_submitted(submission_id: str) -> None:
    """Tells Syruss's billing address that a customer sent proof of a payment to check. Never raises."""
    try:
        async with AsyncSessionLocal() as db:
            s = await db.get(PaymentSubmission, submission_id)
            invoice = await db.get(Invoice, s.invoice_id) if s else None
            seller = await parties.seller(db)
            tenant = await db.get(Tenant, s.tenant_id) if s else None
            if not (s and invoice and tenant and seller.email):
                return
            await notify(PAYMENT_SUBMITTED, Recipient(email=seller.email), {
                "organization": tenant.name, "number": invoice.number, "amount": money(s.amount_cents, s.currency),
                "method": METHODS[s.method].label if s.method in METHODS else s.method,
                "url": f"{settings.PUBLIC_ADMIN_URL.rstrip('/')}/billing?tab=payments&submission={s.id}"})
    except Exception:
        logger.exception("Telling Syruss about payment %s failed", submission_id)


async def payment_rejected(submission_id: str) -> None:
    """Tells the organization its proof of payment wasn't accepted, and why. Never raises."""
    async def data(db, invoice):
        s = await db.get(PaymentSubmission, submission_id)
        return {"amount": money(s.amount_cents, s.currency), "reason": s.rejection_reason or "No reason was given."}
    async with AsyncSessionLocal() as db:
        s = await db.get(PaymentSubmission, submission_id)
    if s is not None:
        await _send(PAYMENT_REJECTED, s.invoice_id, data, lambda i: None)
