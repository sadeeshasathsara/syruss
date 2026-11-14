"""
Billing for an organization, the one door the rest of the server uses:

    check_limit(db, key, ...)     refuses what would take it past a limit (402, a sentence people can act on)
    reserve_pages / charge_pages  the pages of each document accepted for reading come out of the monthly allowance,
                                  then out of prepaid credits
    standing(db, tenant_id)       its plan, period and every limit now
    roll_periods(db)              moves periods on, applies a downgrade at the period's end, ends trials
    set_override, change_plan, set_addon, start_trial   what staff change by hand
    apply_item                    switches on what was bought (invoices call it once an invoice is paid: see invoices.py)

An organization's workspaces share its plan: pages are counted for the organization, a workspace limit (members,
channels, ...) applies to each workspace on its own. Every organization has a subscription; one without (a new
organization) gets the Free plan, starting now, the first time it is needed.
"""
import calendar
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Set
from fastapi import HTTPException
from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.billing import CatalogItem, OrgLimitOverride, OrgSubscription, OrgSubscriptionAddon, UsageEvent
from app.models.document import Document
from app.models.template import DocumentTemplate
from app.services.billing import catalog, credits
from app.services.billing.entitlements import Limits, compose
from app.services.billing.limits import LIMITS, Where
from app.services.intake.rules import IntakeRejected

PAGES = "pages_per_month"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def month_after(start: datetime, anchor_day: int) -> datetime:
    """The same time a month later, on the anchor day (or the month's last day when it is shorter)."""
    year, month = (start.year + 1, 1) if start.month == 12 else (start.year, start.month + 1)
    return start.replace(year=year, month=month, day=min(anchor_day, calendar.monthrange(year, month)[1]))


@dataclass
class Standing:
    """Where an organization stands now: its subscription, its plan version and every limit (None: unlimited)."""
    subscription: OrgSubscription
    plan: CatalogItem
    limits: Limits
    overridden: Set[str] = field(default_factory=set)

    @property
    def where(self) -> Where:
        return Where(self.subscription.tenant_id, None, self.subscription.period_start)


async def subscription(db: AsyncSession, tenant_id: str, now: Optional[datetime] = None) -> OrgSubscription:
    """The organization's subscription, brought up to date (its period, a pending change, a trial that ended)."""
    now = now or _now()
    sub = await db.get(OrgSubscription, tenant_id)
    if sub is None:
        free = await catalog.latest(db, "plan", catalog.FREE)
        await db.execute(insert(OrgSubscription).values(
            tenant_id=tenant_id, plan_id=free.id, status="active", period_start=now, period_end=month_after(now, now.day),
            anchor_day=now.day, provider_ref={}, created_at=now, updated_at=now,
        ).on_conflict_do_nothing(index_elements=["tenant_id"]))
        sub = await db.get(OrgSubscription, tenant_id)
    await _settle(db, sub, now)
    return sub


async def _settle(db: AsyncSession, sub: OrgSubscription, now: datetime) -> None:
    while sub.period_end <= now:
        sub.period_start, sub.period_end = sub.period_end, month_after(sub.period_end, sub.anchor_day)
        if sub.pending_plan_id:
            sub.plan_id, sub.pending_plan_id = sub.pending_plan_id, None
    if sub.status == "trialing" and sub.trial_ends_at and sub.trial_ends_at <= now:
        if not (sub.paid_until and sub.paid_until > now):
            sub.plan_id = (await catalog.latest(db, "plan", catalog.FREE)).id
        sub.status = "active"


