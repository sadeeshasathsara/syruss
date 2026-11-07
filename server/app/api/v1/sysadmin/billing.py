"""
Billing, for platform staff (billing.manage to change anything): the catalog (plans, add-ons and credit packs, versioned),
each organization's subscription, and the requests customers send while payments go through staff.

Every change needs a reason and a fresh sign-in. It is written to the platform audit log and shown in the organization's
own activity ("… of Syruss support moved the organization to the Team plan, because: …").
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.v1.sysadmin.access import Staff, record, require_staff
from app.api.v1.sysadmin.common import names, tell_organization
from app.core.database import get_db_session
from app.models.billing import (
    BillingPayment, BillingRequest, CatalogItem, OrgLimitOverride, OrgSubscription, OrgSubscriptionAddon,
)
from app.models.tenant import Tenant
from app.models.workspace import Workspace
from app.services.billing import credits, service as billing
from app.services.billing.limits import LIMITS
from app.services.billing.providers import provider

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
    """Every limit a plan or add-on can set (the catalog editor's fields), and the payment provider in use."""
    return {"limits": [limit.describe() for limit in LIMITS.values()], "provider": provider().describe()}


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
    await provider().publish(db, item)
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
    """Every organization's plan, pages this period, credits and open requests."""
    stmt = select(Tenant).order_by(Tenant.name).limit(limit)
    if q:
        stmt = stmt.where(or_(Tenant.name.ilike(f"%{q}%"), Tenant.slug.ilike(f"%{q}%")))
    out = []
    open_requests = dict((await db.execute(select(BillingRequest.tenant_id, func.count()).where(
        BillingRequest.status == "open").group_by(BillingRequest.tenant_id))).all())
    for tenant in (await db.execute(stmt)).scalars().all():
        s = await billing.summary(db, tenant.id)
        sub = await db.get(OrgSubscription, tenant.id)
        out.append({"id": tenant.id, "name": tenant.name, "slug": tenant.slug, "status": tenant.status, "plan": s["plan"], "plan_key": s["plan_key"],
                    "subscription_status": sub.status, "trial_ends_at": sub.trial_ends_at, "paid_until": sub.paid_until,
                    "pages_used": s["pages_used"], "pages_limit": s["pages_limit"], "credits": s["credits"],
                    "period_end": s["period_end"], "open_requests": open_requests.get(tenant.id, 0)})
    await db.commit()  # subscriptions created or brought up to date while reading
    return out


def _request_dict(r: BillingRequest, item: Optional[CatalogItem]) -> Dict[str, Any]:
    return {"id": r.id, "tenant_id": r.tenant_id, "item": item_dict(item) if item else None, "quantity": r.quantity, "note": r.note,
            "requested_by_name": r.requested_by_name, "status": r.status, "handled_by_name": r.handled_by_name, "handled_note": r.handled_note,
            "created_at": r.created_at, "handled_at": r.handled_at}


def _payment_dict(p: BillingPayment) -> Dict[str, Any]:
    return {"id": p.id, "amount_cents": p.amount_cents, "currency": p.currency, "item_id": p.item_id, "quantity": p.quantity,
            "reference": p.reference, "note": p.note, "recorded_by_name": p.recorded_by_name, "created_at": p.created_at}


@router.get("/organizations/{tenant_id}", dependencies=[VIEW])
async def organization(tenant_id: str, db: AsyncSession = Depends(get_db_session)):
    """One organization's billing: the subscription, where each limit comes from, usage, credits, payments and requests."""
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
    payments = (await db.execute(select(BillingPayment).where(BillingPayment.tenant_id == tenant_id)
                                 .order_by(BillingPayment.created_at.desc()).limit(50))).scalars().all()
    requests = (await db.execute(select(BillingRequest, CatalogItem).join(CatalogItem, CatalogItem.id == BillingRequest.item_id)
                                 .where(BillingRequest.tenant_id == tenant_id).order_by(BillingRequest.created_at.desc()).limit(50))).all()
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
        "payments": [_payment_dict(p) for p in payments],
        "requests": [_request_dict(r, i) for r, i in requests],
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


class PaymentIn(BaseModel):
    amount_cents: int = Field(..., ge=0)
    currency: str = Field("USD", pattern=r"^[A-Z]{3}$")
    item_id: Optional[str] = Field(None, description="What it paid for: applied when the payment is recorded")
    quantity: int = Field(1, ge=1, le=10000)
    reference: Optional[str] = Field(None, max_length=200)
    note: Optional[str] = Field(None, max_length=2000)
    reason: str = Field(..., min_length=5, max_length=1000)


@router.post("/organizations/{tenant_id}/payments", status_code=201)
async def record_payment(tenant_id: str, body: PaymentIn, staff: Staff = Depends(CHANGE), db: AsyncSession = Depends(get_db_session)):
    """Records money received; a plan, add-on or credit pack it paid for is applied at once."""
    await _tenant(db, tenant_id)
    item = await _item(db, body.item_id) if body.item_id else None
    try:
        payment, done = await billing.record_payment(db, tenant_id, amount_cents=body.amount_cents, currency=body.currency, by_id=staff.user_id,
                                                     by_name=staff.name, item=item, quantity=body.quantity, reference=body.reference, note=body.note)
    except ValueError as exc:
        raise _refused(exc)
    text = f"recorded a payment of {body.amount_cents / 100:,.2f} {body.currency}" + (f" and {done}" if done else "")
    record(db, staff, "billing.payment_recorded", "organization", tenant_id, tenant_id=tenant_id, reason=body.reason,
           details={"payment_id": payment.id, "amount_cents": body.amount_cents, "currency": body.currency, "item_id": body.item_id})
    tell_organization(db, staff, tenant_id, text, body.reason)
    await db.commit()
    return {**_payment_dict(payment), "done": text}


# ---------------------------------------------------------------------------
# Requests (the manual payment provider)
# ---------------------------------------------------------------------------

@router.get("/requests", dependencies=[VIEW])
async def requests(status: Optional[str] = "open", db: AsyncSession = Depends(get_db_session)):
    stmt = select(BillingRequest, CatalogItem).join(CatalogItem, CatalogItem.id == BillingRequest.item_id).order_by(BillingRequest.created_at)
    rows = (await db.execute(stmt.where(BillingRequest.status == status) if status else stmt.limit(200))).all()
    named = await names(db, [r.tenant_id for r, _ in rows])
    return [{**_request_dict(r, i), "organization": named["organizations"].get(r.tenant_id)} for r, i in rows]


class HandleIn(BaseModel):
    action: Literal["fulfil", "decline"]
    note: Optional[str] = Field(None, max_length=2000, description="Shown to the customer")
    amount_cents: Optional[int] = Field(None, ge=0, description="The payment received for it, recorded with it")
    reference: Optional[str] = Field(None, max_length=200)
    reason: str = Field(..., min_length=5, max_length=1000)


@router.post("/requests/{request_id}")
async def handle_request(request_id: str, body: HandleIn, staff: Staff = Depends(CHANGE), db: AsyncSession = Depends(get_db_session)):
    """Completes a customer's request (applying it, with the payment when one is given) or declines it."""
    request = await db.get(BillingRequest, request_id)
    if request is None:
        raise HTTPException(status_code=404, detail="No such request.")
    item = await db.get(CatalogItem, request.item_id)
    try:
        done = await billing.handle_request(db, request, fulfil=body.action == "fulfil", note=body.note, by_id=staff.user_id, by_name=staff.name,
                                            amount_cents=body.amount_cents, reference=body.reference)
    except ValueError as exc:
        raise _refused(exc)
    text = done or f"declined the request for {item.name}"
    if done and body.amount_cents is not None:
        text = f"recorded a payment of {body.amount_cents / 100:,.2f} {item.currency} and {done}"
    record(db, staff, f"billing.request_{'fulfilled' if body.action == 'fulfil' else 'declined'}", "billing_request", request.id,
           tenant_id=request.tenant_id, reason=body.reason, details={"item_id": item.id, "quantity": request.quantity})
    tell_organization(db, staff, request.tenant_id, text, body.reason)
    await db.commit()
    return {"done": text, "status": request.status}


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
