"""
The organization's billing, for the people who manage it (org billing.manage: its owners): the plan and period, pages
used out of the allowance and by which workspace, prepaid credits, add-ons and history, what is on offer, and buying.

Buying goes through the payment provider (app/services/billing/providers): with the manual one, it sends a request to
Syruss, who invoice it and switch it on when it is paid.
"""
from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.auth import AuthContext, require_org_permission
from app.core.database import get_db_session
from app.models.audit import AuditLog
from app.models.billing import BillingPayment, BillingRequest, CatalogItem, OrgSubscriptionAddon
from app.models.workspace import Workspace
from app.services.billing import catalog, credits, service as billing
from app.services.billing.limits import LIMITS
from app.services.billing.providers import Buyer, provider

router = APIRouter(prefix="/billing", tags=["Billing"])
BILLING = Depends(require_org_permission("billing.manage"))


def _offer(i: CatalogItem) -> Dict[str, Any]:
    return {"id": i.id, "kind": i.kind, "key": i.key, "name": i.name, "description": i.description, "price_cents": i.price_cents,
            "currency": i.currency, "interval": i.interval, "limits": i.limits or {}, "pages": i.pages}


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
    payments = (await db.execute(select(BillingPayment, CatalogItem).outerjoin(CatalogItem, CatalogItem.id == BillingPayment.item_id)
                                 .where(BillingPayment.tenant_id == tenant_id).order_by(BillingPayment.created_at.desc()).limit(50))).all()
    requests = (await db.execute(select(BillingRequest, CatalogItem).join(CatalogItem, CatalogItem.id == BillingRequest.item_id)
                                 .where(BillingRequest.tenant_id == tenant_id).order_by(BillingRequest.created_at.desc()).limit(20))).all()
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
        "payments": [{"amount_cents": p.amount_cents, "currency": p.currency, "for": i.name if i else None, "quantity": p.quantity,
                      "reference": p.reference, "created_at": p.created_at} for p, i in payments],
        "requests": [{"id": r.id, "item": _offer(i), "quantity": r.quantity, "status": r.status, "note": r.handled_note,
                      "created_at": r.created_at, "handled_at": r.handled_at} for r, i in requests],
        "provider": provider().describe(),
    }
    await db.commit()  # the subscription may have been created or brought up to date
    return result


@router.get("/catalog", dependencies=[BILLING])
async def offers(db: AsyncSession = Depends(get_db_session)):
    """What is on offer: the newest published version of each public plan, add-on and credit pack."""
    return {kind: [_offer(i) for i in await catalog.offered(db, kind)] for kind in ("plan", "addon", "credits")}


class PurchaseIn(BaseModel):
    item_id: str
    quantity: int = Field(1, ge=1, le=1000)
    note: Optional[str] = Field(None, max_length=2000)


@router.post("/purchases", status_code=201)
async def purchase(body: PurchaseIn, auth: AuthContext = BILLING, db: AsyncSession = Depends(get_db_session)):
    """Starts buying a plan, an add-on or credits: a page to pay on, or a request Syruss completes."""
    item = await db.get(CatalogItem, body.item_id)
    if item is None or item.status != "published" or not item.public:
        raise HTTPException(status_code=404, detail="That isn't on offer.")
    buyer = Buyer(tenant_id=auth.tenant_id, user_id=auth.user_id, name=auth.username or auth.user_id, email=auth.email)
    try:
        started = await provider().start_purchase(db, buyer, item, body.quantity, body.note)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    db.add(AuditLog(tenant_id=auth.tenant_id, template_id=None, event_type="BILLING_REQUESTED", entity_type="Organization",
                    entity_id=auth.tenant_id, actor=auth.user_id, details={"item": item.name, "quantity": body.quantity}))
    await db.commit()
    return {"status": started.status, "message": started.message, "url": started.url, "request_id": started.request_id}