async def standing(db: AsyncSession, tenant_id: str, template_id: Optional[str] = None, now: Optional[datetime] = None) -> Standing:
    """Every limit for the organization; with a workspace, its own overrides of workspace limits count too."""
    now = now or _now()
    sub = await subscription(db, tenant_id, now)
    plan = await db.get(CatalogItem, sub.plan_id)
    addons = (await db.execute(select(CatalogItem.limits, OrgSubscriptionAddon.quantity).join(
        OrgSubscriptionAddon, OrgSubscriptionAddon.addon_id == CatalogItem.id).where(OrgSubscriptionAddon.tenant_id == tenant_id))).all()
    rows = (await db.execute(select(OrgLimitOverride).where(
        OrgLimitOverride.tenant_id == tenant_id,
        or_(OrgLimitOverride.template_id.is_(None), OrgLimitOverride.template_id == template_id) if template_id else OrgLimitOverride.template_id.is_(None),
        or_(OrgLimitOverride.expires_at.is_(None), OrgLimitOverride.expires_at > now),
    ))).scalars().all()
    overrides = {r.key: r.value for r in rows if r.template_id is None}
    overrides.update({r.key: r.value for r in rows if r.template_id and r.key in LIMITS and LIMITS[r.key].scope == "workspace"})
    return Standing(sub, plan, compose(plan.limits, [(limits, qty) for limits, qty in addons], overrides), set(overrides))


def _refusal(s: Standing, key: str, allowed: int, would_be: int) -> str:
    whose = "This organization's limit is" if key in s.overridden else f"The {s.plan.name} plan allows"
    return f"{whose} {LIMITS[key].count(allowed)}; this would make {would_be}."


async def check_limit(db: AsyncSession, key: str, *, template_id: Optional[str] = None, tenant_id: Optional[str] = None,
                      adding: int = 1) -> None:
    """Refuses (402) an action that would take the organization, or for a workspace limit the workspace, past its limit."""
    if tenant_id is None:
        tenant_id = (await db.get(DocumentTemplate, template_id)).tenant_id
    s = await standing(db, tenant_id, template_id)
    allowed = s.limits.get(key)
    if allowed is None:
        return
    used = await LIMITS[key].used(db, Where(tenant_id, template_id, s.subscription.period_start))
    if used + adding > allowed:
        raise HTTPException(status_code=402, detail=_refusal(s, key, allowed, used + adding))


async def _pages_left(db: AsyncSession, s: Standing) -> Optional[int]:
    allowed = s.limits.get(PAGES)
    return None if allowed is None else max(0, allowed - await LIMITS[PAGES].used(db, s.where))


def _out_of_pages(s: Standing, pages: int, left: int, balance: int) -> IntakeRejected:
    allowance = f"the {s.plan.name} plan's {s.limits[PAGES]} pages for this period"
    if left or balance:
        have = " and ".join(p for p in (f"{left} of {allowance}" if left else "", f"{balance} prepaid credits" if balance else "") if p)
        said = f"This document has {pages} pages, but only {have} {'is' if left + balance == 1 else 'are'} left."
    else:
        said = f"{allowance[0].upper()}{allowance[1:]} are used up, and there are no prepaid credits."
    return IntakeRejected(402, f"{said} An owner can buy credits in Billing; the plan's pages renew on {s.subscription.period_end:%d %B}.",
                          "plan_limit_reached")


async def _cover(db: AsyncSession, s: Standing, pages: int) -> int:
    """How many of a document's pages need credits (the plan's pages go first), or IntakeRejected when it doesn't fit."""
    left = await _pages_left(db, s)
    if left is None or pages <= left:
        return 0
    balance = await credits.balance(db, s.subscription.tenant_id)
    if pages - left > balance:
        raise _out_of_pages(s, pages, left, balance)
    return pages - left


async def reserve_pages(db: AsyncSession, *, tenant_id: str, pages: int) -> None:
    """Refuses a document whose pages don't fit, before it is stored. charge_pages checks again, under a lock."""
    await _cover(db, await standing(db, tenant_id), pages)


