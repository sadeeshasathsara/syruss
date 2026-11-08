"""
The organization's billing, for the people who manage it (org billing.manage: its owners): the plan and period, pages
used out of the allowance and by which workspace, prepaid credits, add-ons, what is on offer, the billing details
invoices are made out to, and the invoices themselves.

Buying issues an invoice at once; what it sells is switched on once it is paid (app/services/billing/invoices.py).
"""
import asyncio
from datetime import date
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.auth import AuthContext, require_org_permission
from app.core.database import get_db_session
from app.models.audit import AuditLog
from app.models.billing import (
    BillingPayment, BillingProfile, CatalogItem, CreditNote, Invoice, OrgSubscriptionAddon, PaymentFile, PaymentSubmission,
)
from app.models.tenant import Tenant
from app.models.workspace import Workspace
from app.services.billing import cards, catalog, corrections, credits, invoices, notices, parties, payments, proofs, service as billing
from app.services.billing.limits import LIMITS
from app.services.billing.methods import METHODS, enabled
from app.services.billing.methods import card as card_api
from app.services.storage_service import storage_service

router = APIRouter(prefix="/billing", tags=["Billing"])
BILLING = Depends(require_org_permission("billing.manage"))
EMAIL = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


def _offer(i: CatalogItem) -> Dict[str, Any]:
    return {"id": i.id, "kind": i.kind, "key": i.key, "name": i.name, "description": i.description, "price_cents": i.price_cents,
            "currency": i.currency, "interval": i.interval, "limits": i.limits or {}, "pages": i.pages}


def _profile(p: Optional[BillingProfile]) -> Optional[Dict[str, Any]]:
    return parties.buyer_snapshot(p) | {"updated_at": p.updated_at} if p else None


def _card(p: Optional[BillingProfile]) -> Dict[str, Any]:
    """The card kept for renewals, and whether Stripe's page for managing it can be opened."""
    return {"kept": bool(p and p.card_payment_method), "label": p.card_label if p and p.card_payment_method else None,
            "can_manage": bool(p and p.stripe_customer_id and METHODS["card"].available())}


def _not_ready(exc: parties.NotReady) -> HTTPException:
    code = "billing_details_missing" if exc.who == "buyer" else "seller_not_ready"
    return HTTPException(status_code=409, detail={"code": code, "message": str(exc), "missing": exc.missing})


