"""General settings: preferences, the agent's budget and reflection, plan and usage, and your own notification preferences."""
from typing import Any, Dict
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db_session
from app.core.permissions import Access, get_access, require
from app.models.billing import CatalogItem
from app.models.workspace_admin import WorkspaceMember
from app.models.jobs import WorkspaceJob
from app.models.template import DocumentTemplate
from app.services import budget_service, scheduler, settings_service, workspace_events
from app.services.billing import catalog
from app.services.billing import service as billing

router = APIRouter()


def _access_dict(access: Access) -> Dict[str, Any]:
    return {"role": access.role, "role_label": access.role_name, "permissions": sorted(access.permissions)}


@router.get("")
async def get_settings(access: Access = Depends(get_access), db: AsyncSession = Depends(get_db_session)):
    data = await settings_service.get_settings(db, access.scope.template_id)
    return {"settings": data.model_dump(), "access": _access_dict(access)}


@router.patch("")
async def update_settings(patch: Dict[str, Any], access: Access = Depends(require("workspace.manage")), db: AsyncSession = Depends(get_db_session)):
    data = await settings_service.update_settings(db, access.scope.template_id, patch, access.auth.user_id)
    await db.commit()
    budget_service.forget(access.scope.template_id)
    return {"settings": data.model_dump(), "access": _access_dict(access)}


@router.get("/budget")
async def budget(access: Access = Depends(get_access), db: AsyncSession = Depends(get_db_session)):
    """The agent's budget this month: limits, what is used, and whether its work is paused."""
    return await budget_service.status(db, access.scope.template_id)


@router.get("/reflection")
async def reflection(access: Access = Depends(get_access), db: AsyncSession = Depends(get_db_session)):
    """When the agent last reflected, what it found, and when it will next."""
    tid = access.scope.template_id
    return {
        "cadence": (await settings_service.get_settings(db, tid)).agent.reflection,
        "reflect": scheduler.job_dict(await db.get(WorkspaceJob, (tid, "reflect"))),
        "graph": scheduler.job_dict(await db.get(WorkspaceJob, (tid, "graph"))),
    }


@router.post("/reflection/run")
async def reflect_now(access: Access = Depends(require("workspace.manage")), db: AsyncSession = Depends(get_db_session)):
    """Consolidates memory and asks the agent to reflect now, whatever the schedule."""
    template = await db.get(DocumentTemplate, access.scope.template_id)
    if not template or not template.active_version_id:
        raise HTTPException(status_code=409, detail="Publish the template first: there is no work to reflect on yet.")
    result = await scheduler.reflect(db, template, requested_by={
        "id": access.auth.user_id, "name": access.auth.username or access.auth.user_id,
    })
    if result is None:
        raise HTTPException(status_code=409, detail="A reflection is already being prepared. Try again in a moment.")
    await db.commit()
    scheduler.agent_event_relay.notify()
    await workspace_events.dispatch(db)
    return {"reflect": scheduler.job_dict(await db.get(WorkspaceJob, (template.id, "reflect"))), "result": result}


def _plan_row(item: CatalogItem) -> Dict[str, Any]:
    limits = item.limits or {}
    return {"name": item.name, "price_per_month": item.price_cents / 100, "documents_per_month": limits.get(billing.PAGES),
            **{k: limits.get(f"{k}_per_workspace") for k in ("members", "api_keys", "webhooks", "channels", "destinations")}}


@router.get("/billing")
async def billing_overview(access: Access = Depends(get_access), db: AsyncSession = Depends(get_db_session)):
    """The organization's plan as this workspace sees it, and the plans on offer. The plan itself is the organization's."""
    current = await billing.standing(db, access.scope.tenant_id, access.scope.template_id)
    plans = {p.key: _plan_row(p) for p in await catalog.offered(db)}
    plans.setdefault(current.plan.key, _plan_row(current.plan))
    return {
        "plan": current.plan.key, "plans": plans, "usage": await settings_service.usage(db, access.scope.template_id),
        "pages": await billing.summary(db, access.scope.tenant_id),
        # No payment provider is connected yet: nothing is charged, and nothing is pretended.
        "payment_method": None, "invoices": [], "payments_enabled": False,
    }


@router.get("/notifications")
async def my_notification_prefs(access: Access = Depends(get_access), db: AsyncSession = Depends(get_db_session)):
    me = (await db.execute(select(WorkspaceMember).where(
        WorkspaceMember.template_id == access.scope.template_id, WorkspaceMember.user_id == access.auth.user_id,
    ))).scalar_one_or_none()
    prefs = (me.notification_prefs if me else {}) or {}
    return [
        {**t, "enabled": prefs.get(t["type"], t["notify_by_default"]),
         "available": workspace_events.EVENT_TYPES[t["type"]]["permission"] in access.permissions}
        for t in workspace_events.event_types()
    ]


@router.put("/notifications")
async def set_my_notification_prefs(prefs: Dict[str, bool], access: Access = Depends(get_access), db: AsyncSession = Depends(get_db_session)):
    me = (await db.execute(select(WorkspaceMember).where(
        WorkspaceMember.template_id == access.scope.template_id, WorkspaceMember.user_id == access.auth.user_id,
    ))).scalar_one_or_none()
    if me is None:
        raise HTTPException(status_code=409, detail="Only members of this workspace have notification preferences here.")
    me.notification_prefs = {k: bool(v) for k, v in prefs.items() if k in workspace_events.EVENT_TYPES}
    await db.commit()
    return await my_notification_prefs(access, db)
