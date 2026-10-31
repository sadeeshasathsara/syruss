"""
What every accounting or ERP destination does with a record: it becomes a vendor bill (a purchase invoice) with lines. Each system has
its own adapter for how that is asked for; this is the part they share: reading the mapped record into a bill, and checking it first, so a
bill that would not add up is stopped here with a sentence instead of arriving wrong.
"""
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple
from app.services.destinations import http
from app.services.destinations.base import Adapter, Field, SendContext, SendResult, Target

TARGETS = [
    Target("vendor_name", "Supplier name", required=True),
    Target("vendor_id", "Supplier number in their system (skips the lookup)"),
    Target("invoice_number", "Invoice number", required=True),
    Target("invoice_date", "Invoice date", required=True, kind="date"),
    Target("due_date", "Due date", kind="date"),
    Target("currency", "Currency (such as EUR)"),
    Target("total", "Invoice total", kind="number"),
    Target("tax_total", "Tax", kind="number"),
    Target("memo", "Note"),
    Target("description", "Line: description", line=True, required=True),
    Target("quantity", "Line: quantity", line=True, kind="number"),
    Target("unit_price", "Line: unit price", line=True, kind="number"),
    Target("amount", "Line: amount", line=True, kind="number"),
]

CENT = Decimal("0.01")


@dataclass
class Line:
    description: str
    quantity: Decimal
    unit_price: Decimal
    amount: Decimal


@dataclass
class Bill:
    vendor: str
    vendor_id: Optional[str]
    number: str
    date: str
    due: Optional[str]
    currency: Optional[str]
    total: Optional[Decimal]
    memo: str
    lines: List[Line] = field(default_factory=list)


def money(value: Any) -> Optional[Decimal]:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float, Decimal)):
        number = Decimal(str(value))
    else:
        cleaned = re.sub(r"[^\d.,\-]", "", str(value))
        if cleaned.count(",") and cleaned.count("."):
            cleaned = cleaned.replace(",", "")
        elif cleaned.count(",") == 1 and len(cleaned.split(",")[1]) <= 2:
            cleaned = cleaned.replace(",", ".")
        else:
            cleaned = cleaned.replace(",", "")
        try:
            number = Decimal(cleaned)
        except InvalidOperation:
            raise ValueError(f"'{value}' is not an amount")
    return number.quantize(CENT, rounding=ROUND_HALF_UP)


def iso_date(value: Any, what: str) -> Optional[str]:
    if value in (None, ""):
        return None
    text = str(value).strip()[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        raise ValueError(f"{what} '{value}' is not a date like 2026-01-31")


def fmt(value: Decimal) -> str:
    return f"{value:.2f}"


def bill_from(envelope: Dict[str, Any]) -> Tuple[Optional[Bill], Optional[str]]:
    """(the bill, None) or (None, what is wrong with the record, in words a person can act on)."""
    data = envelope["record"]["data"]
    if envelope["record"].get("mode") != "mapped":
        return None, "This destination needs its fields mapped. Open it and choose which field fills each one."
    try:
        vendor, number = str(data.get("vendor_name") or "").strip(), str(data.get("invoice_number") or "").strip()
        if not vendor:
            return None, "The supplier name is empty on this record."
        if not number:
            return None, "The invoice number is empty on this record."
        bill = Bill(vendor, str(data.get("vendor_id") or "").strip() or None, number, iso_date(data.get("invoice_date"), "The invoice date") or "",
                    iso_date(data.get("due_date"), "The due date"), (str(data.get("currency") or "").strip().upper() or None), money(data.get("total")),
                    str(data.get("memo") or "").strip())
        if not bill.date:
            return None, "The invoice date is empty on this record."
        tax = money(data.get("tax_total"))
        for raw in data.get("lines") or []:
            qty, price, amount = money(raw.get("quantity")), money(raw.get("unit_price")), money(raw.get("amount"))
            if amount is None and price is not None:
                amount = (price * (qty if qty is not None else Decimal(1))).quantize(CENT, rounding=ROUND_HALF_UP)
            if amount is None:
                return None, f"A line ('{raw.get('description') or 'no description'}') has no amount."
            bill.lines.append(Line(str(raw.get("description") or "").strip() or f"Invoice {number}", qty if qty is not None else Decimal(1),
                                   price if price is not None else amount, amount))
        if not bill.lines:
            if bill.total is None:
                return None, "There are no lines and no total, so there is nothing to bill."
            net = bill.total - (tax or Decimal(0))
            bill.lines.append(Line(bill.memo or f"Invoice {number}", Decimal(1), net, net))
        if tax:
            bill.lines.append(Line("Tax", Decimal(1), tax, tax))
        if bill.total is not None:
            summed = sum((l.amount for l in bill.lines), Decimal(0))
            if abs(summed - bill.total) > Decimal("0.05"):
                return None, (f"The lines add up to {fmt(summed)} but the invoice total is {fmt(bill.total)}. "
                              + ("Check the lines." if tax else "If tax is not in the lines, map the tax amount too."))
        return bill, None
    except ValueError as exc:
        return None, str(exc)


def fail(error: str, *, retryable: bool = False, request: str = "", reply: Optional[http.Reply] = None) -> SendResult:
    return SendResult(False, reply.status if reply else None, error, retryable, request=request, response=http.excerpt(reply.text) if reply else "",
                      retry_after=reply.retry_after if reply else None)


def failed_reply(reply: http.Reply, what: str, request: str = "") -> SendResult:
    error, retryable = http.failure_from(reply, what)
    detail = _vendor_message(reply)
    return fail(f"{error} {detail}".strip(), retryable=retryable, request=request, reply=reply)


def _vendor_message(reply: http.Reply) -> str:
    """The reason the other system gave, when its reply has one in the usual places."""
    body = reply.json()
    if isinstance(body, dict):
        for key in ("Message", "message", "Detail", "detail", "title", "error_description"):
            if isinstance(body.get(key), str):
                return body[key][:300]
        err = body.get("error") or body.get("Fault") or body.get("Elements")
        if isinstance(err, dict):
            return str(err.get("message") or err.get("Message") or err.get("code") or "")[:300]
        if isinstance(err, str):
            return err[:300]
        if isinstance(err, list) and err and isinstance(err[0], dict):
            return str(err[0].get("ValidationErrors") or err[0])[:300]
    return ""


def done(ref: str, request: str, reply: Optional[http.Reply] = None) -> SendResult:
    return SendResult(True, reply.status if reply else 200, external_ref=ref, request=request, response=http.excerpt(reply.text) if reply else "")


class BillAdapter(Adapter):
    """An accounting system that is given the record as a vendor bill."""
    category = "accounting"
    corrections = False       # a bill is created once; correcting it is done in the accounting system
    targets = TARGETS

    async def send(self, ctx: SendContext) -> SendResult:
        bill, problem = bill_from(ctx.envelope)
        if bill is None:
            return fail(problem or "The record can't be turned into a bill.")
        return await self.post_bill(ctx, bill)

    async def post_bill(self, ctx: SendContext, bill: Bill) -> SendResult:
        raise NotImplementedError


def create_vendors_field() -> Field:
    return Field("create_suppliers", "Create the supplier if it isn’t there", "checkbox", default=True,
                 help="When the supplier name isn’t found, add it. Turn off to refuse such bills instead.")