@router.get("")
async def overview(auth: AuthContext = BILLING, db: AsyncSession = Depends(get_db_session)):
    tenant_id = auth.tenant_id
    s = await billing.standing(db, tenant_id)
    sub = s.subscription
    summary = await billing.summary(db, tenant_id)
    pages = await billing.pages_by_workspace(db, tenant_id, sub.period_start)
    spaces = {w.id: w.name for w in (await db.execute(select(Workspace).where(Workspace.tenant_id == tenant_id))).scalars().all()}
    pending = await db.get(CatalogItem, sub.pending_plan_id) if sub.pending_plan_id else None
    addons = (await db.execute(select(OrgSubscriptionAddon, CatalogItem).join(CatalogItem, CatalogItem.id == OrgSubscriptionAddon.addon_id)
                               .where(OrgSubscriptionAddon.tenant_id == tenant_id))).all()
    payments = (await db.execute(select(BillingPayment, Invoice.number, CatalogItem.name).outerjoin(Invoice, Invoice.id == BillingPayment.invoice_id)
                                 .outerjoin(CatalogItem, CatalogItem.id == BillingPayment.item_id).where(BillingPayment.tenant_id == tenant_id)
                                 .order_by(BillingPayment.created_at.desc()).limit(50))).all()
    waiting = (await db.execute(select(Invoice).where(Invoice.tenant_id == tenant_id, Invoice.status.in_(invoices.PAYABLE))
                                .order_by(Invoice.due_at))).scalars().all()
    result = {
        "plan": _offer(s.plan), "pending_plan": _offer(pending) if pending else None, "status": sub.status,
        "period_start": sub.period_start, "period_end": sub.period_end, "trial_ends_at": sub.trial_ends_at,
        "limits": [{**LIMITS[k].describe(), "value": v} for k, v in s.limits.items()],
        "pages": {"used": summary["pages_used"], "limit": summary["pages_limit"],
                  "by_workspace": sorted(({"id": t, "name": spaces.get(t, "A deleted workspace"), "pages": n} for t, n in pages.items()),
                                         key=lambda w: -w["pages"])},
        "credits": {"balance": summary["credits"], "history": [
            {"delta": e.delta, "kind": e.kind, "reason": e.reason if e.kind != "usage" else None, "created_at": e.created_at}
            for e in await credits.history(db, tenant_id)]},
        "addons": [{"addon": _offer(i), "quantity": a.quantity} for a, i in addons],
        "payments": [{"amount_cents": p.amount_cents, "currency": p.currency, "method": p.method, "invoice_id": p.invoice_id, "invoice": number,
                      "for": name, "quantity": p.quantity, "reference": p.reference, "created_at": p.created_at} for p, number, name in payments],
        "invoices_waiting": [invoices.describe(i) for i in waiting],
        "billing_details_missing": parties.profile_missing(await parties.profile(db, tenant_id)),
        "methods": [m.describe() for m in enabled() if m.customer_can_use],
        "card": _card(await parties.profile(db, tenant_id)),
    }
    await db.commit()  # the subscription may have been created or brought up to date
    return result


@router.get("/catalog", dependencies=[BILLING])
async def offers(db: AsyncSession = Depends(get_db_session)):
    """What is on offer: the newest published version of each public plan, add-on and credit pack."""
    return {kind: [_offer(i) for i in await catalog.offered(db, kind)] for kind in ("plan", "addon", "credits")}


# ---- Billing details ---------------------------------------------------------------------------

@router.get("/profile")
async def get_profile(auth: AuthContext = BILLING, db: AsyncSession = Depends(get_db_session)):
    """Who invoices are made out to, and what is still missing before one can be issued."""
    p = await parties.profile(db, auth.tenant_id)
    return {"profile": _profile(p), "missing": parties.profile_missing(p)}


class ProfileIn(BaseModel):
    legal_name: str = Field(..., min_length=2, max_length=200)
    email: str = Field(..., max_length=255, pattern=EMAIL)
    address_line1: str = Field(..., min_length=2, max_length=200)
    address_line2: Optional[str] = Field(None, max_length=200)
    city: str = Field(..., min_length=1, max_length=120)
    region: Optional[str] = Field(None, max_length=120)
    postal_code: Optional[str] = Field(None, max_length=32)
    country: str = Field(..., pattern=r"^[A-Za-z]{2}$", description="ISO 3166-1 alpha-2")
    tax_id: Optional[str] = Field(None, max_length=64)
    po_number: Optional[str] = Field(None, max_length=64)


@router.put("/profile")
async def put_profile(body: ProfileIn, auth: AuthContext = BILLING, db: AsyncSession = Depends(get_db_session)):
    """Sets the billing details. Invoices already issued keep the details they were issued with."""
    values = {k: (v.strip() or None) if isinstance(v, str) else v for k, v in body.model_dump().items()}
    values["country"] = values["country"].upper()
    p = await parties.profile(db, auth.tenant_id)
    if p is None:
        p = BillingProfile(tenant_id=auth.tenant_id, updated_by=auth.user_id, **values)
        db.add(p)
    else:
        for field, value in values.items():
            setattr(p, field, value)
        p.updated_by = auth.user_id
    db.add(AuditLog(tenant_id=auth.tenant_id, template_id=None, event_type="BILLING_DETAILS_CHANGED", entity_type="Organization",
                    entity_id=auth.tenant_id, actor=auth.user_id, details={}))
    await db.commit()
    return {"profile": _profile(p), "missing": parties.profile_missing(p)}


