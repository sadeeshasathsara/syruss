"""
Renewals, and what happens when one isn't paid. The scheduler runs these with the platform's other jobs; each
organization's step commits on its own, so one failing doesn't hold up the rest.

    issue_renewals   renewal_lead_days before a paid plan's period ends: an invoice for the next period (the plan, or the
                     plan it moves to then, and its add-ons), due when that period starts. A card the organization kept
                     pays it straight away; one that is declined leaves the invoice to be paid by hand, and says so.
    send_reminders   at each of reminder_days from an open invoice's due date (before and after), once each
    lapse_unpaid     a renewal not paid by its due date makes the subscription past due; grace_days later the organization
                     moves to the Free plan (its data stays) and the invoice is uncollectible. Paying it later, even
                     then, puts the plan back (invoices.settle).

A renewal is made once a period (the database holds one renewal invoice for each organization and period). One that
can't be issued yet, because the billing details aren't filled in, stays a draft for staff.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.billing import CatalogItem, Invoice, OrgSubscription, OrgSubscriptionAddon
from app.services.billing import catalog, invoices, notices, parties, service as billing
from app.services.billing.methods import METHODS

logger = logging.getLogger("core.billing.dunning")


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def run(db: AsyncSession, now: Optional[datetime] = None) -> None:
    now = now or _now()
    for step in (issue_renewals, send_reminders, lapse_unpaid):
        await step(db, now)


async def _renewal_for(db: AsyncSession, sub: OrgSubscription) -> Optional[Invoice]:
    """The draft renewal for a subscription's next period, or None when nothing is to be paid then."""
    plan = await db.get(CatalogItem, sub.pending_plan_id or sub.plan_id)
    if plan is None or not plan.price_cents:
        return None
    start, end = sub.period_end, billing.month_after(sub.period_end, sub.anchor_day)
    if (await db.execute(select(Invoice.id).where(Invoice.tenant_id == sub.tenant_id, Invoice.kind == "renewal",
                                                  Invoice.period_start == start))).first():
        return None
    lines = [invoices.Line(f"{plan.name} plan", 1, plan.price_cents, "plan", plan, start, end)]
    addons = (await db.execute(select(OrgSubscriptionAddon, CatalogItem).join(CatalogItem, CatalogItem.id == OrgSubscriptionAddon.addon_id)
                               .where(OrgSubscriptionAddon.tenant_id == sub.tenant_id))).all()
    lines += [invoices.Line(f"{item.name} (monthly add-on)", a.quantity, item.price_cents, "addon", item, start, end)
              for a, item in addons if item.price_cents and item.currency == plan.currency]
    return await invoices.draft(db, sub.tenant_id, lines, kind="renewal", created_by="system", period=(start, end))


async def issue_renewals(db: AsyncSession, now: datetime) -> int:
    """Issues the renewals that are due to go out. Returns how many were made."""
    seller = await parties.seller(db)
    lead = timedelta(days=seller.renewal_lead_days)
    due = (await db.execute(select(OrgSubscription.tenant_id).where(
        OrgSubscription.status.in_(("active", "past_due")), OrgSubscription.period_end <= now + lead))).scalars().all()
    made = 0
    for tenant_id in due:
        try:
            sub = await billing.subscription(db, tenant_id, now)
            if sub.period_end > now + lead or sub.status == "trialing":
                await db.commit()
                continue
            invoice = await _renewal_for(db, sub)
            if invoice is None:
                await db.commit()
                continue
            made += 1
            try:
                await invoices.issue(db, invoice, due_at=sub.period_end)
            except parties.NotReady:
                await db.commit()  # it stays a draft; staff see it
                continue
            await db.commit()
        except Exception:
            await db.rollback()
            logger.exception("Renewal for %s failed", tenant_id)
            continue
        await _pay_with_kept_card(db, invoice)
    return made