async def charge_pages(db: AsyncSession, document: Document) -> None:
    """
    Charges a document's pages, once (its usage event is keyed by the document): from the plan's pages for the period
    first, then from prepaid credits. The organization's charges are taken one at a time until the caller commits, so
    two documents at once can't both take the last pages.
    """
    key = f"pages:{document.id}"
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"billing:{document.tenant_id}"})
    if (await db.execute(select(UsageEvent.id).where(UsageEvent.idempotency_key == key))).first():
        return
    s = await standing(db, document.tenant_id)
    from_credits = await _cover(db, s, document.page_count)
    if from_credits:
        await credits.add(db, tenant_id=document.tenant_id, delta=-from_credits, kind="usage", created_by="system", ref=f"document:{document.id}")
    db.add(UsageEvent(tenant_id=document.tenant_id, template_id=document.template_id, metric="pages", quantity=document.page_count,
                      from_credits=from_credits, source_type="document", source_id=document.id, idempotency_key=key,
                      period_start=s.subscription.period_start))
    await db.flush()


async def charge_more_pages(db: AsyncSession, document: Document, pages: int) -> int:
    """
    A document turned out to have more pages than it was charged for (one picture showing several sheets): charges the
    difference, once for each new total, and sets the document's page count. Returns the pages added; raises
    IntakeRejected (402) when they don't fit, leaving the document as it was.
    """
    extra = pages - document.page_count
    if extra <= 0:
        return 0
    key = f"pages:{document.id}:to:{pages}"
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"billing:{document.tenant_id}"})
    if not (await db.execute(select(UsageEvent.id).where(UsageEvent.idempotency_key == key))).first():
        s = await standing(db, document.tenant_id)
        from_credits = await _cover(db, s, extra)
        if from_credits:
            await credits.add(db, tenant_id=document.tenant_id, delta=-from_credits, kind="usage", created_by="system", ref=f"document:{document.id}")
        db.add(UsageEvent(tenant_id=document.tenant_id, template_id=document.template_id, metric="pages", quantity=extra,
                          from_credits=from_credits, source_type="document", source_id=document.id, idempotency_key=key,
                          period_start=s.subscription.period_start))
    document.page_count = pages
    await db.flush()
    return extra


async def pages_by_workspace(db: AsyncSession, tenant_id: str, period_start: datetime) -> Dict[str, int]:
    """Pages each workspace used this period (allowance and credits alike)."""
    return {t: int(n) for t, n in (await db.execute(select(UsageEvent.template_id, func.sum(UsageEvent.quantity)).where(
        UsageEvent.tenant_id == tenant_id, UsageEvent.metric == "pages", UsageEvent.period_start == period_start,
    ).group_by(UsageEvent.template_id))).all() if t}


async def summary(db: AsyncSession, tenant_id: str) -> Dict[str, object]:
    """What people see: the plan, the period, the pages used out of the allowance, and the prepaid credits left."""
    s = await standing(db, tenant_id)
    return {"plan": s.plan.name, "plan_key": s.plan.key, "period_start": s.subscription.period_start, "period_end": s.subscription.period_end,
            "pages_used": await LIMITS[PAGES].used(db, s.where), "pages_limit": s.limits.get(PAGES), "limits": s.limits,
            "credits": await credits.balance(db, tenant_id)}


async def set_override(db: AsyncSession, *, tenant_id: str, key: str, value: Optional[int], reason: str, set_by: str,
                       template_id: Optional[str] = None, expires_at: Optional[datetime] = None) -> OrgLimitOverride:
    """Sets one limit by hand for the organization (or one workspace), replacing an earlier override of it."""
    if key not in LIMITS:
        raise ValueError(f"Unknown limit: {key}")
    if template_id and LIMITS[key].scope != "workspace":
        raise ValueError(f"{key} is counted for the whole organization, not per workspace")
    await db.execute(delete(OrgLimitOverride).where(
        OrgLimitOverride.tenant_id == tenant_id, OrgLimitOverride.key == key,
        OrgLimitOverride.template_id == template_id if template_id else OrgLimitOverride.template_id.is_(None)))
    row = OrgLimitOverride(tenant_id=tenant_id, template_id=template_id, key=key, value=value, reason=reason, set_by=set_by, expires_at=expires_at)
    db.add(row)
    await db.flush()
    return row