# ---- Invoices ----------------------------------------------------------------------------------

async def _invoice(db: AsyncSession, auth: AuthContext, invoice_id: str) -> Invoice:
    invoice = await db.get(Invoice, invoice_id)
    if invoice is None or invoice.tenant_id != auth.tenant_id or invoice.status == "draft":
        raise HTTPException(status_code=404, detail="No such invoice.")
    return invoice


@router.get("/invoices")
async def list_invoices(auth: AuthContext = BILLING, db: AsyncSession = Depends(get_db_session)):
    """Every invoice issued to the organization, newest first (drafts are Syruss's until issued)."""
    rows = (await db.execute(select(Invoice).where(Invoice.tenant_id == auth.tenant_id, Invoice.status != "draft")
                             .order_by(Invoice.issued_at.desc()).limit(200))).scalars().all()
    return [invoices.describe(i) for i in rows]


@router.get("/invoices/{invoice_id}")
async def get_invoice(invoice_id: str, auth: AuthContext = BILLING, db: AsyncSession = Depends(get_db_session)):
    """One invoice, its lines and payments, and how it can be paid while it is waiting."""
    invoice = await _invoice(db, auth, invoice_id)
    out = invoices.describe(invoice, await invoices.lines_of(db, invoice.id), await invoices.payments_of(db, invoice.id))
    out["how_to_pay"] = [{**m.describe(), "instructions": m.instructions(invoice)} for m in enabled()
                         if m.customer_can_use] if invoice.status in invoices.PAYABLE else []
    sent = (await db.execute(select(PaymentSubmission).where(PaymentSubmission.invoice_id == invoice.id)
                             .order_by(PaymentSubmission.created_at.desc()))).scalars().all()
    out["submissions"] = [payments.describe(s, await proofs.files_of(db, submission_id=s.id)) for s in sent]
    out["credit_notes"] = [corrections.note_dict(n, await corrections.refunds_of(db, credit_note_id=n.id))
                           for n in await corrections.notes_of(db, invoice.id)]
    return out


