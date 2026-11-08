"""
Billing, for platform staff (billing.manage to change anything): the catalog (plans, add-ons and credit packs, versioned),
each organization's subscription, Syruss's own billing details (seller, bank accounts, tax rates), and invoices:
drafting one for a custom deal, issuing, voiding, switching one on before it is paid, and recording payments.

Every change needs a reason and a fresh sign-in. It is written to the platform audit log and shown in the organization's
own activity ("… of Syruss support moved the organization to the Team plan, because: …").
"""
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.v1.billing import file_response
from app.api.v1.sysadmin.access import Staff, record, require_staff
from app.api.v1.sysadmin.common import names, tell_organization
from app.core.database import get_db_session
from app.models.billing import (
    BankAccount, BillingPayment, CatalogItem, Invoice, OrgLimitOverride, OrgSubscription, OrgSubscriptionAddon, PaymentFile,
    PaymentSubmission, TaxRate,
)
from app.models.tenant import Tenant
from app.models.workspace import Workspace
from app.services.billing import credits, invoices, notices, parties, payments, proofs, service as billing
from app.services.billing.limits import LIMITS
from app.services.billing.methods import enabled
from app.services.billing.pdf import money

router = APIRouter(prefix="/billing")
VIEW = Depends(require_staff())
CHANGE = require_staff("billing.manage", recent=True)


def item_dict(i: CatalogItem, organizations: int = 0) -> Dict[str, Any]:
    return {"id": i.id, "kind": i.kind, "key": i.key, "version": i.version, "name": i.name, "description": i.description, "status": i.status,
            "price_cents": i.price_cents, "currency": i.currency, "interval": i.interval, "public": i.public, "sort": i.sort,
            "limits": i.limits or {}, "pages": i.pages, "created_by": i.created_by, "created_at": i.created_at,
            "published_at": i.published_at, "archived_at": i.archived_at, "organizations": organizations}


async def _item(db: AsyncSession, item_id: str) -> CatalogItem:
    item = await db.get(CatalogItem, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="No such catalog item.")
    return item


async def _tenant(db: AsyncSession, tenant_id: str) -> Tenant:
    tenant = await db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="No such organization.")
    return tenant


def _refused(exc: ValueError) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


@router.get("/limits", dependencies=[VIEW])
async def limits():
    """Every limit a plan or add-on can set (the catalog editor's fields), and the ways invoices can be paid here."""
    return {"limits": [limit.describe() for limit in LIMITS.values()], "methods": [m.describe() for m in enabled()]}


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

@router.get("/catalog", dependencies=[VIEW])
async def list_catalog(kind: Optional[str] = None, db: AsyncSession = Depends(get_db_session)):
    """Every version of everything sold, newest version first, with how many organizations have each."""
    stmt = select(CatalogItem).order_by(CatalogItem.kind, CatalogItem.sort, CatalogItem.key, CatalogItem.version.desc())
    rows = (await db.execute(stmt.where(CatalogItem.kind == kind) if kind else stmt)).scalars().all()
    on_plan = dict((await db.execute(select(OrgSubscription.plan_id, func.count()).group_by(OrgSubscription.plan_id))).all())
    with_addon = dict((await db.execute(select(OrgSubscriptionAddon.addon_id, func.count()).group_by(OrgSubscriptionAddon.addon_id))).all())
    return [item_dict(r, on_plan.get(r.id, 0) + with_addon.get(r.id, 0)) for r in rows]


