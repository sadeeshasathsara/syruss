"""
Correcting an issued invoice without changing it: a credit note takes an amount off what is owed (numbered in its own
series, printed and sent like an invoice), and a refund gives money back from a payment, through the card processor
or by transfer or cash that staff record. Taking back what the invoice switched on (credits, a plan) is a separate,
deliberate step staff take with the usual changes.
"""
import asyncio
from datetime import datetime, timezone
from typing import List, Optional
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.billing import BillingPayment, BillingRefund, CreditNote, Invoice
from app.services.billing import invoices, parties, pdf
from app.services.billing.methods import card as card_api
from app.services.billing.numbering import next_number
from app.services.storage_service import storage_service


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def notes_of(db: AsyncSession, invoice_id: str) -> List[CreditNote]:
    return list((await db.execute(select(CreditNote).where(CreditNote.invoice_id == invoice_id).order_by(CreditNote.created_at))).scalars().all())


async def refunds_of(db: AsyncSession, *, payment_id: Optional[str] = None, credit_note_id: Optional[str] = None) -> List[BillingRefund]:
    stmt = select(BillingRefund).order_by(BillingRefund.created_at)
    stmt = stmt.where(BillingRefund.payment_id == payment_id) if payment_id else stmt.where(BillingRefund.credit_note_id == credit_note_id)
    return list((await db.execute(stmt)).scalars().all())


async def _totals(db: AsyncSession, invoice: Invoice) -> None:
    invoice.amount_credited_cents = int((await db.execute(select(func.coalesce(func.sum(CreditNote.amount_cents), 0))
                                                          .where(CreditNote.invoice_id == invoice.id))).scalar() or 0)
    invoice.amount_refunded_cents = int((await db.execute(select(func.coalesce(func.sum(BillingRefund.amount_cents), 0))
                                                          .join(BillingPayment, BillingPayment.id == BillingRefund.payment_id)
                                                          .where(BillingPayment.invoice_id == invoice.id))).scalar() or 0)
    await db.flush()


async def refund(db: AsyncSession, payment: BillingPayment, amount_cents: int, *, by_id: str, by_name: str, reference: Optional[str] = None,
                 credit_note: Optional[CreditNote] = None) -> BillingRefund:
    """Gives money back from a payment: a card payment through Stripe; any other way is recorded as done by staff."""
    given = int((await db.execute(select(func.coalesce(func.sum(BillingRefund.amount_cents), 0))
                                  .where(BillingRefund.payment_id == payment.id))).scalar() or 0)
    if amount_cents <= 0 or given + amount_cents > payment.amount_cents:
        raise ValueError(f"At most {pdf.money(payment.amount_cents - given, payment.currency)} of this payment can be refunded.")
    external = None
    if payment.method == "card":
        if not payment.external_ref:
            raise ValueError("This card payment can't be found at Stripe.")
        external = card_api.refund(payment_intent=payment.external_ref, amount_cents=amount_cents)
    row = BillingRefund(payment_id=payment.id, credit_note_id=credit_note.id if credit_note else None, tenant_id=payment.tenant_id,
                        amount_cents=amount_cents, currency=payment.currency, method=payment.method, reference=(reference or "").strip() or None,
                        external_ref=external, created_by=by_id, created_by_name=by_name)
    db.add(row)
    await db.flush()
    if payment.invoice_id:
        await _totals(db, await db.get(Invoice, payment.invoice_id))
    return row


async def credit_note(db: AsyncSession, invoice: Invoice, *, amount_cents: int, reason: str, by_id: str, by_name: str,
                      refund_from: Optional[BillingPayment] = None, refund_reference: Optional[str] = None) -> CreditNote:
    """
    Takes an amount off an issued invoice, with its own number and PDF. With refund_from, that much is also given back
    from the payment. On an invoice still waiting for payment, a credit note can settle it.
    """
    invoice = await invoices.locked(db, invoice.id)
    if invoice.status in ("draft", "void"):
        raise ValueError("Only an issued invoice can be credited.")
    left = invoice.total_cents - invoice.amount_credited_cents
    if amount_cents <= 0 or amount_cents > left:
        raise ValueError(f"At most {pdf.money(left, invoice.currency)} of this invoice is left to credit.")
    seller = await parties.seller(db)
    now = _now()
    n = await next_number(db, f"credit_note:{now.year}")
    note = CreditNote(number=f"{seller.invoice_prefix}-CN-{now.year}-{n:05d}", invoice_id=invoice.id, tenant_id=invoice.tenant_id,
                      currency=invoice.currency, amount_cents=amount_cents, reason=reason.strip(), created_by=by_id, created_by_name=by_name)
    db.add(note)
    await db.flush()
    if refund_from is not None:
        if refund_from.invoice_id != invoice.id:
            raise ValueError("That payment isn't for this invoice.")
        await refund(db, refund_from, amount_cents, by_id=by_id, by_name=by_name, reference=refund_reference, credit_note=note)
    await _totals(db, invoice)
    await invoices.settle(db, invoice, by=by_id)
    note.pdf_key = f"billing/{invoice.tenant_id}/credit-notes/{note.id}.pdf"
    data = pdf.render_credit_note(note, invoice, await refunds_of(db, credit_note_id=note.id))
    await asyncio.to_thread(storage_service.upload_file, note.pdf_key, data, "application/pdf")
    await db.flush()
    return note


async def refunded_by_stripe(db: AsyncSession, payment_intent: str, refunds: List[dict]) -> List[BillingRefund]:
    """Refunds Stripe reports (from a credit note here, or made in Stripe's dashboard): each recorded once."""
    payment = (await db.execute(select(BillingPayment).where(BillingPayment.external_ref == payment_intent))).scalar_one_or_none()
    if payment is None:
        return []
    made = []
    for r in refunds:
        if r.get("status") not in (None, "succeeded", "pending") or not r.get("id"):
            continue
        if (await db.execute(select(BillingRefund.id).where(BillingRefund.external_ref == r["id"]))).first():
            continue
        row = BillingRefund(payment_id=payment.id, tenant_id=payment.tenant_id, amount_cents=int(r["amount"]), currency=payment.currency,
                            method="card", reference="Refunded in Stripe", external_ref=r["id"], created_by="stripe", created_by_name="Stripe")
        db.add(row)
        made.append(row)
    await db.flush()
    if made and payment.invoice_id:
        await _totals(db, await db.get(Invoice, payment.invoice_id))
    return made


def note_dict(n: CreditNote, refunds: List[BillingRefund]) -> dict:
    return {"id": n.id, "number": n.number, "invoice_id": n.invoice_id, "amount_cents": n.amount_cents, "currency": n.currency,
            "reason": n.reason, "created_by_name": n.created_by_name, "created_at": n.created_at, "has_pdf": bool(n.pdf_key),
            "refunds": [{"amount_cents": r.amount_cents, "method": r.method, "reference": r.reference, "created_at": r.created_at} for r in refunds]}
