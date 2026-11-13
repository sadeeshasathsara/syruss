"""
Invoices: what an organization is asked to pay, from Syruss to the organization.

    purchase(...)          a customer buys a plan, an add-on or credits: a draft, issued at once
    draft(...)             an invoice staff build (a custom deal), or one a renewal makes
    issue(invoice)         numbers it, copies in seller, buyer, bank accounts and tax, prints the PDF; fixed from then on
    record_payment(...)    money received for it, by any way of paying; settle() follows
    settle(invoice)        once payments cover it: paid, what it sells switched on (once), receipt printed
    switch_on_early(...)   staff switch it on before it is paid, for an organization they trust
    void(invoice)          an issued invoice nobody paid, cancelled (its number stays used)

Nothing is switched on until the invoice is paid, unless staff say otherwise. Every step is in the caller's
transaction; the caller commits, then tells people (notices.py) once it has.
"""
import asyncio
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional, Tuple
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.billing import BillingPayment, CatalogItem, Invoice, InvoiceLine, OrgSubscriptionAddon
from app.services.billing import parties, pdf, service as billing
from app.services.billing.methods import method as payment_method
from app.services.billing.numbering import invoice_number, next_number
from app.services.billing.tax import tax_for
from app.services.storage_service import storage_service

PAYABLE = ("open", "uncollectible")  # an uncollectible invoice can still be paid late


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Line:
    """A line to put on a draft: a catalog item, or text and a price staff wrote."""
    description: str
    quantity: int
    unit_cents: int
    kind: str = "custom"
    item: Optional[CatalogItem] = None
    period_start: Optional[datetime] = None
    period_end: Optional[datetime] = None


def item_line(item: CatalogItem, quantity: int = 1) -> Line:
    if item.kind == "plan":
        return Line(f"{item.name} plan, one month", 1, item.price_cents, "plan", item)
    if item.kind == "addon":
        return Line(f"{item.name} (monthly add-on)", quantity, item.price_cents, "addon", item)
    return Line(f"{item.name}: {(item.pages or 0):,} prepaid pages", quantity, item.price_cents, "credits", item)


def prorated(item: CatalogItem, current: CatalogItem, sub, now: datetime) -> List[Line]:
    """
    Moving up from one paid plan to a dearer one, mid-period: the new plan for the rest of this period, less the
    unused rest of the current one when it was paid for. The next period renews at the new plan's full price.
    """
    whole = (sub.period_end - sub.period_start).total_seconds()
    share = max(0.0, (sub.period_end - now).total_seconds()) / whole if whole > 0 else 0.0
    lines = [Line(f"{item.name} plan for the rest of this period", 1, round(item.price_cents * share), "plan", item, now, sub.period_end)]
    if sub.paid_until and sub.paid_until >= sub.period_end:
        lines.append(Line(f"Unused time on the {current.name} plan", 1, -round(current.price_cents * share), "proration", current, now, sub.period_end))
    return lines


async def lines_of(db: AsyncSession, invoice_id: str) -> List[InvoiceLine]:
    return list((await db.execute(select(InvoiceLine).where(InvoiceLine.invoice_id == invoice_id).order_by(InvoiceLine.position))).scalars().all())


async def payments_of(db: AsyncSession, invoice_id: str) -> List[BillingPayment]:
    return list((await db.execute(select(BillingPayment).where(BillingPayment.invoice_id == invoice_id)
                                  .order_by(BillingPayment.created_at))).scalars().all())


async def draft(db: AsyncSession, tenant_id: str, lines: List[Line], *, kind: str, created_by: str, note: Optional[str] = None,
                period: Optional[Tuple[datetime, datetime]] = None) -> Invoice:
    """A draft invoice in the lines' currency (they must share one). Its tax is worked out when it is issued."""
    if not lines:
        raise ValueError("An invoice needs at least one line.")
    currencies = {line.item.currency for line in lines if line.item}
    if len(currencies) > 1:
        raise ValueError("Everything on one invoice must be in the same currency.")
    if any(line.quantity < 1 for line in lines):
        raise ValueError("Each line needs a quantity of at least 1.")
    invoice = Invoice(tenant_id=tenant_id, kind=kind, status="draft", currency=(currencies.pop() if currencies else "USD"),
                      note=(note or "").strip() or None, created_by=created_by, seller={}, buyer={}, bank=[], tax_lines=[],
                      period_start=period[0] if period else None, period_end=period[1] if period else None)
    db.add(invoice)
    await db.flush()
    for position, line in enumerate(lines):
        db.add(InvoiceLine(invoice_id=invoice.id, position=position, item_id=line.item.id if line.item else None, kind=line.kind,
                           description=line.description[:300], quantity=line.quantity, unit_cents=line.unit_cents,
                           amount_cents=line.unit_cents * line.quantity, period_start=line.period_start, period_end=line.period_end))
    invoice.subtotal_cents = invoice.total_cents = sum(line.unit_cents * line.quantity for line in lines)
    await db.flush()
    return invoice