class ItemIn(BaseModel):
    kind: Literal["plan", "addon", "credits"]
    key: str = Field(..., pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")
    name: str = Field(..., min_length=2, max_length=120)
    description: Optional[str] = Field(None, max_length=2000)
    price_cents: int = Field(0, ge=0)
    currency: str = Field("USD", pattern=r"^[A-Z]{3}$")
    public: bool = True
    sort: int = 0
    limits: Dict[str, Optional[int]] = Field(default_factory=dict)
    pages: Optional[int] = Field(None, gt=0)
    reason: str = Field(..., min_length=5, max_length=1000)


class ItemPatch(BaseModel):
    name: Optional[str] = Field(None, min_length=2, max_length=120)
    description: Optional[str] = Field(None, max_length=2000)
    price_cents: Optional[int] = Field(None, ge=0)
    currency: Optional[str] = Field(None, pattern=r"^[A-Z]{3}$")
    public: Optional[bool] = None
    sort: Optional[int] = None
    limits: Optional[Dict[str, Optional[int]]] = None
    pages: Optional[int] = Field(None, gt=0)
    reason: str = Field(..., min_length=5, max_length=1000)


def _check_item(kind: str, limits: Dict[str, Optional[int]], pages: Optional[int]) -> None:
    unknown = sorted(set(limits) - set(LIMITS))
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown limits: {', '.join(unknown)}. Use the ones in /sysadmin/billing/limits.")
    if any(v is not None and v < 0 for v in limits.values()):
        raise HTTPException(status_code=422, detail="A limit can't be below zero.")
    if kind == "addon" and any(v is None for v in limits.values()):
        raise HTTPException(status_code=422, detail="An add-on adds a number to each limit it names.")
    if kind == "credits" and (not pages or limits):
        raise HTTPException(status_code=422, detail="A credit pack has a number of pages and no limits.")
    if kind != "credits" and pages:
        raise HTTPException(status_code=422, detail="Only a credit pack has pages.")


@router.post("/catalog", status_code=201)
async def create_item(body: ItemIn, staff: Staff = Depends(CHANGE), db: AsyncSession = Depends(get_db_session)):
    """A new draft. Its version is the next for its key (a new key starts at 1)."""
    _check_item(body.kind, body.limits, body.pages)
    latest = (await db.execute(select(func.max(CatalogItem.version)).where(CatalogItem.kind == body.kind, CatalogItem.key == body.key))).scalar()
    item = CatalogItem(kind=body.kind, key=body.key, version=(latest or 0) + 1, name=body.name.strip(), description=body.description,
                       price_cents=body.price_cents, currency=body.currency, interval="once" if body.kind == "credits" else "month",
                       public=body.public, sort=body.sort, limits=body.limits, pages=body.pages, created_by=staff.user_id)
    db.add(item)
    await db.flush()
    record(db, staff, "billing.catalog_created", "catalog_item", item.id, reason=body.reason, details={"key": item.key, "version": item.version})
    await db.commit()
    return item_dict(item)


@router.post("/catalog/{item_id}/versions", status_code=201)
async def new_version(item_id: str, body: ItemPatch, staff: Staff = Depends(CHANGE), db: AsyncSession = Depends(get_db_session)):
    """A draft of the next version, starting from this one: how a published item changes."""
    base = await _item(db, item_id)
    latest = (await db.execute(select(func.max(CatalogItem.version)).where(CatalogItem.kind == base.kind, CatalogItem.key == base.key))).scalar()
    item = CatalogItem(kind=base.kind, key=base.key, version=latest + 1, name=base.name, description=base.description, price_cents=base.price_cents,
                       currency=base.currency, interval=base.interval, public=base.public, sort=base.sort, limits=dict(base.limits or {}),
                       pages=base.pages, created_by=staff.user_id)
    _apply_patch(item, body)
    db.add(item)
    await db.flush()
    record(db, staff, "billing.catalog_created", "catalog_item", item.id, reason=body.reason,
           details={"key": item.key, "version": item.version, "from": base.id})
    await db.commit()
    return item_dict(item)


def _apply_patch(item: CatalogItem, body: ItemPatch) -> None:
    for field in ("name", "description", "price_cents", "currency", "public", "sort", "limits", "pages"):
        value = getattr(body, field)
        if value is not None:
            setattr(item, field, value)
    _check_item(item.kind, item.limits or {}, item.pages)


@router.patch("/catalog/{item_id}")
async def update_item(item_id: str, body: ItemPatch, staff: Staff = Depends(CHANGE), db: AsyncSession = Depends(get_db_session)):
    """Changes a draft. A published version never changes: make a new version instead."""
    item = await _item(db, item_id)
    if item.status != "draft":
        raise HTTPException(status_code=409, detail="Only a draft can be changed. Make a new version of a published item.")
    _apply_patch(item, body)
    record(db, staff, "billing.catalog_changed", "catalog_item", item.id, reason=body.reason,
           details=body.model_dump(exclude={"reason"}, exclude_none=True))
    await db.commit()
    return item_dict(item)


class Reason(BaseModel):
    reason: str = Field(..., min_length=5, max_length=1000)


@router.post("/catalog/{item_id}/publish")
async def publish(item_id: str, body: Reason, staff: Staff = Depends(CHANGE), db: AsyncSession = Depends(get_db_session)):
    """Puts a draft on sale. From now on it never changes."""
    item = await _item(db, item_id)
    if item.status != "draft":
        raise HTTPException(status_code=409, detail="Only a draft can be published.")
    item.status, item.published_at = "published", datetime.now(timezone.utc)
    record(db, staff, "billing.catalog_published", "catalog_item", item.id, reason=body.reason, details={"key": item.key, "version": item.version})
    await db.commit()
    return item_dict(item)


@router.post("/catalog/{item_id}/archive")
async def archive(item_id: str, body: Reason, staff: Staff = Depends(CHANGE), db: AsyncSession = Depends(get_db_session)):
    """Takes it off sale. Organizations that have it keep it until they are moved."""
    item = await _item(db, item_id)
    if item.status == "archived":
        raise HTTPException(status_code=409, detail="It is already archived.")
    item.status, item.archived_at = "archived", datetime.now(timezone.utc)
    record(db, staff, "billing.catalog_archived", "catalog_item", item.id, reason=body.reason, details={"key": item.key, "version": item.version})
    await db.commit()
    return item_dict(item)


class MoveIn(BaseModel):
    to_id: str
    at_period_end: bool = True
    reason: str = Field(..., min_length=5, max_length=1000)


@router.post("/catalog/{item_id}/move")
async def move_organizations(item_id: str, body: MoveIn, staff: Staff = Depends(CHANGE), db: AsyncSession = Depends(get_db_session)):
    """Moves every organization on this plan version to another one, from their next period unless said otherwise."""
    source, target = await _item(db, item_id), await _item(db, body.to_id)
    if source.kind != "plan" or target.kind != "plan":
        raise HTTPException(status_code=409, detail="Organizations move between plans.")
    tenants = (await db.execute(select(OrgSubscription.tenant_id).where(OrgSubscription.plan_id == source.id))).scalars().all()
    try:
        for tenant_id in tenants:
            await billing.change_plan(db, tenant_id, target, at_period_end=body.at_period_end)
            tell_organization(db, staff, tenant_id, f"moved the organization to the {target.name} plan"
                              + (" from its next period" if body.at_period_end else ""), body.reason)
    except ValueError as exc:
        raise _refused(exc)
    record(db, staff, "billing.plan_moved", "catalog_item", source.id, reason=body.reason,
           details={"to": target.id, "organizations": len(tenants), "at_period_end": body.at_period_end})
    await db.commit()
    return {"moved": len(tenants)}


# ---------------------------------------------------------------------------
# Organizations
# ---------------------------------------------------------------------------

@router.get("/organizations", dependencies=[VIEW])
async def organizations(q: Optional[str] = None, limit: int = Query(100, le=500), db: AsyncSession = Depends(get_db_session)):
    """Every organization's plan, pages this period, credits and invoices waiting for payment."""
    stmt = select(Tenant).order_by(Tenant.name).limit(limit)
    if q:
        stmt = stmt.where(or_(Tenant.name.ilike(f"%{q}%"), Tenant.slug.ilike(f"%{q}%")))
    out = []
    now = datetime.now(timezone.utc)
    waiting = {t: (n, overdue) for t, n, overdue in (await db.execute(select(
        Invoice.tenant_id, func.count(), func.count().filter(Invoice.due_at < now)).where(
        Invoice.status.in_(invoices.PAYABLE)).group_by(Invoice.tenant_id))).all()}
    drafts = dict((await db.execute(select(Invoice.tenant_id, func.count()).where(Invoice.status == "draft").group_by(Invoice.tenant_id))).all())
    for tenant in (await db.execute(stmt)).scalars().all():
        s = await billing.summary(db, tenant.id)
        sub = await db.get(OrgSubscription, tenant.id)
        out.append({"id": tenant.id, "name": tenant.name, "slug": tenant.slug, "status": tenant.status, "plan": s["plan"], "plan_key": s["plan_key"],
                    "subscription_status": sub.status, "trial_ends_at": sub.trial_ends_at, "paid_until": sub.paid_until,
                    "pages_used": s["pages_used"], "pages_limit": s["pages_limit"], "credits": s["credits"], "period_end": s["period_end"],
                    "invoices_waiting": waiting.get(tenant.id, (0, 0))[0], "invoices_overdue": waiting.get(tenant.id, (0, 0))[1],
                    "drafts": drafts.get(tenant.id, 0)})
    await db.commit()  # subscriptions created or brought up to date while reading
    return out


def _payment_dict(p: BillingPayment, number: Optional[str] = None) -> Dict[str, Any]:
    return {"id": p.id, "invoice_id": p.invoice_id, "invoice": number, "method": p.method, "amount_cents": p.amount_cents, "currency": p.currency,
            "item_id": p.item_id, "quantity": p.quantity, "received_on": p.received_on, "reference": p.reference, "note": p.note,
            "recorded_by_name": p.recorded_by_name, "created_at": p.created_at}


@router.get("/organizations/{tenant_id}", dependencies=[VIEW])
async def organization(tenant_id: str, db: AsyncSession = Depends(get_db_session)):
    """One organization's billing: the subscription, where each limit comes from, usage, credits, billing details, invoices and payments."""
    tenant = await _tenant(db, tenant_id)
    s = await billing.standing(db, tenant_id)
    sub = s.subscription
    pending = await db.get(CatalogItem, sub.pending_plan_id) if sub.pending_plan_id else None
    addons = (await db.execute(select(OrgSubscriptionAddon, CatalogItem).join(CatalogItem, CatalogItem.id == OrgSubscriptionAddon.addon_id)
                               .where(OrgSubscriptionAddon.tenant_id == tenant_id))).all()
    overrides = (await db.execute(select(OrgLimitOverride).where(OrgLimitOverride.tenant_id == tenant_id)
                                  .order_by(OrgLimitOverride.created_at.desc()))).scalars().all()
    pages = await billing.pages_by_workspace(db, tenant_id, sub.period_start)
    named = await names(db, [tenant_id], [o.template_id for o in overrides] + list(pages))
    payments = (await db.execute(select(BillingPayment, Invoice.number).outerjoin(Invoice, Invoice.id == BillingPayment.invoice_id)
                                 .where(BillingPayment.tenant_id == tenant_id).order_by(BillingPayment.created_at.desc()).limit(50))).all()
    issued = (await db.execute(select(Invoice).where(Invoice.tenant_id == tenant_id).order_by(Invoice.created_at.desc()).limit(50))).scalars().all()
    profile = await parties.profile(db, tenant_id)
    summary = await billing.summary(db, tenant_id)
    result = {
        "organization": {"id": tenant.id, "name": tenant.name, "slug": tenant.slug, "status": tenant.status},
        "workspaces": [{"id": w.id, "name": w.name} for w in (await db.execute(
            select(Workspace).where(Workspace.tenant_id == tenant_id).order_by(Workspace.name))).scalars().all()],
        "subscription": {"plan": item_dict(s.plan), "pending_plan": item_dict(pending) if pending else None, "status": sub.status,
                         "period_start": sub.period_start, "period_end": sub.period_end, "anchor_day": sub.anchor_day,
                         "trial_ends_at": sub.trial_ends_at, "paid_until": sub.paid_until},
        "limits": {"plan": s.plan.limits or {}, "effective": s.limits,
                   "addons": [{"id": a.id, "addon": item_dict(i), "quantity": a.quantity, "added_at": a.added_at} for a, i in addons],
                   "overrides": [{"id": o.id, "key": o.key, "value": o.value, "template_id": o.template_id,
                                  "workspace": named["workspaces"].get(o.template_id, {}).get("name") if o.template_id else None,
                                  "reason": o.reason, "set_by": o.set_by, "created_at": o.created_at, "expires_at": o.expires_at}
                                 for o in overrides]},
        "usage": {"pages_used": summary["pages_used"], "pages_limit": summary["pages_limit"],
                  "by_workspace": [{"id": t, "name": named["workspaces"].get(t, {}).get("name"), "pages": n} for t, n in pages.items()]},
        "credits": {"balance": summary["credits"], "history": [
            {"id": e.id, "delta": e.delta, "kind": e.kind, "reason": e.reason, "ref": e.ref, "created_by": e.created_by, "created_at": e.created_at}
            for e in await credits.history(db, tenant_id)]},
        "payments": [_payment_dict(p, number) for p, number in payments],
        "invoices": [invoices.describe(i) for i in issued],
        "billing_details": parties.buyer_snapshot(profile) if profile else None,
        "billing_details_missing": parties.profile_missing(profile),
    }
    await db.commit()
    return result


class PlanIn(BaseModel):
    plan_id: str
    at_period_end: bool = False
    reason: str = Field(..., min_length=5, max_length=1000)


@router.post("/organizations/{tenant_id}/plan")
async def change_plan(tenant_id: str, body: PlanIn, staff: Staff = Depends(CHANGE), db: AsyncSession = Depends(get_db_session)):
    await _tenant(db, tenant_id)
    plan = await _item(db, body.plan_id)
    try:
        await billing.change_plan(db, tenant_id, plan, at_period_end=body.at_period_end)
    except ValueError as exc:
        raise _refused(exc)
    text = f"moved the organization to the {plan.name} plan" + (" from its next period" if body.at_period_end else "")
    record(db, staff, "billing.plan_changed", "organization", tenant_id, tenant_id=tenant_id, reason=body.reason,
           details={"plan_id": plan.id, "at_period_end": body.at_period_end})
    tell_organization(db, staff, tenant_id, text, body.reason)
    await db.commit()
    return {"done": text}


class AddonIn(BaseModel):
    addon_id: str
    quantity: int = Field(..., ge=0, le=10000)
    reason: str = Field(..., min_length=5, max_length=1000)


@router.post("/organizations/{tenant_id}/addons")
async def set_addon(tenant_id: str, body: AddonIn, staff: Staff = Depends(CHANGE), db: AsyncSession = Depends(get_db_session)):
    """How many of an add-on the organization has from now on (0 removes it)."""
    await _tenant(db, tenant_id)
    addon = await _item(db, body.addon_id)
    try:
        await billing.set_addon(db, tenant_id, addon, body.quantity, staff.user_id)
    except ValueError as exc:
        raise _refused(exc)
    text = f"set {addon.name} to {body.quantity}" if body.quantity else f"removed {addon.name}"
    record(db, staff, "billing.addon_set", "organization", tenant_id, tenant_id=tenant_id, reason=body.reason,
           details={"addon_id": addon.id, "quantity": body.quantity})
    tell_organization(db, staff, tenant_id, text, body.reason)
    await db.commit()
    return {"done": text}


class OverrideIn(BaseModel):
    key: str
    value: Optional[int] = Field(None, ge=0, description="null: unlimited")
    template_id: Optional[str] = Field(None, description="One workspace (a workspace limit only)")
    expires_at: Optional[datetime] = None
    reason: str = Field(..., min_length=5, max_length=1000)


@router.post("/organizations/{tenant_id}/overrides", status_code=201)
async def set_override(tenant_id: str, body: OverrideIn, staff: Staff = Depends(CHANGE), db: AsyncSession = Depends(get_db_session)):
    await _tenant(db, tenant_id)
    if body.template_id and not (await names(db, workspace_ids=[body.template_id]))["workspaces"].get(body.template_id, {}).get("tenant_id") == tenant_id:
        raise HTTPException(status_code=404, detail="That workspace isn't in this organization.")
    try:
        row = await billing.set_override(db, tenant_id=tenant_id, key=body.key, value=body.value, reason=body.reason, set_by=staff.name,
                                         template_id=body.template_id, expires_at=body.expires_at)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    shown = "unlimited" if body.value is None else f"{body.value:,}"
    text = f"set the limit for {LIMITS[body.key].label.lower()} to {shown}" + (" for one workspace" if body.template_id else "")
    record(db, staff, "billing.override_set", "organization", tenant_id, tenant_id=tenant_id, reason=body.reason,
           details=body.model_dump(exclude={"reason"}, mode="json"))
    tell_organization(db, staff, tenant_id, text, body.reason)
    await db.commit()
    return {"id": row.id, "done": text}


@router.post("/organizations/{tenant_id}/overrides/{override_id}/remove")
async def remove_override(tenant_id: str, override_id: str, body: Reason, staff: Staff = Depends(CHANGE), db: AsyncSession = Depends(get_db_session)):
    row = await db.get(OrgLimitOverride, override_id)
    if row is None or row.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="No such override.")
    text = f"removed the custom limit for {LIMITS[row.key].label.lower() if row.key in LIMITS else row.key}"
    await db.delete(row)
    record(db, staff, "billing.override_removed", "organization", tenant_id, tenant_id=tenant_id, reason=body.reason, details={"key": row.key})
    tell_organization(db, staff, tenant_id, text, body.reason)
    await db.commit()
    return {"done": text}


