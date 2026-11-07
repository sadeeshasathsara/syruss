"""Webhooks: endpoints this workspace's events are sent to, their signing secrets, test pings and recent deliveries."""
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.auth import require_recent_login
from app.core.database import get_db_session
from app.core.permissions import Access, require
from app.core.secrets import seal
from app.models.workspace_admin import Webhook, WebhookDelivery
from app.services import settings_service, workspace_events
from app.services.source_data import SourceError, check_host

router = APIRouter()


class WebhookIn(BaseModel):
    url: str
    description: str = ""
    events: List[str] = Field(default_factory=lambda: ["*"])
    active: bool = True


class WebhookPatch(BaseModel):
    url: Optional[str] = None
    description: Optional[str] = None
    events: Optional[List[str]] = None
    active: Optional[bool] = None


async def _webhook_dict(db: AsyncSession, h: Webhook) -> Dict[str, Any]:
    last = (await db.execute(select(WebhookDelivery).where(WebhookDelivery.webhook_id == h.id)
                             .order_by(WebhookDelivery.created_at.desc()).limit(1))).scalar_one_or_none()
    return {"id": h.id, "url": h.url, "description": h.description, "events": h.events, "active": h.active,
            "created_by": h.created_by, "created_at": h.created_at,
            "last_delivery": {"ok": last.ok, "status_code": last.status_code, "error": last.error, "at": last.created_at} if last else None}


async def _check_webhook(url: str, events: List[str]) -> None:
    unknown = [e for e in events if e != "*" and e not in workspace_events.EVENT_TYPES]
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown events: {', '.join(unknown)}.")
    if not url.startswith("https://") and not url.startswith("http://"):
        raise HTTPException(status_code=422, detail="The URL must start with https://.")
    try:
        await check_host(url)
    except SourceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


async def _webhook(db: AsyncSession, access: Access, hook_id: str) -> Webhook:
    h = await db.get(Webhook, hook_id)
    if not h or h.template_id != access.scope.template_id:
        raise HTTPException(status_code=404, detail="Webhook not found.")
    return h


@router.get("/webhooks")
async def list_webhooks(access: Access = Depends(require("integrations.manage")), db: AsyncSession = Depends(get_db_session)):
    rows = (await db.execute(select(Webhook).where(Webhook.template_id == access.scope.template_id).order_by(Webhook.created_at.desc()))).scalars().all()
    return {"webhooks": [await _webhook_dict(db, h) for h in rows], "events": workspace_events.event_types()}


@router.post("/webhooks", status_code=201, dependencies=[Depends(require_recent_login)])
async def create_webhook(body: WebhookIn, access: Access = Depends(require("integrations.manage")), db: AsyncSession = Depends(get_db_session)):
    """The signing secret is returned once."""
    await _check_webhook(body.url, body.events)
    await settings_service.check_limit(db, access.scope.template_id, "webhooks")
    secret = f"whsec_{secrets.token_urlsafe(24)}"
    hook = Webhook(
        tenant_id=access.scope.tenant_id, template_id=access.scope.template_id, url=body.url.strip(),
        description=body.description.strip(), events=body.events or ["*"], active=body.active,
        secret=seal({"secret": secret}), created_by=access.auth.username or access.auth.user_id,
    )
    db.add(hook)
    await db.commit()
    return {"webhook": await _webhook_dict(db, hook), "secret": secret}


@router.patch("/webhooks/{hook_id}", dependencies=[Depends(require_recent_login)])
async def update_webhook(hook_id: str, body: WebhookPatch, access: Access = Depends(require("integrations.manage")), db: AsyncSession = Depends(get_db_session)):
    hook = await _webhook(db, access, hook_id)
    if body.url is not None or body.events is not None:
        await _check_webhook(body.url or hook.url, body.events if body.events is not None else hook.events)
    for field in ("url", "description", "events", "active"):
        value = getattr(body, field)
        if value is not None:
            setattr(hook, field, value)
    await db.commit()
    return await _webhook_dict(db, hook)


@router.post("/webhooks/{hook_id}/rotate-secret", dependencies=[Depends(require_recent_login)])
async def rotate_webhook_secret(hook_id: str, access: Access = Depends(require("integrations.manage")), db: AsyncSession = Depends(get_db_session)):
    hook = await _webhook(db, access, hook_id)
    secret = f"whsec_{secrets.token_urlsafe(24)}"
    hook.secret = seal({"secret": secret})
    await db.commit()
    return {"secret": secret}


@router.post("/webhooks/{hook_id}/test")
async def test_webhook(hook_id: str, access: Access = Depends(require("integrations.manage")), db: AsyncSession = Depends(get_db_session)):
    """Sends a `ping` event now and reports how the endpoint answered."""
    await _webhook(db, access, hook_id)
    delivery = await workspace_events.deliver(hook_id, {
        "id": f"evt_test_{secrets.token_hex(6)}", "type": "ping", "created_at": datetime.now(timezone.utc).isoformat(),
        "workspace_id": access.scope.template_id, "data": {"message": "A test from your workspace's webhook settings."},
    })
    return {"ok": delivery.ok, "status_code": delivery.status_code, "error": delivery.error, "duration_ms": delivery.duration_ms}


@router.get("/webhooks/{hook_id}/deliveries")
async def webhook_deliveries(hook_id: str, access: Access = Depends(require("integrations.manage")), db: AsyncSession = Depends(get_db_session)):
    await _webhook(db, access, hook_id)
    rows = (await db.execute(select(WebhookDelivery).where(WebhookDelivery.webhook_id == hook_id)
                             .order_by(WebhookDelivery.created_at.desc()).limit(50))).scalars().all()
    return [{"id": d.id, "event_type": d.event_type, "ok": d.ok, "status_code": d.status_code, "error": d.error,
             "duration_ms": d.duration_ms, "at": d.created_at} for d in rows]


@router.delete("/webhooks/{hook_id}", status_code=204, dependencies=[Depends(require_recent_login)])
async def delete_webhook(hook_id: str, access: Access = Depends(require("integrations.manage")), db: AsyncSession = Depends(get_db_session)):
    await db.delete(await _webhook(db, access, hook_id))
    await db.commit()
