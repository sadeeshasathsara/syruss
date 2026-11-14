"""
Payments customers report themselves. For a way of paying that needs proof (a bank transfer), the customer sends the
slip with the amount, the day and their bank's reference; staff check it against the account, then approve it (the
payment is recorded for what actually arrived, and the invoice settles) or reject it with a reason the customer sees.

    submit(...)     a customer sends proof for an invoice waiting for payment (one waiting to be checked at a time)
    withdraw(...)   the customer takes it back before it is checked
    approve(...)    staff: the money arrived
    reject(...)     staff: it didn't, or the slip doesn't show it; the invoice keeps waiting
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional, Tuple
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.billing import BillingPayment, Invoice, PaymentFile, PaymentSubmission
from app.services.billing import invoices, proofs
from app.services.billing.methods import method as payment_method
from app.services.billing.pdf import money


class Conflict(ValueError):
    """Not now: the invoice or the payment isn't in a state that allows it (as opposed to something wrong with what was sent)."""


class NeedsConfirmation(ValueError):
    """Allowed, but only when staff say they mean it (more than is due)."""


@dataclass
class Upload:
    filename: Optional[str]
    data: bytes


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def locked(db: AsyncSession, submission_id: str) -> Optional[PaymentSubmission]:
    return (await db.execute(select(PaymentSubmission).where(PaymentSubmission.id == submission_id).with_for_update()
                             .execution_options(populate_existing=True))).scalar_one_or_none()


async def submit(db: AsyncSession, invoice: Invoice, *, method: str, amount_cents: int, paid_on: date, reference: Optional[str],
                 note: Optional[str], files: List[Upload], by_id: str, by_name: str) -> PaymentSubmission:
    """A customer's proof of payment for an invoice. Every file is checked before any is kept."""
    way = payment_method(method)
    if not (way.customer_can_use and way.needs_proof):
        raise ValueError(f"Payments by {way.label.lower()} aren't reported this way.")
    invoice = await invoices.locked(db, invoice.id)
    if invoice.status not in invoices.PAYABLE:
        raise Conflict("This invoice isn't waiting for payment.")
    if amount_cents <= 0:
        raise ValueError("Say how much was paid.")
    if paid_on > (_now() + timedelta(days=1)).date():
        raise ValueError("The day it was paid can't be in the future.")
    if not files:
        raise ValueError("Add the bank slip or the transfer confirmation.")
    if len(files) > proofs.MAX_FILES:
        raise ValueError(f"Send up to {proofs.MAX_FILES} files.")
    for f in files:
        proofs.check(f.data)
    if (await db.execute(select(PaymentSubmission.id).where(PaymentSubmission.invoice_id == invoice.id,
                                                            PaymentSubmission.status == "submitted"))).first():
        raise Conflict("A payment sent for this invoice is still being checked. Withdraw it to send it again.")
    row = PaymentSubmission(invoice_id=invoice.id, tenant_id=invoice.tenant_id, method=method, amount_cents=amount_cents, currency=invoice.currency,
                            paid_on=paid_on, payer_reference=(reference or "").strip() or None, note=(note or "").strip() or None,
                            status="submitted", submitted_by=by_id, submitted_by_name=by_name)
    db.add(row)
    await db.flush()
    for f in files:
        await proofs.store(db, tenant_id=invoice.tenant_id, filename=f.filename, data=f.data, uploaded_by=by_id, submission_id=row.id)
    return row


async def withdraw(db: AsyncSession, submission: PaymentSubmission) -> PaymentSubmission:
    submission = await locked(db, submission.id)
    if submission.status != "submitted":
        raise Conflict("Only a payment still being checked can be withdrawn.")
    submission.status = "withdrawn"
    await db.flush()
    return submission


async def approve(db: AsyncSession, submission: PaymentSubmission, *, by_id: str, by_name: str, amount_cents: Optional[int] = None,
                  received_on: Optional[date] = None, overpaid_ok: bool = False) -> Tuple[BillingPayment, Optional[str], Invoice]:
    """The money arrived: records a payment for what arrived (it can be less than was said). Returns it, what was switched on, the invoice."""
    submission = await locked(db, submission.id)
    if submission is None or submission.status != "submitted":
        raise Conflict("This payment was already checked." if submission else "No such payment.")
    invoice = await db.get(Invoice, submission.invoice_id)
    amount = amount_cents or submission.amount_cents
    due = invoices.amount_due(invoice)
    if amount > due and not overpaid_ok:
        raise NeedsConfirmation(f"That's more than the {money(due, invoice.currency)} due. Confirm it to record it anyway.")
    payment, done = await invoices.record_payment(db, invoice, method=submission.method, amount_cents=amount, by_id=by_id, by_name=by_name,
                                                  received_on=received_on or submission.paid_on, reference=submission.payer_reference,
                                                  note=submission.note)
    submission.status, submission.reviewed_by, submission.reviewed_by_name, submission.reviewed_at, submission.payment_id = (
        "approved", by_id, by_name, _now(), payment.id)
    await db.flush()
    return payment, done, invoice


async def reject(db: AsyncSession, submission: PaymentSubmission, *, by_id: str, by_name: str, reason: str) -> PaymentSubmission:
    """It didn't arrive, or the slip doesn't show it. The reason is shown to the customer; the invoice keeps waiting."""
    submission = await locked(db, submission.id)
    if submission is None or submission.status != "submitted":
        raise Conflict("This payment was already checked." if submission else "No such payment.")
    submission.status, submission.reviewed_by, submission.reviewed_by_name, submission.reviewed_at, submission.rejection_reason = (
        "rejected", by_id, by_name, _now(), reason.strip())
    await db.flush()
    return submission


def file_dict(f: PaymentFile) -> dict:
    return {"id": f.id, "filename": f.filename, "content_type": f.content_type, "size": f.size, "created_at": f.created_at}


def describe(s: PaymentSubmission, files: List[PaymentFile]) -> dict:
    return {"id": s.id, "invoice_id": s.invoice_id, "tenant_id": s.tenant_id, "method": s.method, "amount_cents": s.amount_cents,
            "currency": s.currency, "paid_on": s.paid_on, "reference": s.payer_reference, "note": s.note, "status": s.status,
            "submitted_by_name": s.submitted_by_name, "reviewed_by_name": s.reviewed_by_name, "reviewed_at": s.reviewed_at,
            "rejection_reason": s.rejection_reason, "payment_id": s.payment_id, "created_at": s.created_at, "files": [file_dict(f) for f in files]}