class TrialIn(BaseModel):
    plan_id: str
    days: int = Field(..., ge=1, le=90)
    reason: str = Field(..., min_length=5, max_length=1000)


@router.post("/organizations/{tenant_id}/trial")
async def trial(tenant_id: str, body: TrialIn, staff: Staff = Depends(CHANGE), db: AsyncSession = Depends(get_db_session)):
    """Gives a plan for some days; unless it is paid for by then, the organization goes back to Free."""
    await _tenant(db, tenant_id)
    plan = await _item(db, body.plan_id)
    try:
        await billing.start_trial(db, tenant_id, plan, body.days)
    except ValueError as exc:
        raise _refused(exc)
    text = f"started a {body.days}-day trial of the {plan.name} plan"
    record(db, staff, "billing.trial_started", "organization", tenant_id, tenant_id=tenant_id, reason=body.reason,
           details={"plan_id": plan.id, "days": body.days})
    tell_organization(db, staff, tenant_id, text, body.reason)
    await db.commit()
    return {"done": text}


class CreditsIn(BaseModel):
    pages: int = Field(..., description="Pages to add (or, negative, to take back)")
    kind: Literal["grant", "refund", "adjustment"] = "grant"
    reason: str = Field(..., min_length=5, max_length=1000)


@router.post("/organizations/{tenant_id}/credits")
async def change_credits(tenant_id: str, body: CreditsIn, staff: Staff = Depends(CHANGE), db: AsyncSession = Depends(get_db_session)):
    """Gives credits (a grant, a refund for pages the platform got wrong) or corrects the balance."""
    await _tenant(db, tenant_id)
    try:
        await credits.add(db, tenant_id=tenant_id, delta=body.pages, kind=body.kind, created_by=staff.name, reason=body.reason)
    except ValueError as exc:
        raise _refused(exc)
    text = f"{'added' if body.pages > 0 else 'took back'} {abs(body.pages):,} prepaid pages ({body.kind})"
    record(db, staff, "billing.credits_changed", "organization", tenant_id, tenant_id=tenant_id, reason=body.reason,
           details={"pages": body.pages, "kind": body.kind})
    tell_organization(db, staff, tenant_id, text, body.reason)
    await db.commit()
    return {"done": text, "balance": await credits.balance(db, tenant_id)}


