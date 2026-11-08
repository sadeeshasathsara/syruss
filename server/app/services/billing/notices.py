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
from app.models.billing import Invoice
from app.models.identity import OrgMember, User
from app.models.tenant import Tenant
from app.services.billing.invoices import lines_of
from app.services.billing.pdf import money
from app.services.notifications import Recipient, inapp, notify
from app.services.notifications.kinds import INVOICE_ISSUED, PAYMENT_RECEIVED, Kind
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
