"""
Workspace events: what happened in a workspace that people or other systems care about.

emit() runs inside the caller's transaction. It stores a notification for each member who
should hear about it (their role must cover it, and their preferences allow it) and queues
the event for the workspace's webhooks. dispatch() runs after the commit: it pushes the
notifications live on each person's channel and delivers the webhooks in the background.
"""
import asyncio
import hashlib
import hmac
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import AsyncSessionLocal
from app.models.identity import WorkspaceRole
from app.core.secrets import unseal
from app.models.notification import Notification
from app.models.tenant import Tenant
from app.models.workspace import Workspace
from app.models.workspace_admin import Webhook, WebhookDelivery, WorkspaceMember
from app.services import source_data
from app.services.source_data import SourceError
from app.services import notifications
from app.services.notifications import inapp

logger = logging.getLogger("core.workspace_events")

EVENT_TYPES: Dict[str, Dict[str, Any]] = {
    "document.needs_review": {"label": "A document needs review", "permission": "documents.review", "notify": True},
    "document.failed": {"label": "A document couldn't be processed", "permission": "documents.upload", "notify": True},
    "document.completed": {"label": "A document was read and saved", "permission": "documents.upload", "notify": False},
    "agent.question": {"label": "The agent asks a question", "permission": "documents.review", "notify": True},
    "agent.screen": {"label": "Syruss suggests an analytics screen", "permission": "screens.manage", "notify": True},
    "agent.proposal": {"label": "The agent proposes a change", "permission": "template.publish", "notify": True},
    "agent.conversation": {"label": "The agent starts a conversation with you", "permission": "documents.review", "notify": True},
    "member.joined": {"label": "Someone joins the workspace", "permission": "members.manage", "notify": True},
    "usage.limit": {"label": "The workspace reaches its plan limit", "permission": "billing.manage", "notify": True},
    "agent.budget": {"label": "The agent nears or reaches its monthly budget", "permission": "workspace.manage", "notify": True},
    "agent.evaluation": {"label": "An evaluation of the agent finishes", "permission": "template.publish", "notify": True},
    "agent.reflection": {"label": "The agent reflects on recent work", "permission": "knowledge.manage", "notify": False},
    "delivery.dead": {"label": "A delivery could not be sent after every try", "permission": "integrations.manage", "notify": True},
    "destination.failing": {"label": "A destination keeps failing and was paused", "permission": "integrations.manage", "notify": True},
}
_PENDING = "workspace_events_pending"
WEBHOOK_TIMEOUT_SECONDS = 10.0


notification_dict = notifications.notification_dict


async def _base_link(db: AsyncSession, tenant_id: str, template_id: str) -> str:
    tenant = await db.get(Tenant, tenant_id)
    workspace = await db.get(Workspace, template_id)
    return f"/{tenant.slug if tenant else 'default'}/w/{workspace.slug if workspace else template_id}"


async def emit(
    db: AsyncSession,
    *,
    tenant_id: str,
    template_id: str,
    type: str,
    title: str,
    body: str = "",
    path: Optional[str] = None,
    severity: str = "info",
    data: Optional[Dict[str, Any]] = None,
    exclude_user: Optional[str] = None,
    permission: Optional[str] = None,
) -> None:
    """`permission` narrows who hears about it (by default the event type's own permission)."""
    spec = EVENT_TYPES[type]
    needed = permission or spec["permission"]
    base = await _base_link(db, tenant_id, template_id)
    link = f"{base}/{path}" if path else base
    pending = db.info.setdefault(_PENDING, {"webhooks": []})

    rows = (await db.execute(
        select(WorkspaceMember, WorkspaceRole).join(WorkspaceRole, WorkspaceRole.id == WorkspaceMember.role)
        .where(WorkspaceMember.template_id == template_id)
    )).all()
    for m, role in rows:
        if m.user_id == exclude_user or needed not in (role.permissions or []):
            continue
        if not (m.notification_prefs or {}).get(type, spec["notify"]):
            continue
        await inapp.create(db, user_id=m.user_id, tenant_id=tenant_id, template_id=template_id, type=type, title=title, body=body,
                           link=link, severity=severity, data=data or {})

    hooks = (await db.execute(select(Webhook).where(Webhook.template_id == template_id, Webhook.active.is_(True)))).scalars().all()
    event = {
        "id": f"evt_{uuid.uuid4().hex}", "type": type, "created_at": datetime.now(timezone.utc).isoformat(),
        "workspace_id": template_id, "data": {"title": title, "body": body, "link": link, **(data or {})},
    }
    for hook in hooks:
        if type in hook.events or "*" in hook.events:
            pending["webhooks"].append((hook.id, event))


async def dispatch(db: AsyncSession) -> None:
    """After the commit: live notifications, then webhook deliveries in the background."""
    await inapp.push(db)
    pending = db.info.pop(_PENDING, None)
    if not pending:
        return
    for hook_id, event in pending["webhooks"]:
        asyncio.create_task(deliver(hook_id, event))


def sign(secret: str, body: bytes, timestamp: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()


async def deliver(hook_id: str, event: Dict[str, Any]) -> Optional[WebhookDelivery]:
    """POSTs one event, signed, and records the attempt. Never raises; None if the webhook is gone."""
    async with AsyncSessionLocal() as db:
        hook = await db.get(Webhook, hook_id)
        if hook is None:
            return None
        body = json.dumps(event, default=str).encode()
        timestamp = str(int(time.time()))
        headers = {
            "Content-Type": "application/json", "User-Agent": "Syruss-Webhooks/1",
            "X-Webhook-Id": event["id"], "X-Webhook-Event": event["type"], "X-Webhook-Timestamp": timestamp,
            "X-Webhook-Signature": sign(unseal(hook.secret).get("secret", ""), body, timestamp),
        }
        started = time.perf_counter()
        status_code, error = None, None
        try:
            await source_data._check_host(hook.url)
            async with httpx.AsyncClient(transport=source_data.HTTP_TRANSPORT, timeout=WEBHOOK_TIMEOUT_SECONDS, follow_redirects=False) as client:
                response = await client.post(hook.url, content=body, headers=headers)
            status_code = response.status_code
            if not 200 <= status_code < 300:
                error = f"The endpoint answered HTTP {status_code}."
        except SourceError as exc:
            error = str(exc)
        except httpx.HTTPError as exc:
            error = f"Could not reach the endpoint: {exc}"
        delivery = WebhookDelivery(
            webhook_id=hook.id, event_type=event["type"], ok=error is None, status_code=status_code, error=error,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
        )
        db.add(delivery)
        await db.commit()
        await db.refresh(delivery)
        return delivery


def event_types() -> List[Dict[str, Any]]:
    return [{"type": k, "label": v["label"], "notify_by_default": v["notify"]} for k, v in EVENT_TYPES.items()]