# ---------------------------------------------------------------------------
# Syruss's billing details: the seller, its bank accounts, tax rates
# ---------------------------------------------------------------------------

def _settings_dict(s) -> Dict[str, Any]:
    return {**parties.seller_snapshot(s), "invoice_prefix": s.invoice_prefix, "payment_terms_days": s.payment_terms_days,
            "renewal_lead_days": s.renewal_lead_days, "grace_days": s.grace_days, "reminder_days": s.reminder_days,
            "updated_at": s.updated_at, "missing": parties.seller_missing(s)}


def _rate_dict(r: TaxRate) -> Dict[str, Any]:
    return {"id": r.id, "country": r.country, "name": r.name, "rate_bp": r.rate_bp, "reverse_charge_with_tax_id": r.reverse_charge_with_tax_id,
            "active": r.active, "created_at": r.created_at}


@router.get("/settings", dependencies=[VIEW])
async def get_settings(db: AsyncSession = Depends(get_db_session)):
    """Who invoices are from, how they are numbered and paid, the bank accounts and the tax rates."""
    s = await parties.seller(db)
    accounts = (await db.execute(select(BankAccount).order_by(BankAccount.active.desc(), BankAccount.created_at))).scalars().all()
    rates = (await db.execute(select(TaxRate).order_by(TaxRate.active.desc(), TaxRate.country))).scalars().all()
    await db.commit()  # the settings row may have just been made
    return {"seller": _settings_dict(s), "bank_accounts": [parties.bank_dict(a) for a in accounts], "tax_rates": [_rate_dict(r) for r in rates],
            "methods": [m.describe() for m in enabled()]}