async def change_plan(db: AsyncSession, tenant_id: str, plan: CatalogItem, *, at_period_end: bool = False) -> OrgSubscription:
    """Moves the organization to a plan version now (an upgrade) or from its next period (a downgrade)."""
    if plan.kind != "plan" or plan.status != "published":
        raise ValueError("Only a published plan can be given to an organization")
    sub = await subscription(db, tenant_id)
    if at_period_end:
        sub.pending_plan_id = plan.id
    else:
        sub.plan_id, sub.pending_plan_id = plan.id, None
    await db.flush()
    return sub


async def roll_periods(db: AsyncSession, now: Optional[datetime] = None) -> int:
    """For the scheduler: settles every subscription whose period ended or whose trial ran out. Returns how many."""
    now = now or _now()
    due = (await db.execute(select(OrgSubscription).where(or_(
        OrgSubscription.period_end <= now,
        (OrgSubscription.status == "trialing") & (OrgSubscription.trial_ends_at <= now),
    )))).scalars().all()
    for sub in due:
        await _settle(db, sub, now)
    await db.commit()
    return len(due)


async def set_addon(db: AsyncSession, tenant_id: str, addon: CatalogItem, quantity: int, added_by: str) -> Optional[OrgSubscriptionAddon]:
    """How many of an add-on the organization has from now on; 0 removes it."""
    if addon.kind != "addon":
        raise ValueError("That isn't an add-on")
    row = (await db.execute(select(OrgSubscriptionAddon).where(
        OrgSubscriptionAddon.tenant_id == tenant_id, OrgSubscriptionAddon.addon_id == addon.id))).scalar_one_or_none()
    if quantity <= 0:
        if row:
            await db.delete(row)
        return None
    if addon.status != "published" and row is None:
        raise ValueError("Only a published add-on can be added")
    if row is None:
        row = OrgSubscriptionAddon(tenant_id=tenant_id, addon_id=addon.id, quantity=quantity, added_by=added_by)
        db.add(row)
    else:
        row.quantity = quantity
    await db.flush()
    return row


async def start_trial(db: AsyncSession, tenant_id: str, plan: CatalogItem, days: int) -> OrgSubscription:
    """Gives the organization a plan for a number of days; unless it is paid for by then, it goes back to Free."""
    sub = await change_plan(db, tenant_id, plan)
    sub.status, sub.trial_ends_at = "trialing", _now() + timedelta(days=days)
    await db.flush()
    return sub


async def apply_item(db: AsyncSession, tenant_id: str, item: CatalogItem, quantity: int, by: str, *, ref: Optional[str] = None,
                     reason: Optional[str] = None) -> str:
    """
    Gives the organization what it bought; says what was done, for the record. Moving from a free plan to a paid one
    starts a new period now, so the first month paid for is a whole month.
    """
    if item.kind == "plan":
        sub = await subscription(db, tenant_id)
        current = await db.get(CatalogItem, sub.plan_id)
        await change_plan(db, tenant_id, item)
        sub.status = "active"
        if item.price_cents and not (current and current.price_cents):
            now = _now()
            sub.period_start, sub.period_end, sub.anchor_day, sub.alerts = now, month_after(now, now.day), now.day, {}
            await db.flush()
        return f"moved the organization to the {item.name} plan"
    if item.kind == "addon":
        have = (await db.execute(select(OrgSubscriptionAddon.quantity).where(
            OrgSubscriptionAddon.tenant_id == tenant_id, OrgSubscriptionAddon.addon_id == item.id))).scalar() or 0
        await set_addon(db, tenant_id, item, have + quantity, by)
        return f"added {quantity} × {item.name}"
    pages = (item.pages or 0) * quantity
    await credits.add(db, tenant_id=tenant_id, delta=pages, kind="purchase", created_by=by, ref=ref or f"item:{item.id}", reason=reason or item.name)
    return f"added {pages:,} prepaid pages ({quantity} × {item.name})"


async def paid_through_period(db: AsyncSession, tenant_id: str) -> OrgSubscription:
    """A plan was paid for: the current period counts as paid (a trial ending now keeps the plan)."""
    sub = await subscription(db, tenant_id)
    sub.paid_until = max(filter(None, (sub.paid_until, sub.period_end)))
    await db.flush()
    return sub