async def _store(key: str, data: bytes) -> str:
    await asyncio.to_thread(storage_service.upload_file, key, data, "application/pdf")
    return key


async def issue(db: AsyncSession, invoice: Invoice, due_at: Optional[datetime] = None) -> Invoice:
    """
    Issues a draft: refuses (parties.NotReady) while either side's billing details are missing. It takes the next
    number of the year, copies in the seller, the buyer, the bank accounts in its currency and the tax, sets the due date
    (from the payment terms unless given) and stores its PDF. An invoice for nothing (all free) is paid as it is issued.
    """
    if invoice.status != "draft":
        raise ValueError("Only a draft can be issued.")
    await parties.ready(db, invoice.tenant_id)
    seller, buyer = await parties.seller(db), await parties.profile(db, invoice.tenant_id)
    lines = await lines_of(db, invoice.id)
    subtotal = sum(line.amount_cents for line in lines)
    tax = tax_for(subtotal, buyer.country, buyer.tax_id, await parties.rates(db))
    for line in lines:  # the lines are fixed with their invoice, so they are finished first
        line.tax_rate_bp = tax.rate_bp
    await db.flush()
    now = _now()
    invoice.number = invoice_number(seller.invoice_prefix, now.year, await next_number(db, f"invoice:{now.year}"))
    invoice.seller, invoice.buyer = parties.seller_snapshot(seller), parties.buyer_snapshot(buyer)
    invoice.bank = [parties.bank_dict(a) for a in await parties.bank_accounts(db, invoice.currency)]
    invoice.subtotal_cents, invoice.tax_cents, invoice.total_cents, invoice.tax_lines = subtotal, tax.total_cents, subtotal + tax.total_cents, tax.lines
    invoice.issued_at, invoice.due_at, invoice.status = now, due_at or now + timedelta(days=seller.payment_terms_days), "open"
    await db.flush()
    invoice.pdf_key = await _store(f"billing/{invoice.tenant_id}/invoices/{invoice.id}.pdf", pdf.render(invoice, lines))
    await db.flush()
    if invoice.total_cents <= 0:
        await settle(db, invoice, by="system")
    return invoice


async def locked(db: AsyncSession, invoice_id: str) -> Optional[Invoice]:
    """The invoice, locked until the transaction ends: payments and staff actions on it go one at a time."""
    return (await db.execute(select(Invoice).where(Invoice.id == invoice_id).with_for_update().execution_options(populate_existing=True))).scalar_one_or_none()


async def record_payment(db: AsyncSession, invoice: Invoice, *, method: str, amount_cents: int, by_id: str, by_name: str,
                         received_on: Optional[date] = None, reference: Optional[str] = None, note: Optional[str] = None,
                         external_ref: Optional[str] = None) -> Tuple[BillingPayment, Optional[str]]:
    """Money received for an invoice. Returns the payment and, when it settled the invoice, what was switched on."""
    payment_method(method)
    invoice = await locked(db, invoice.id)
    if invoice.status not in PAYABLE:
        raise ValueError("This invoice isn't waiting for payment." if invoice.status != "paid" else "This invoice is already paid.")
    if amount_cents <= 0:
        raise ValueError("A payment is more than zero.")
    payment = BillingPayment(tenant_id=invoice.tenant_id, invoice_id=invoice.id, method=method, amount_cents=amount_cents, currency=invoice.currency,
                             received_on=received_on, reference=(reference or "").strip() or None, note=(note or "").strip() or None,
                             external_ref=external_ref, recorded_by=by_id, recorded_by_name=by_name)
    db.add(payment)
    await db.flush()
    return payment, await settle(db, invoice, by=by_id)


async def _renew(db: AsyncSession, invoice: Invoice, by: str) -> List[str]:
    """A renewal paid: the plan and add-ons it was for carry on (and come back, if it was paid after they lapsed)."""
    done = []
    sub = await billing.subscription(db, invoice.tenant_id)
    for line in await lines_of(db, invoice.id):
        item = await db.get(CatalogItem, line.item_id) if line.item_id else None
        if item is None:
            continue
        if line.kind == "plan" and sub.plan_id != item.id and sub.pending_plan_id != item.id:
            await billing.change_plan(db, invoice.tenant_id, item)
            done.append(f"put the organization back on the {item.name} plan")
        elif line.kind == "addon":
            have = (await db.execute(select(OrgSubscriptionAddon.quantity).where(
                OrgSubscriptionAddon.tenant_id == invoice.tenant_id, OrgSubscriptionAddon.addon_id == item.id))).scalar() or 0
            if have < line.quantity:
                await billing.set_addon(db, invoice.tenant_id, item, line.quantity, by)
                done.append(f"put back {line.quantity} × {item.name}")
    return done