class SettingsIn(BaseModel):
    legal_name: str = Field(..., min_length=2, max_length=200)
    address: str = Field(..., min_length=2, max_length=1000)
    country: str = Field(..., pattern=r"^[A-Za-z]{2}$")
    tax_id: Optional[str] = Field(None, max_length=64)
    email: str = Field(..., max_length=255, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    invoice_prefix: str = Field("SYR", pattern=r"^[A-Z0-9]{2,12}$")
    payment_terms_days: int = Field(14, ge=0, le=120)
    renewal_lead_days: int = Field(7, ge=1, le=30)
    grace_days: int = Field(7, ge=0, le=60)
    reminder_days: List[int] = Field(default_factory=lambda: [3, 0, -3], max_length=6)
    footer: Optional[str] = Field(None, max_length=1000)
    reason: str = Field(..., min_length=5, max_length=1000)


@router.put("/settings")
async def put_settings(body: SettingsIn, staff: Staff = Depends(CHANGE), db: AsyncSession = Depends(get_db_session)):
    """Changes Syruss's billing details. Invoices already issued keep what they were issued with."""
    s = await parties.seller(db)
    for field, value in body.model_dump(exclude={"reason"}).items():
        setattr(s, field, value.strip() if isinstance(value, str) else value)
    s.country, s.updated_by = s.country.upper(), staff.user_id
    record(db, staff, "billing.settings_changed", "billing_settings", "1", reason=body.reason, details=body.model_dump(exclude={"reason"}))
    await db.commit()
    return _settings_dict(s)


class BankAccountIn(BaseModel):
    bank: str = Field(..., min_length=2, max_length=120)
    account_name: str = Field(..., min_length=2, max_length=200)
    account_number: str = Field(..., min_length=2, max_length=64)
    branch: Optional[str] = Field(None, max_length=120)
    swift: Optional[str] = Field(None, max_length=16)
    currency: str = Field(..., pattern=r"^[A-Z]{3}$")
    instructions: Optional[str] = Field(None, max_length=1000)
    reason: str = Field(..., min_length=5, max_length=1000)


@router.post("/bank-accounts", status_code=201)
async def add_bank_account(body: BankAccountIn, staff: Staff = Depends(CHANGE), db: AsyncSession = Depends(get_db_session)):
    """An account customers can pay into; invoices issued from now on in its currency show it."""
    account = BankAccount(created_by=staff.user_id, **body.model_dump(exclude={"reason"}))
    db.add(account)
    await db.flush()
    record(db, staff, "billing.bank_account_added", "bank_account", account.id, reason=body.reason,
           details={"bank": account.bank, "currency": account.currency})
    await db.commit()
    return parties.bank_dict(account)


class ActiveIn(BaseModel):
    active: bool
    reason: str = Field(..., min_length=5, max_length=1000)


@router.post("/bank-accounts/{account_id}/active")
async def set_bank_account_active(account_id: str, body: ActiveIn, staff: Staff = Depends(CHANGE), db: AsyncSession = Depends(get_db_session)):
    """Stops (or starts again) showing an account on new invoices. Accounts are never deleted: old invoices name them."""
    account = await db.get(BankAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="No such bank account.")
    account.active = body.active
    record(db, staff, "billing.bank_account_changed", "bank_account", account.id, reason=body.reason, details={"active": body.active})
    await db.commit()
    return parties.bank_dict(account)


class TaxRateIn(BaseModel):
    country: str = Field(..., pattern=r"^([A-Za-z]{2}|\*)$", description="ISO alpha-2, or * for every other country")
    name: str = Field(..., min_length=2, max_length=60)
    rate_bp: int = Field(..., ge=0, le=10000, description="Basis points: 1800 is 18%")
    reverse_charge_with_tax_id: bool = False
    reason: str = Field(..., min_length=5, max_length=1000)


@router.post("/tax-rates", status_code=201)
async def add_tax_rate(body: TaxRateIn, staff: Staff = Depends(CHANGE), db: AsyncSession = Depends(get_db_session)):
    """A tax rate for buyers in one country (or the default); it replaces that country's active rate for new invoices."""
    country = body.country.upper()
    for old in (await db.execute(select(TaxRate).where(TaxRate.country == country, TaxRate.active.is_(True)))).scalars().all():
        old.active = False
    rate = TaxRate(country=country, name=body.name.strip(), rate_bp=body.rate_bp, reverse_charge_with_tax_id=body.reverse_charge_with_tax_id,
                   created_by=staff.user_id)
    db.add(rate)
    await db.flush()
    record(db, staff, "billing.tax_rate_set", "tax_rate", rate.id, reason=body.reason, details=body.model_dump(exclude={"reason"}))
    await db.commit()
    return _rate_dict(rate)


@router.post("/tax-rates/{rate_id}/active")
async def set_tax_rate_active(rate_id: str, body: ActiveIn, staff: Staff = Depends(CHANGE), db: AsyncSession = Depends(get_db_session)):
    rate = await db.get(TaxRate, rate_id)
    if rate is None:
        raise HTTPException(status_code=404, detail="No such tax rate.")
    if body.active:
        for other in (await db.execute(select(TaxRate).where(TaxRate.country == rate.country, TaxRate.active.is_(True), TaxRate.id != rate.id))).scalars().all():
            other.active = False
    rate.active = body.active
    record(db, staff, "billing.tax_rate_changed", "tax_rate", rate.id, reason=body.reason, details={"active": body.active})
    await db.commit()
    return _rate_dict(rate)


# ---------------------------------------------------------------------------
# Invoices
# ---------------------------------------------------------------------------

async def _invoice(db: AsyncSession, invoice_id: str) -> Invoice:
    invoice = await db.get(Invoice, invoice_id)
    if invoice is None:
        raise HTTPException(status_code=404, detail="No such invoice.")
    return invoice


@router.get("/invoices", dependencies=[VIEW])
async def list_invoices(status: Optional[str] = None, tenant_id: Optional[str] = None, overdue: bool = False, q: Optional[str] = None,
                        limit: int = Query(100, le=500), db: AsyncSession = Depends(get_db_session)):
    """Invoices, newest first: by status, organization, overdue, or a number to find."""
    stmt = select(Invoice).order_by(Invoice.created_at.desc()).limit(limit)
    if status:
        stmt = stmt.where(Invoice.status == status)
    if tenant_id:
        stmt = stmt.where(Invoice.tenant_id == tenant_id)
    if overdue:
        stmt = stmt.where(Invoice.status == "open", Invoice.due_at < datetime.now(timezone.utc))
    if q:
        stmt = stmt.where(Invoice.number.ilike(f"%{q.strip()}%"))
    rows = (await db.execute(stmt)).scalars().all()
    named = await names(db, [i.tenant_id for i in rows])
    return [{**invoices.describe(i), "organization": named["organizations"].get(i.tenant_id)} for i in rows]


@router.get("/invoices/{invoice_id}", dependencies=[VIEW])
async def get_invoice(invoice_id: str, db: AsyncSession = Depends(get_db_session)):
    invoice = await _invoice(db, invoice_id)
    named = await names(db, [invoice.tenant_id])
    out = invoices.describe(invoice, await invoices.lines_of(db, invoice.id), await invoices.payments_of(db, invoice.id))
    for p in out["payments"]:
        p["files"] = [payments.file_dict(f) for f in await proofs.files_of(db, payment_id=p["id"])]
    sent = (await db.execute(select(PaymentSubmission).where(PaymentSubmission.invoice_id == invoice.id)
                             .order_by(PaymentSubmission.created_at.desc()))).scalars().all()
    return {**out, "organization": named["organizations"].get(invoice.tenant_id),
            "submissions": [payments.describe(s, await proofs.files_of(db, submission_id=s.id)) for s in sent],
            "billing_details_missing": parties.profile_missing(await parties.profile(db, invoice.tenant_id))}


@router.get("/invoices/{invoice_id}/{which}", dependencies=[VIEW])
async def invoice_file(invoice_id: str, which: Literal["pdf", "receipt"], db: AsyncSession = Depends(get_db_session)):
    invoice = await _invoice(db, invoice_id)
    data = await invoices.file_of(invoice, which)
    if data is None:
        raise HTTPException(status_code=404, detail="It has no receipt until it is paid." if which == "receipt" else "It has no PDF until it is issued.")
    return Response(content=data, media_type="application/pdf", headers={
        "Content-Disposition": f'inline; filename="{"Receipt" if which == "receipt" else "Invoice"} {invoice.number}.pdf"',
        "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})


class LineIn(BaseModel):
    item_id: Optional[str] = Field(None, description="A catalog item; or give description and unit_cents")
    description: Optional[str] = Field(None, max_length=300)
    unit_cents: Optional[int] = Field(None, ge=0)
    quantity: int = Field(1, ge=1, le=10000)


class InvoiceIn(BaseModel):
    tenant_id: str
    lines: List[LineIn] = Field(..., min_length=1, max_length=50)
    currency: Optional[str] = Field(None, pattern=r"^[A-Z]{3}$", description="For an invoice of written lines only")
    note: Optional[str] = Field(None, max_length=2000)
    issue: bool = Field(False, description="Issue it at once")
    reason: str = Field(..., min_length=5, max_length=1000)


async def _issue(db: AsyncSession, invoice: Invoice) -> None:
    try:
        await invoices.issue(db, invoice)
    except parties.NotReady as exc:
        raise HTTPException(status_code=409, detail={"code": "billing_details_missing" if exc.who == "buyer" else "seller_not_ready",
                                                     "message": str(exc), "missing": exc.missing})
    except ValueError as exc:
        raise _refused(exc)


async def _tell_customer(invoice: Invoice) -> None:
    await (notices.invoice_issued(invoice.id) if invoice.status == "open" else notices.invoice_paid(invoice.id, None))


@router.post("/invoices", status_code=201)
async def create_invoice(body: InvoiceIn, staff: Staff = Depends(CHANGE), db: AsyncSession = Depends(get_db_session)):
    """An invoice for a custom deal: catalog items and lines staff write. A draft, unless issue is set."""
    await _tenant(db, body.tenant_id)
    lines = []
    for line in body.lines:
        if line.item_id:
            item = await _item(db, line.item_id)
            if item.status == "draft":
                raise HTTPException(status_code=409, detail=f"{item.name} isn't published yet.")
            lines.append(invoices.item_line(item, line.quantity))
        elif line.description and line.unit_cents is not None:
            lines.append(invoices.Line(line.description.strip(), line.quantity, line.unit_cents))
        else:
            raise HTTPException(status_code=422, detail="Each line is a catalog item, or a description with a price.")
    try:
        invoice = await invoices.draft(db, body.tenant_id, lines, kind="manual", created_by=staff.user_id, note=body.note)
    except ValueError as exc:
        raise _refused(exc)
    if body.currency and not any(line.item for line in lines):
        invoice.currency = body.currency
    if body.issue:
        await _issue(db, invoice)
    text = f"issued invoice {invoice.number} ({money(invoice.total_cents, invoice.currency)})" if body.issue else "drafted an invoice"
    record(db, staff, "billing.invoice_created", "invoice", invoice.id, tenant_id=body.tenant_id, reason=body.reason,
           details={"issued": body.issue, "number": invoice.number, "total_cents": invoice.total_cents})
    if body.issue:
        tell_organization(db, staff, body.tenant_id, text, body.reason)
    await db.commit()
    if body.issue:
        await _tell_customer(invoice)
    return invoices.describe(invoice, await invoices.lines_of(db, invoice.id))


class Reasoned(BaseModel):
    reason: str = Field(..., min_length=5, max_length=1000)


@router.post("/invoices/{invoice_id}/issue")
async def issue_invoice(invoice_id: str, body: Reasoned, staff: Staff = Depends(CHANGE), db: AsyncSession = Depends(get_db_session)):
    """Issues a draft: numbered, fixed, printed and sent to the organization's billing address."""
    invoice = await _invoice(db, invoice_id)
    await _issue(db, invoice)
    text = f"issued invoice {invoice.number} ({money(invoice.total_cents, invoice.currency)})"
    record(db, staff, "billing.invoice_issued", "invoice", invoice.id, tenant_id=invoice.tenant_id, reason=body.reason, details={"number": invoice.number})
    tell_organization(db, staff, invoice.tenant_id, text, body.reason)
    await db.commit()
    await _tell_customer(invoice)
    return invoices.describe(invoice)


@router.post("/invoices/{invoice_id}/discard")
async def discard_invoice(invoice_id: str, body: Reasoned, staff: Staff = Depends(CHANGE), db: AsyncSession = Depends(get_db_session)):
    """Throws a draft away."""
    invoice = await _invoice(db, invoice_id)
    try:
        await invoices.discard(db, invoice)
    except ValueError as exc:
        raise _refused(exc)
    record(db, staff, "billing.invoice_discarded", "invoice", invoice_id, tenant_id=invoice.tenant_id, reason=body.reason)
    await db.commit()
    return {"discarded": invoice_id}


@router.post("/invoices/{invoice_id}/void")
async def void_invoice(invoice_id: str, body: Reasoned, staff: Staff = Depends(CHANGE), db: AsyncSession = Depends(get_db_session)):
    """Cancels an issued invoice nothing was paid on. It stays on record with its number."""
    invoice = await _invoice(db, invoice_id)
    try:
        await invoices.void(db, invoice, reason=body.reason)
    except ValueError as exc:
        raise _refused(exc)
    record(db, staff, "billing.invoice_voided", "invoice", invoice.id, tenant_id=invoice.tenant_id, reason=body.reason, details={"number": invoice.number})
    tell_organization(db, staff, invoice.tenant_id, f"voided invoice {invoice.number}", body.reason)
    await db.commit()
    return invoices.describe(invoice)


@router.post("/invoices/{invoice_id}/switch-on")
async def switch_on_early(invoice_id: str, body: Reasoned, staff: Staff = Depends(CHANGE), db: AsyncSession = Depends(get_db_session)):
    """Switches on what an unpaid invoice sells now, for an organization trusted to pay it; it stays waiting for payment."""
    invoice = await _invoice(db, invoice_id)
    try:
        done = await invoices.switch_on_early(db, invoice, by_id=staff.user_id, by_name=staff.name, reason=body.reason)
    except ValueError as exc:
        raise _refused(exc)
    text = f"switched on invoice {invoice.number} before it was paid ({done})"
    record(db, staff, "billing.invoice_switched_on", "invoice", invoice.id, tenant_id=invoice.tenant_id, reason=body.reason, details={"number": invoice.number})
    tell_organization(db, staff, invoice.tenant_id, text, body.reason)
    await db.commit()
    return {**invoices.describe(invoice), "done": done}


class InvoicePaymentIn(BaseModel):
    method: str = Field(..., description="One of the ways to pay this deployment offers")
    amount_cents: int = Field(..., gt=0)
    received_on: Optional[date] = None
    reference: Optional[str] = Field(None, max_length=200)
    note: Optional[str] = Field(None, max_length=2000)
    reason: str = Field(..., min_length=5, max_length=1000)


@router.post("/invoices/{invoice_id}/payments", status_code=201)
async def record_payment(invoice_id: str, body: InvoicePaymentIn, staff: Staff = Depends(CHANGE), db: AsyncSession = Depends(get_db_session)):
    """Money received for an invoice (cash, a cheque, a transfer seen on the statement). Once it is covered, it is switched on."""
    invoice = await _invoice(db, invoice_id)
    try:
        payment, done = await invoices.record_payment(db, invoice, method=body.method, amount_cents=body.amount_cents, by_id=staff.user_id,
                                                      by_name=staff.name, received_on=body.received_on, reference=body.reference, note=body.note)
    except ValueError as exc:
        raise _refused(exc)
    text = f"recorded a payment of {money(body.amount_cents, invoice.currency)} for invoice {invoice.number}"
    if invoice.status == "paid":
        text += ", which is now paid" + (f": {done}" if done else "")
    record(db, staff, "billing.payment_recorded", "invoice", invoice.id, tenant_id=invoice.tenant_id, reason=body.reason,
           details={"payment_id": payment.id, "amount_cents": body.amount_cents, "method": body.method, "number": invoice.number})
    tell_organization(db, staff, invoice.tenant_id, text, body.reason)
    await db.commit()
    if invoice.status == "paid":
        await notices.invoice_paid(invoice.id, done)
    return {**_payment_dict(payment, invoice.number), "invoice_status": invoice.status, "done": text}


@router.post("/payments/{payment_id}/files", status_code=201)
async def attach_payment_files(payment_id: str, reason: str = Form(..., min_length=5, max_length=1000), files: List[UploadFile] = File(...),
                               staff: Staff = Depends(CHANGE), db: AsyncSession = Depends(get_db_session)):
    """Keeps a receipt staff issued (or a scan of the cheque) with a payment they recorded."""
    payment = await db.get(BillingPayment, payment_id)
    if payment is None:
        raise HTTPException(status_code=404, detail="No such payment.")
    if len(files) > proofs.MAX_FILES:
        raise HTTPException(status_code=422, detail=f"Attach up to {proofs.MAX_FILES} files.")
    uploads = [(f.filename, await f.read(proofs.MAX_BYTES + 1)) for f in files]
    try:
        for _, data in uploads:
            proofs.check(data)
        kept = [await proofs.store(db, tenant_id=payment.tenant_id, filename=name, data=data, uploaded_by=staff.user_id, payment_id=payment.id)
                for name, data in uploads]
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    record(db, staff, "billing.payment_files_added", "billing_payment", payment.id, tenant_id=payment.tenant_id, reason=reason,
           details={"files": [f.filename for f in kept]})
    await db.commit()
    return [payments.file_dict(f) for f in kept]


@router.get("/payments/{payment_id}/files/{file_id}", dependencies=[VIEW])
async def payment_file(payment_id: str, file_id: str, db: AsyncSession = Depends(get_db_session)):
    f = await db.get(PaymentFile, file_id)
    if f is None or f.payment_id != payment_id:
        raise HTTPException(status_code=404, detail="No such file.")
    return file_response(f, await proofs.read(f))


# ---------------------------------------------------------------------------
# Payments customers sent proof of, to check
# ---------------------------------------------------------------------------

async def _submission_view(db: AsyncSession, s: PaymentSubmission, invoice: Invoice, organization: Optional[dict]) -> Dict[str, Any]:
    due = invoice.total_cents - invoice.amount_paid_cents
    return {**payments.describe(s, await proofs.files_of(db, submission_id=s.id)), "invoice": invoices.describe(invoice),
            "organization": organization, "warnings": await proofs.warnings(db, s, due) if s.status == "submitted" else []}


@router.get("/submissions", dependencies=[VIEW])
async def list_submissions(status: str = "submitted", tenant_id: Optional[str] = None, limit: int = Query(100, le=500),
                           db: AsyncSession = Depends(get_db_session)):
    """Payments customers sent proof of: those waiting to be checked (oldest first), or those already checked."""
    stmt = select(PaymentSubmission, Invoice).join(Invoice, Invoice.id == PaymentSubmission.invoice_id).where(PaymentSubmission.status == status)
    if tenant_id:
        stmt = stmt.where(PaymentSubmission.tenant_id == tenant_id)
    order = PaymentSubmission.created_at if status == "submitted" else PaymentSubmission.created_at.desc()
    rows = (await db.execute(stmt.order_by(order).limit(limit))).all()
    named = await names(db, [s.tenant_id for s, _ in rows])
    return [await _submission_view(db, s, i, named["organizations"].get(s.tenant_id)) for s, i in rows]


async def _submission(db: AsyncSession, submission_id: str) -> PaymentSubmission:
    s = await db.get(PaymentSubmission, submission_id)
    if s is None:
        raise HTTPException(status_code=404, detail="No such payment.")
    return s


@router.get("/submissions/{submission_id}", dependencies=[VIEW])
async def get_submission(submission_id: str, db: AsyncSession = Depends(get_db_session)):
    s = await _submission(db, submission_id)
    named = await names(db, [s.tenant_id])
    return await _submission_view(db, s, await db.get(Invoice, s.invoice_id), named["organizations"].get(s.tenant_id))


@router.get("/submissions/{submission_id}/files/{file_id}", dependencies=[VIEW])
async def submission_file(submission_id: str, file_id: str, db: AsyncSession = Depends(get_db_session)):
    """The slip itself, shown in the browser and unable to run anything."""
    f = await db.get(PaymentFile, file_id)
    if f is None or f.submission_id != submission_id:
        raise HTTPException(status_code=404, detail="No such file.")
    return file_response(f, await proofs.read(f))


class ApproveIn(BaseModel):
    amount_cents: Optional[int] = Field(None, gt=0, description="What actually arrived (default: what the customer said)")
    received_on: Optional[date] = None
    overpaid_ok: bool = Field(False, description="Record it although it is more than is due")
    reason: str = Field(..., min_length=5, max_length=1000)


@router.post("/submissions/{submission_id}/approve")
async def approve_submission(submission_id: str, body: ApproveIn, staff: Staff = Depends(CHANGE), db: AsyncSession = Depends(get_db_session)):
    """The money is on the account: records the payment for what arrived; the invoice is paid once it is covered."""
    s = await _submission(db, submission_id)
    try:
        payment, done, invoice = await payments.approve(db, s, by_id=staff.user_id, by_name=staff.name, amount_cents=body.amount_cents,
                                                        received_on=body.received_on, overpaid_ok=body.overpaid_ok)
    except payments.NeedsConfirmation as exc:
        raise HTTPException(status_code=409, detail={"code": "overpayment", "message": str(exc)})
    except ValueError as exc:
        raise _refused(exc)
    text = f"confirmed a bank transfer of {money(payment.amount_cents, payment.currency)} for invoice {invoice.number}"
    if invoice.status == "paid":
        text += ", which is now paid" + (f": {done}" if done else "")
    record(db, staff, "billing.payment_approved", "payment_submission", s.id, tenant_id=s.tenant_id, reason=body.reason,
           details={"payment_id": payment.id, "amount_cents": payment.amount_cents, "number": invoice.number})
    tell_organization(db, staff, s.tenant_id, text, body.reason)
    await db.commit()
    if invoice.status == "paid":
        await notices.invoice_paid(invoice.id, done)
    return {"status": "approved", "payment_id": payment.id, "invoice_status": invoice.status, "done": text}


class RejectIn(BaseModel):
    reason: str = Field(..., min_length=5, max_length=1000, description="Shown to the customer: what didn't match")


@router.post("/submissions/{submission_id}/reject")
async def reject_submission(submission_id: str, body: RejectIn, staff: Staff = Depends(CHANGE), db: AsyncSession = Depends(get_db_session)):
    """The money isn't on the account, or the slip doesn't show it. The customer sees the reason and can send it again."""
    s = await _submission(db, submission_id)
    try:
        await payments.reject(db, s, by_id=staff.user_id, by_name=staff.name, reason=body.reason)
    except ValueError as exc:
        raise _refused(exc)
    invoice = await db.get(Invoice, s.invoice_id)
    record(db, staff, "billing.payment_rejected", "payment_submission", s.id, tenant_id=s.tenant_id, reason=body.reason,
           details={"number": invoice.number, "amount_cents": s.amount_cents})
    tell_organization(db, staff, s.tenant_id, f"couldn't confirm the payment sent for invoice {invoice.number}", body.reason)
    await db.commit()
    await notices.payment_rejected(s.id)
    return {"status": "rejected"}


@router.get("/expiring", dependencies=[VIEW])
async def expiring(days: int = Query(7, ge=1, le=60), db: AsyncSession = Depends(get_db_session)) -> List[Dict[str, Any]]:
    """Trials that end soon, and paid plans whose payment doesn't cover the current period: who to follow up with."""
    soon = datetime.now(timezone.utc) + timedelta(days=days)
    rows = (await db.execute(select(OrgSubscription, CatalogItem).join(CatalogItem, CatalogItem.id == OrgSubscription.plan_id).where(or_(
        (OrgSubscription.status == "trialing") & (OrgSubscription.trial_ends_at <= soon),
        (CatalogItem.price_cents > 0) & (OrgSubscription.status == "active")
        & or_(OrgSubscription.paid_until.is_(None), OrgSubscription.paid_until < OrgSubscription.period_end),
    )))).all()
    named = await names(db, [s.tenant_id for s, _ in rows])
    return [{"tenant_id": s.tenant_id, "organization": named["organizations"].get(s.tenant_id), "plan": p.name, "status": s.status,
             "trial_ends_at": s.trial_ends_at, "paid_until": s.paid_until, "period_end": s.period_end} for s, p in rows]