async def _pay_with_kept_card(db: AsyncSession, invoice: Invoice) -> None:
    from app.services.billing import cards  # (cards uses Stripe; only needed when a card is kept)
    profile = await parties.profile(db, invoice.tenant_id)
    if profile and profile.card_payment_method and METHODS["card"].available():
        try:
            paid = await cards.charge_saved_card(db, invoice)
            await db.commit()
            if paid:
                await notices.invoice_paid(invoice.id, paid[1])
                return
        except ValueError as exc:
            await db.rollback()
            await notices.invoice_issued(invoice.id)
            await notices.invoice_reminder(invoice.id, None, note=str(exc))
            return
    await notices.invoice_issued(invoice.id)


async def _try_kept_card_again(db: AsyncSession, invoice_id: str) -> bool:
    """True when the kept card paid the invoice now."""
    from app.services.billing import cards
    invoice = await db.get(Invoice, invoice_id)
    profile = await parties.profile(db, invoice.tenant_id)
    if not (profile and profile.card_payment_method and METHODS["card"].available()):
        return False
    try:
        paid = await cards.charge_saved_card(db, invoice)
        await db.commit()
    except ValueError:
        await db.rollback()
        return False
    if paid:
        await notices.invoice_paid(invoice_id, paid[1])
    return bool(paid)


async def send_reminders(db: AsyncSession, now: datetime) -> int:
    """Sends each reminder an open invoice is due, once (only the latest one, when several came due at once)."""
    seller = await parties.seller(db)
    offsets = sorted({int(d) for d in (seller.reminder_days or [])}, reverse=True)
    if not offsets:
        return 0
    rows = (await db.execute(select(Invoice).where(Invoice.status == "open", Invoice.due_at.isnot(None),
                                                   Invoice.due_at <= now + timedelta(days=offsets[0])))).scalars().all()
    sent_now = 0
    for invoice in rows:
        came_due = [d for d in offsets if now >= invoice.due_at - timedelta(days=d)]
        already = set(invoice.reminders or [])
        if not came_due or set(came_due) <= already:
            continue
        latest = min(came_due)
        invoice.reminders = sorted(already | set(came_due), reverse=True)
        invoice_id, renewal = invoice.id, invoice.kind == "renewal"  # read now: a rolled-back card attempt forgets the row
        await db.commit()
        if latest not in already:
            # A renewal with a kept card is tried on the card again before anyone is chased: cards are declined for
            # passing reasons (a limit, a bank's check), and most go through a few days later.
            if renewal and await _try_kept_card_again(db, invoice_id):
                continue
            await notices.invoice_reminder(invoice_id, latest)
            sent_now += 1
    return sent_now


async def lapse_unpaid(db: AsyncSession, now: datetime) -> int:
    """Marks overdue renewals' subscriptions past due, and moves to Free those past the grace period. Returns how many moved."""
    seller = await parties.seller(db)
    grace = timedelta(days=seller.grace_days)
    rows = (await db.execute(select(Invoice).where(Invoice.kind == "renewal", Invoice.status == "open", Invoice.due_at <= now))).scalars().all()
    moved = 0
    for invoice in rows:
        try:
            sub = await billing.subscription(db, invoice.tenant_id, now)
            if now < invoice.due_at + grace:
                if sub.status == "active":
                    sub.status = "past_due"
                await db.commit()
                continue
            free = await catalog.latest(db, "plan", catalog.FREE)
            was = (await db.get(CatalogItem, sub.plan_id)).name
            await billing.change_plan(db, invoice.tenant_id, free)
            for a in (await db.execute(select(OrgSubscriptionAddon).where(OrgSubscriptionAddon.tenant_id == invoice.tenant_id))).scalars().all():
                await db.delete(a)
            sub.status, invoice.status = "active", "uncollectible"
            await db.commit()
            moved += 1
            await notices.plan_lapsed(invoice.id, was)
        except Exception:
            await db.rollback()
            logger.exception("Lapsing invoice %s failed", invoice.id)
    return moved