async def fulfil(db: AsyncSession, invoice: Invoice, by: str) -> str:
    """Switches on what the invoice sells, once. Returns what was done."""
    if invoice.fulfilled_at:
        return ""
    if invoice.kind == "renewal":
        done = await _renew(db, invoice, by)
    else:
        done = []
        for line in await lines_of(db, invoice.id):
            item = await db.get(CatalogItem, line.item_id) if line.item_id else None
            if item is not None and line.kind in ("plan", "addon", "credits"):
                done.append(await billing.apply_item(db, invoice.tenant_id, item, line.quantity, by, ref=f"invoice:{invoice.id}",
                                                     reason=f"{item.name} ({invoice.number})"))
    invoice.fulfilled_at = _now()
    await db.flush()
    return "; ".join(done)


async def settle(db: AsyncSession, invoice: Invoice, by: str) -> Optional[str]:
    """
    Brings what has been paid up to date. Once payments (less refunds) and credit notes cover the total: paid, what it
    sells is switched on (unless it already was), a plan counts as paid (a renewal: for its period), and the receipt is
    printed. Returns what was switched on.
    """
    paid = int((await db.execute(select(func.coalesce(func.sum(BillingPayment.amount_cents), 0))
                                 .where(BillingPayment.invoice_id == invoice.id))).scalar() or 0)
    invoice.amount_paid_cents = paid
    covered = paid - invoice.amount_refunded_cents + invoice.amount_credited_cents
    if invoice.status not in PAYABLE or covered < invoice.total_cents:
        await db.flush()
        return None
    invoice.status, invoice.paid_at = "paid", _now()
    done = await fulfil(db, invoice, by)
    lines = await lines_of(db, invoice.id)
    if invoice.kind == "renewal":
        sub = await billing.subscription(db, invoice.tenant_id)
        sub.paid_until = max(filter(None, (sub.paid_until, invoice.period_end)))
        if sub.status == "past_due":
            sub.status = "active"
    elif any(line.kind == "plan" for line in lines):
        await billing.paid_through_period(db, invoice.tenant_id)
    invoice.receipt_key = await _store(f"billing/{invoice.tenant_id}/invoices/{invoice.id}-receipt.pdf",
                                       pdf.render(invoice, lines, await payments_of(db, invoice.id)))
    await db.flush()
    return done or None


async def switch_on_early(db: AsyncSession, invoice: Invoice, *, by_id: str, by_name: str, reason: str) -> str:
    """Staff switch on an unpaid invoice now; it stays open until it is paid."""
    invoice = await locked(db, invoice.id)
    if invoice.status != "open":
        raise ValueError("Only an issued invoice waiting for payment can be switched on early.")
    if invoice.fulfilled_at:
        raise ValueError("What this invoice sells is already switched on.")
    done = await fulfil(db, invoice, by_id)
    invoice.switched_on_early_by, invoice.switched_on_early_reason = by_name, reason
    await db.flush()
    return done


async def void(db: AsyncSession, invoice: Invoice, *, reason: str) -> Invoice:
    """Cancels an issued invoice nothing was paid on. Its number stays used, and it stays on record."""
    invoice = await locked(db, invoice.id)
    if invoice.status not in PAYABLE:
        raise ValueError("Only an issued invoice waiting for payment can be voided.")
    if invoice.amount_paid_cents:
        raise ValueError("Part of this invoice is paid: correct it with a credit note instead.")
    invoice.status, invoice.voided_at, invoice.void_reason = "void", _now(), reason
    await db.flush()
    return invoice


async def discard(db: AsyncSession, invoice: Invoice) -> None:
    """Throws a draft away (it never had a number)."""
    if invoice.status != "draft":
        raise ValueError("Only a draft can be thrown away; void an issued invoice.")
    await db.delete(invoice)
    await db.flush()


