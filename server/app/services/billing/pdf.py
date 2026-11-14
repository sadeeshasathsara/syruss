"""
The PDF of an invoice, and of its receipt once paid. Everything printed comes from what the invoice copied in when it
was issued (seller, buyer, bank accounts, tax), so printing it again years later gives the same document.
"""
import io
import os
from datetime import datetime
from typing import List, Optional, Sequence, Tuple
from xml.sax.saxutils import escape
import reportlab
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from app.core.config import settings
from app.models.billing import BillingPayment, Invoice, InvoiceLine

INK, MUTED, LINE = colors.HexColor("#15171a"), colors.HexColor("#5b616b"), colors.HexColor("#e4e6ea")
METHODS = {"bank_transfer": "Bank transfer", "staff_recorded": "Recorded by Syruss", "card": "Card"}
_fonts: Optional[Tuple[str, str]] = None


def _font_pair() -> Tuple[str, str]:
    """(regular, bold): BILLING_PDF_FONT, else DejaVu Sans where installed, else ReportLab's own Vera (Latin only)."""
    global _fonts
    if _fonts is None:
        bundled = os.path.join(os.path.dirname(reportlab.__file__), "fonts")
        candidates = [(settings.BILLING_PDF_FONT, settings.BILLING_PDF_FONT.replace(".ttf", "-Bold.ttf"))] if settings.BILLING_PDF_FONT else []
        candidates += [("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
                       (os.path.join(bundled, "Vera.ttf"), os.path.join(bundled, "VeraBd.ttf"))]
        for regular, bold in candidates:
            if os.path.exists(regular):
                pdfmetrics.registerFont(TTFont("InvoiceSans", regular))
                pdfmetrics.registerFont(TTFont("InvoiceSans-Bold", bold if os.path.exists(bold) else regular))
                _fonts = ("InvoiceSans", "InvoiceSans-Bold")
                break
        else:
            _fonts = ("Helvetica", "Helvetica-Bold")
    return _fonts


def money(cents: int, currency: str) -> str:
    """"USD 1,234.50"."""
    sign = "-" if cents < 0 else ""
    return f"{sign}{currency} {abs(cents) / 100:,.2f}"


def _day(value: Optional[datetime]) -> str:
    return f"{value:%d %b %Y}" if value else ""


def _text(value: object) -> str:
    """Printable and safe inside a Paragraph (its markup is XML)."""
    return escape(str(value or "")).replace("\n", "<br/>")


def _address(party: dict) -> List[str]:
    if "address_line1" in party:  # a buyer
        city = " ".join(p for p in (party.get("postal_code"), party.get("city")) if p)
        return [p for p in (party.get("address_line1"), party.get("address_line2"), city, party.get("region"), party.get("country")) if p]
    return [line for line in (party.get("address") or "").splitlines() if line.strip()] + ([party["country"]] if party.get("country") else [])


def render_credit_note(note, invoice: Invoice, refunds: Sequence = ()) -> bytes:
    """A credit note: which invoice it corrects, by how much and why, and any money given back with it."""
    regular, bold = _font_pair()
    base = ParagraphStyle("base", fontName=regular, fontSize=9.5, leading=13, textColor=INK)
    muted = ParagraphStyle("muted", parent=base, textColor=MUTED, fontSize=8.5, leading=12)
    title = ParagraphStyle("title", parent=base, fontName=bold, fontSize=20, leading=24)
    strong = ParagraphStyle("strong", parent=base, fontName=bold)
    right = ParagraphStyle("right", parent=base, alignment=TA_RIGHT)
    seller, buyer = invoice.seller or {}, invoice.buyer or {}
    out = io.BytesIO()
    doc = SimpleDocTemplate(out, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
                            title=f"Credit note {note.number}", author=seller.get("legal_name") or "Syruss")
    width = A4[0] - 36 * mm
    story: list = [Table([[Paragraph("Credit note", title), Paragraph(f"<b>{_text(note.number)}</b>", right)]], colWidths=[width * 0.6, width * 0.4],
                         style=[("VALIGN", (0, 0), (-1, -1), "BOTTOM")]), Spacer(1, 6 * mm)]
    parties = []
    for label, p in (("From", seller), ("To", buyer)):
        rows = [Paragraph(label, muted), Paragraph(_text(p.get("legal_name")), strong)] + [Paragraph(_text(x), base) for x in _address(p)]
        if p.get("tax_id"):
            rows.append(Paragraph(f"Tax ID: {_text(p['tax_id'])}", base))
        parties.append(rows)
    facts = [Paragraph(f"Issued: <b>{_day(note.created_at)}</b>", right), Paragraph(f"For invoice: <b>{_text(invoice.number)}</b>", right),
             Paragraph(f"Invoice total: <b>{money(invoice.total_cents, invoice.currency)}</b>", right)]
    story.append(Table([parties + [facts]], colWidths=[width * 0.36, width * 0.36, width * 0.28],
                       style=[("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story += [Spacer(1, 8 * mm), Paragraph("Reason", muted), Paragraph(_text(note.reason), base), Spacer(1, 6 * mm),
              Table([[Paragraph("<b>Credited</b>", right), Paragraph(f"<b>{money(note.amount_cents, note.currency)}</b>", right)]],
                    colWidths=[width * 0.81, width * 0.19])]
    for r in refunds:
        story.append(Paragraph(f"Refunded {money(r.amount_cents, r.currency)} ({_text(METHODS.get(r.method, r.method))}"
                               f"{', ' + _text(r.reference) if r.reference else ''}).", base))
    if seller.get("footer"):
        story += [Spacer(1, 6 * mm), Paragraph(_text(seller["footer"]), muted)]
    doc.build(story)
    return out.getvalue()


def render(invoice: Invoice, lines: Sequence[InvoiceLine], payments: Optional[Sequence[BillingPayment]] = None) -> bytes:
    """The invoice as a PDF; with its payments, the receipt for it."""
    regular, bold = _font_pair()
    base = ParagraphStyle("base", fontName=regular, fontSize=9.5, leading=13, textColor=INK)
    muted = ParagraphStyle("muted", parent=base, textColor=MUTED, fontSize=8.5, leading=12)
    title = ParagraphStyle("title", parent=base, fontName=bold, fontSize=20, leading=24)
    strong = ParagraphStyle("strong", parent=base, fontName=bold)
    right = ParagraphStyle("right", parent=base, alignment=TA_RIGHT)
    right_strong = ParagraphStyle("right_strong", parent=strong, alignment=TA_RIGHT)
    receipt = payments is not None
    seller, buyer, cur = invoice.seller or {}, invoice.buyer or {}, invoice.currency

    out = io.BytesIO()
    doc = SimpleDocTemplate(out, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
                            title=f"{'Receipt' if receipt else 'Invoice'} {invoice.number}", author=seller.get("legal_name") or "Syruss")
    width = A4[0] - 36 * mm
    story: list = []

    heading = [[Paragraph("Receipt" if receipt else "Invoice", title), Paragraph(f"<b>{_text(invoice.number)}</b>", right)]]
    story.append(Table(heading, colWidths=[width * 0.6, width * 0.4], style=[("VALIGN", (0, 0), (-1, -1), "BOTTOM")]))
    story.append(Spacer(1, 6 * mm))

    def party(label: str, p: dict) -> list:
        rows = [Paragraph(label, muted), Paragraph(_text(p.get("legal_name")), strong)]
        rows += [Paragraph(_text(line), base) for line in _address(p)]
        if p.get("tax_id"):
            rows.append(Paragraph(f"Tax ID: {_text(p['tax_id'])}", base))
        if p.get("email"):
            rows.append(Paragraph(_text(p["email"]), base))
        return rows

    dates = [("Issued", _day(invoice.issued_at)), ("Due", _day(invoice.due_at))]
    if invoice.period_start and invoice.period_end:
        dates.append(("Period", f"{_day(invoice.period_start)} to {_day(invoice.period_end)}"))
    if buyer.get("po_number"):
        dates.append(("Purchase order", buyer["po_number"]))
    if receipt and invoice.paid_at:
        dates.append(("Paid", _day(invoice.paid_at)))
    facts = [Paragraph(f"{label}: <b>{_text(value)}</b>", right) for label, value in dates if value]
    story.append(Table([[party("From", seller), party("Bill to", buyer), facts]], colWidths=[width * 0.36, width * 0.36, width * 0.28],
                       style=[("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    story.append(Spacer(1, 8 * mm))

    rows = [[Paragraph("Description", muted), Paragraph("Qty", muted), Paragraph("Unit price", muted), Paragraph("Amount", muted)]]
    for line in sorted(lines, key=lambda x: x.position):
        text = _text(line.description)
        if line.period_start and line.period_end:
            text += f"<br/><font size=8 color='#5b616b'>{_day(line.period_start)} to {_day(line.period_end)}</font>"
        rows.append([Paragraph(text, base), Paragraph(str(line.quantity), right), Paragraph(money(line.unit_cents, cur), right),
                     Paragraph(money(line.amount_cents, cur), right)])
    table = Table(rows, colWidths=[width * 0.52, width * 0.1, width * 0.19, width * 0.19], repeatRows=1)
    table.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, 0), 0.6, LINE), ("LINEBELOW", (0, 1), (-1, -1), 0.3, LINE),
                               ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    story.append(table)
    story.append(Spacer(1, 4 * mm))

    totals = [("Subtotal", money(invoice.subtotal_cents, cur), False)]
    for t in invoice.tax_lines or []:
        label = f"{t['name']} ({t['rate_bp'] / 100:g}%)" if t.get("rate_bp") else str(t["name"])
        totals.append((label, money(int(t.get("tax_cents") or 0), cur), False))
    totals.append(("Total", money(invoice.total_cents, cur), True))
    if receipt or invoice.amount_paid_cents or invoice.amount_credited_cents:
        totals.append(("Paid", money(invoice.amount_paid_cents - invoice.amount_refunded_cents, cur), False))
        if invoice.amount_credited_cents:
            totals.append(("Credited", money(invoice.amount_credited_cents, cur), False))
        owed = invoice.total_cents - (invoice.amount_paid_cents - invoice.amount_refunded_cents) - invoice.amount_credited_cents
        totals.append(("Amount due", money(max(0, owed), cur), True))
    story.append(Table([[Paragraph(label, right_strong if big else right), Paragraph(value, right_strong if big else right)] for label, value, big in totals],
                       colWidths=[width * 0.81, width * 0.19], style=[("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]))
    for t in invoice.tax_lines or []:
        if t.get("note"):
            story.append(Paragraph(_text(t["note"]), muted))
    story.append(Spacer(1, 8 * mm))

    if receipt:
        story.append(Paragraph("Payments received", strong))
        for p in payments:
            what = ", ".join(x for x in (METHODS.get(p.method, p.method), p.reference) if x)
            story.append(Paragraph(f"{_day(p.created_at) if p.received_on is None else f'{p.received_on:%d %b %Y}'}: "
                                   f"{money(p.amount_cents, p.currency)} ({_text(what)})", base))
    elif invoice.bank:
        story.append(Paragraph("How to pay", strong))
        story.append(Paragraph(f"By bank transfer, quoting <b>{_text(invoice.number)}</b> as the reference:", base))
        for a in invoice.bank:
            parts = [a.get("bank"), f"Account name: {a.get('account_name')}", f"Account number: {a.get('account_number')}",
                     f"Branch: {a['branch']}" if a.get("branch") else None, f"SWIFT: {a['swift']}" if a.get("swift") else None]
            story.append(Spacer(1, 2 * mm))
            story.append(Paragraph("<br/>".join(_text(p) for p in parts if p), base))
            if a.get("instructions"):
                story.append(Paragraph(_text(a["instructions"]), muted))
    if invoice.note:
        story.append(Spacer(1, 6 * mm))
        story.append(Paragraph(_text(invoice.note), muted))
    if seller.get("footer"):
        story.append(Spacer(1, 6 * mm))
        story.append(Paragraph(_text(seller["footer"]), muted))
    doc.build(story)
    return out.getvalue()
