"""
The in-app side: a stored notification the person sees in the bell, pushed live on their own channel.
`create` runs inside the caller's transaction; `push` runs after the commit.
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.notification import Notification
from app.services.ui_events import publish_ui_event

logger = logging.getLogger("core.notifications")

ACCOUNT = "account"  # the "organization" of a notification that follows the person everywhere
_PENDING = "notifications_pending"


def to_dict(n: Notification) -> Dict[str, Any]:
    return {
        "id": n.id, "type": n.type, "title": n.title, "body": n.body, "link": n.link, "severity": n.severity,
        "workspace_id": n.template_id, "data": n.data, "read_at": n.read_at, "created_at": n.created_at,
    }


async def create(
    db: AsyncSession, *, user_id: str, tenant_id: str, type: str, title: str, body: str = "", link: Optional[str] = None,
    severity: str = "info", data: Optional[Dict[str, Any]] = None, template_id: Optional[str] = None,
    replace_key: Optional[str] = None,
) -> Notification:
    """
    Stores one notification for one person. `replace_key` (a key of `data`) makes it replace an earlier one of the
    same type with the same value there, so a resent invitation updates its notification instead of piling up.
    """
    data = data or {}
    if replace_key and data.get(replace_key) is not None:
        await retire(db, user_id=user_id, type=type, key=replace_key, value=data[replace_key])
    n = Notification(
        id=str(uuid.uuid4()), tenant_id=tenant_id, user_id=user_id, template_id=template_id, type=type, title=title, body=body,
        link=link, severity=severity, data=data, created_at=datetime.now(timezone.utc),
    )
    db.add(n)
    db.info.setdefault(_PENDING, []).append(n)
    return n


async def retire(db: AsyncSession, *, user_id: str, type: str, key: str, value: Any) -> None:
    """Removes a person's notifications of this type whose data[key] is `value` (the thing they were about is settled)."""
    rows = (await db.execute(select(Notification).where(Notification.user_id == user_id, Notification.type == type))).scalars().all()
    ids = [n.id for n in rows if (n.data or {}).get(key) == value]
    if ids:
        await db.execute(delete(Notification).where(Notification.id.in_(ids)))


async def push(db: AsyncSession) -> None:
    """After the commit: tell each person's open tabs. The notification is stored either way; the push is best effort."""
    for n in db.info.pop(_PENDING, []):
        try:
            await publish_ui_event(f"user:{n.user_id}", "notification", to_dict(n), tenant_id=n.tenant_id, private_to=n.user_id)
        except Exception as exc:
            logger.warning("Live notification failed: %s", exc)