async def purchase(db: AsyncSession, tenant_id: str, item: CatalogItem, quantity: int, *, by: str, note: Optional[str] = None
                   ) -> Tuple[Optional[Invoice], str, bool]:
    """
    A customer buys something on offer: an invoice, issued at once (refused with parties.NotReady until both sides'
    billing details are in). The same thing already invoiced and unpaid gives that invoice again rather than a second.
    A free plan isn't invoiced: the organization moves to it when its period ends.
    Returns (invoice, what to say, whether the invoice is new).
    """
    if item.status != "published" or not item.public:
        raise ValueError("That isn't on offer.")
    lines, kind = [item_line(item, quantity)], "purchase"
    if item.kind == "plan":
        quantity = 1
        sub = await billing.subscription(db, tenant_id)
        current = await db.get(CatalogItem, sub.plan_id)
        if sub.plan_id == item.id or current.key == item.key:
            raise ValueError("That is already the organization's plan.")
        if item.price_cents < current.price_cents or not item.price_cents:
            await billing.change_plan(db, tenant_id, item, at_period_end=True)
            return None, f"The organization moves to the {item.name} plan on {sub.period_end:%d %B}, when this period ends.", False
        if current.price_cents and sub.status != "trialing":
            lines, kind = prorated(item, current, sub, _now()), "upgrade"
    waiting = (await db.execute(select(Invoice).join(InvoiceLine, InvoiceLine.invoice_id == Invoice.id).where(
        Invoice.tenant_id == tenant_id, Invoice.status == "open", Invoice.kind.in_(("purchase", "upgrade")), InvoiceLine.item_id == item.id,
        InvoiceLine.quantity == quantity))).scalars().first()
    if waiting:
        return waiting, f"Invoice {waiting.number} for this is already waiting for payment.", False
    await parties.ready(db, tenant_id)
    invoice = await issue(db, await draft(db, tenant_id, lines, kind=kind, created_by=by, note=note))
    return invoice, (f"Invoice {invoice.number} is ready. {item.name} is switched on as soon as it's paid."
                     if invoice.status == "open" else f"{item.name} is switched on."), True


def amount_due(invoice: Invoice) -> int:
    """What is still owed: the total, less payments (net of refunds) and credit notes. Nothing once void or paid."""
    if invoice.status not in PAYABLE:
        return 0
    return max(0, invoice.total_cents - (invoice.amount_paid_cents - invoice.amount_refunded_cents) - invoice.amount_credited_cents)


def describe(invoice: Invoice, lines: Optional[List[InvoiceLine]] = None, payments: Optional[List[BillingPayment]] = None) -> dict:
    """An invoice as the APIs show it (with its lines and payments when given)."""
    out = {
        "id": invoice.id, "number": invoice.number, "tenant_id": invoice.tenant_id, "kind": invoice.kind, "status": invoice.status,
        "currency": invoice.currency, "subtotal_cents": invoice.subtotal_cents, "tax_cents": invoice.tax_cents, "total_cents": invoice.total_cents,
        "amount_paid_cents": invoice.amount_paid_cents, "amount_credited_cents": invoice.amount_credited_cents,
        "amount_refunded_cents": invoice.amount_refunded_cents, "amount_due_cents": amount_due(invoice),
        "issued_at": invoice.issued_at, "due_at": invoice.due_at, "paid_at": invoice.paid_at, "voided_at": invoice.voided_at,
        "void_reason": invoice.void_reason, "period_start": invoice.period_start, "period_end": invoice.period_end,
        "overdue": invoice.status == "open" and invoice.due_at is not None and invoice.due_at < _now(),
        "seller": invoice.seller, "buyer": invoice.buyer, "bank": invoice.bank, "tax_lines": invoice.tax_lines, "note": invoice.note,
        "switched_on": invoice.fulfilled_at is not None, "switched_on_early_by": invoice.switched_on_early_by,
        "switched_on_early_reason": invoice.switched_on_early_reason, "has_pdf": bool(invoice.pdf_key), "has_receipt": bool(invoice.receipt_key),
        "created_at": invoice.created_at,
    }
    if lines is not None:
        out["lines"] = [{"id": line.id, "kind": line.kind, "item_id": line.item_id, "description": line.description, "quantity": line.quantity,
                         "unit_cents": line.unit_cents, "amount_cents": line.amount_cents, "tax_rate_bp": line.tax_rate_bp,
                         "period_start": line.period_start, "period_end": line.period_end} for line in lines]
    if payments is not None:
        out["payments"] = [{"id": p.id, "method": p.method, "amount_cents": p.amount_cents, "currency": p.currency, "received_on": p.received_on,
                            "reference": p.reference, "recorded_by_name": p.recorded_by_name, "created_at": p.created_at} for p in payments]
    return out


async def file_of(invoice: Invoice, which: str) -> Optional[bytes]:
    """The invoice's PDF ("pdf") or its receipt ("receipt"), when it has one."""
    key = invoice.pdf_key if which == "pdf" else invoice.receipt_key
    return await asyncio.to_thread(storage_service.download_file, key) if key else None