@router.get("/credit-notes/{note_id}/pdf")
async def credit_note_pdf(note_id: str, auth: AuthContext = BILLING, db: AsyncSession = Depends(get_db_session)):
    note = await db.get(CreditNote, note_id)
    if note is None or note.tenant_id != auth.tenant_id or not note.pdf_key:
        raise HTTPException(status_code=404, detail="No such credit note.")
    data = await asyncio.to_thread(storage_service.download_file, note.pdf_key)
    return Response(content=data, media_type="application/pdf", headers={
        "Content-Disposition": f'inline; filename="Credit note {note.number}.pdf"', "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


async def _pdf(invoice: Invoice, which: str) -> Response:
    data = await invoices.file_of(invoice, which)
    if data is None:
        raise HTTPException(status_code=404, detail="There is no receipt until the invoice is paid." if which == "receipt" else "No PDF yet.")
    name = f"{'Receipt' if which == 'receipt' else 'Invoice'} {invoice.number}.pdf"
    return Response(content=data, media_type="application/pdf", headers={
        "Content-Disposition": f'inline; filename="{name}"', "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


@router.get("/invoices/{invoice_id}/pdf")
async def invoice_pdf(invoice_id: str, auth: AuthContext = BILLING, db: AsyncSession = Depends(get_db_session)):
    return await _pdf(await _invoice(db, auth, invoice_id), "pdf")


@router.get("/invoices/{invoice_id}/receipt")
async def invoice_receipt(invoice_id: str, auth: AuthContext = BILLING, db: AsyncSession = Depends(get_db_session)):
    return await _pdf(await _invoice(db, auth, invoice_id), "receipt")


# ---- Paying: proof of a payment made outside the app (a bank transfer) --------------------------

@router.post("/invoices/{invoice_id}/payments", status_code=201)
async def send_payment(
    invoice_id: str,
    method: str = Form(..., description="A way of paying that takes proof (bank_transfer)"),
    amount_cents: int = Form(..., gt=0),
    paid_on: date = Form(...),
    reference: Optional[str] = Form(None, max_length=200, description="The reference on the bank transfer"),
    note: Optional[str] = Form(None, max_length=2000),
    files: List[UploadFile] = File(..., description="The bank slip or transfer confirmation: PDF, JPEG, PNG or WebP"),
    auth: AuthContext = BILLING,
    db: AsyncSession = Depends(get_db_session),
):
    """Sends proof of a payment for an invoice. It counts once Syruss has checked it against the account."""
    invoice = await _invoice(db, auth, invoice_id)
    uploads = [payments.Upload(f.filename, await f.read(proofs.MAX_BYTES + 1)) for f in files[:proofs.MAX_FILES + 1]]
    try:
        sent = await payments.submit(db, invoice, method=method, amount_cents=amount_cents, paid_on=paid_on, reference=reference, note=note,
                                     files=uploads, by_id=auth.user_id, by_name=auth.username or auth.user_id)
    except payments.Conflict as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    db.add(AuditLog(tenant_id=auth.tenant_id, template_id=None, event_type="BILLING_PAYMENT_SENT", entity_type="Organization",
                    entity_id=auth.tenant_id, actor=auth.user_id, details={"invoice": invoice.number, "amount_cents": amount_cents,
                                                                            "currency": invoice.currency}))
    await db.commit()
    await notices.payment_submitted(sent.id)
    return payments.describe(sent, await proofs.files_of(db, submission_id=sent.id))


async def _submission(db: AsyncSession, auth: AuthContext, submission_id: str) -> PaymentSubmission:
    s = await db.get(PaymentSubmission, submission_id)
    if s is None or s.tenant_id != auth.tenant_id:
        raise HTTPException(status_code=404, detail="No such payment.")
    return s


@router.post("/submissions/{submission_id}/withdraw")
async def withdraw_payment(submission_id: str, auth: AuthContext = BILLING, db: AsyncSession = Depends(get_db_session)):
    """Takes back proof of a payment that hasn't been checked yet (to send it again, corrected)."""
    try:
        s = await payments.withdraw(db, await _submission(db, auth, submission_id))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await db.commit()
    return payments.describe(s, await proofs.files_of(db, submission_id=s.id))


def file_response(f: PaymentFile, data: bytes) -> Response:
    """
    A slip: a photo is shown in the browser, unable to run anything; a PDF is downloaded (browsers can't show one
    sandboxed, and an uploaded PDF is never opened inside the app). Never sniffed as anything other than its type.
    """
    headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
    if f.content_type == "application/pdf":
        headers["Content-Disposition"] = f'attachment; filename="{f.filename}"'
    else:
        headers["Content-Disposition"] = f'inline; filename="{f.filename}"'
        headers["Content-Security-Policy"] = "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; sandbox"
    return Response(content=data, media_type=f.content_type, headers=headers)


@router.get("/submissions/{submission_id}/files/{file_id}")
async def payment_file(submission_id: str, file_id: str, auth: AuthContext = BILLING, db: AsyncSession = Depends(get_db_session)):
    s = await _submission(db, auth, submission_id)
    f = await db.get(PaymentFile, file_id)
    if f is None or f.submission_id != s.id:
        raise HTTPException(status_code=404, detail="No such file.")
    return file_response(f, await proofs.read(f))


# ---- Paying by card (Stripe) -------------------------------------------------------------------

class CheckoutIn(BaseModel):
    save_card: bool = Field(False, description="Keep the card to pay renewals by themselves")


@router.post("/invoices/{invoice_id}/checkout")
async def card_checkout(invoice_id: str, body: CheckoutIn, auth: AuthContext = BILLING, db: AsyncSession = Depends(get_db_session)):
    """The address of Stripe's page to pay what is due on the invoice by card."""
    invoice = await _invoice(db, auth, invoice_id)
    tenant = await db.get(Tenant, auth.tenant_id)
    try:
        url = await cards.checkout(db, invoice, slug=tenant.slug, save_card=body.save_card)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    await db.commit()
    return {"url": url}


@router.post("/card/portal")
async def card_portal(auth: AuthContext = BILLING, db: AsyncSession = Depends(get_db_session)):
    """Stripe's page where the kept card is changed or removed."""
    tenant = await db.get(Tenant, auth.tenant_id)
    try:
        return {"url": await cards.portal_url(db, auth.tenant_id, tenant.slug)}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.post("/card/forget")
async def forget_card(auth: AuthContext = BILLING, db: AsyncSession = Depends(get_db_session)):
    """Stops paying renewals with the kept card; they are invoiced to pay by hand instead."""
    p = await parties.profile(db, auth.tenant_id)
    if p and p.card_payment_method:
        p.card_payment_method, p.card_label = None, None
        db.add(AuditLog(tenant_id=auth.tenant_id, template_id=None, event_type="BILLING_CARD_FORGOTTEN", entity_type="Organization",
                        entity_id=auth.tenant_id, actor=auth.user_id, details={}))
        await db.commit()
    return _card(p)


@router.post("/webhooks/stripe", include_in_schema=False)
async def stripe_webhook(request: Request, stripe_signature: Optional[str] = Header(None, alias="Stripe-Signature"),
                         db: AsyncSession = Depends(get_db_session)):
    """Stripe telling us about a payment. Not signed in: the signature (STRIPE_WEBHOOK_SECRET) is what proves it is Stripe."""
    if not METHODS["card"].available():
        raise HTTPException(status_code=404, detail="Card payments aren't set up.")
    try:
        event = card_api.event_from(await request.body(), stripe_signature or "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    paid = await cards.handle(db, event)
    if paid:
        db.add(AuditLog(tenant_id=paid[0].tenant_id, template_id=None, event_type="BILLING_CARD_PAID", entity_type="Organization",
                        entity_id=paid[0].tenant_id, actor="stripe", details={"invoice": paid[0].number}))
    await db.commit()
    if paid:
        await notices.invoice_paid(paid[0].id, paid[1])
    return {"received": True}


# ---- Buying ------------------------------------------------------------------------------------

class PurchaseIn(BaseModel):
    item_id: str
    quantity: int = Field(1, ge=1, le=1000)
    note: Optional[str] = Field(None, max_length=2000, description="Printed on the invoice (a purchase order number, ...)")


@router.post("/purchases", status_code=201)
async def purchase(body: PurchaseIn, auth: AuthContext = BILLING, db: AsyncSession = Depends(get_db_session)):
    """Buys a plan, an add-on or credits: an invoice, issued now; it is switched on once paid."""
    item = await db.get(CatalogItem, body.item_id)
    if item is None or item.status != "published" or not item.public:
        raise HTTPException(status_code=404, detail="That isn't on offer.")
    try:
        invoice, message, new = await invoices.purchase(db, auth.tenant_id, item, body.quantity, by=auth.user_id, note=body.note)
    except parties.NotReady as exc:
        raise _not_ready(exc)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if new:
        db.add(AuditLog(tenant_id=auth.tenant_id, template_id=None, event_type="BILLING_INVOICED", entity_type="Organization",
                        entity_id=auth.tenant_id, actor=auth.user_id, details={"item": item.name, "quantity": body.quantity, "invoice": invoice.number}))
    await db.commit()
    if new:
        await (notices.invoice_issued(invoice.id) if invoice.status == "open" else notices.invoice_paid(invoice.id, None))
    return {"status": "invoiced" if invoice else "scheduled", "message": message, "invoice": invoices.describe(invoice) if invoice else None}
