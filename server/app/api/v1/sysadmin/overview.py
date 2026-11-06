"""
The platform at a glance: who you are on the staff, the platform's figures across every organization, and whether each
service it depends on is answering. Counts only: nothing here opens a customer's documents.
"""
import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict
import httpx
from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.v1.sysadmin.access import Staff, current_staff, require_staff
from app.core.config import settings
from app.core.database import get_db_session
from app.core.platform_permissions import PLATFORM_PERMISSIONS, PLATFORM_ROLES
from app.models.delivery import Delivery
from app.models.document import Document, DocumentRole, DocumentStatus
from app.models.identity import User
from app.models.outbox import AgentEventOutbox
from app.models.tenant import Tenant
from app.models.trace import AgentExecutionTrace
from app.models.workspace import Workspace
from app.services.agent_events import agent_event_relay
from app.services.storage_service import storage_service

router = APIRouter()

# Work that should have moved on by now
STUCK_AFTER = timedelta(minutes=30)
EVENTS_LATE_AFTER = timedelta(minutes=5)


@router.get("/me")
async def me(staff: Staff = Depends(current_staff)) -> Dict[str, Any]:
    """You, as the platform staff sees you: what the admin app shows and hides follows from this."""
    return {**staff.model_dump(exclude={"auth_time", "ip"}), "role_name": staff.role.title(),
            "roles": PLATFORM_ROLES, "permission_names": PLATFORM_PERMISSIONS}


@router.get("/stats", dependencies=[Depends(require_staff())])
async def stats(db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    scalar = lambda stmt: db.scalar(stmt)  # noqa: E731
    by = lambda col, *where: db.execute(select(col, func.count()).where(*where).group_by(col))  # noqa: E731

    tenants = dict((await by(Tenant.status)).all())
    users = dict((await by(User.status)).all())
    real = Document.role == DocumentRole.DOCUMENT
    runs_today = dict((await by(AgentExecutionTrace.status, AgentExecutionTrace.started_at >= today)).all())
    waiting = (AgentEventOutbox.published_at.is_(None), AgentEventOutbox.cancelled_at.is_(None))
    return {
        "organizations": {"total": sum(tenants.values()), "active": tenants.get("active", 0),
                          "suspended": tenants.get("suspended", 0), "closed": tenants.get("closed", 0),
                          "new_this_week": await scalar(select(func.count(Tenant.id)).where(Tenant.created_at >= now - timedelta(days=7)))},
        "people": {"total": sum(users.values()), "active": users.get("active", 0), "disabled": users.get("disabled", 0),
                   "new_this_week": await scalar(select(func.count(User.id)).where(User.created_at >= now - timedelta(days=7))),
                   "seen_this_week": await scalar(select(func.count(User.id)).where(User.last_seen_at >= now - timedelta(days=7)))},
        "workspaces": {"total": await scalar(select(func.count(Workspace.id)))},
        "documents": {
            "total": await scalar(select(func.count(Document.id)).where(real)),
            "today": await scalar(select(func.count(Document.id)).where(real, Document.created_at >= today)),
            "failed": await scalar(select(func.count(Document.id)).where(real, Document.status == DocumentStatus.FAILED)),
            "stuck": await scalar(select(func.count(Document.id)).where(
                real, Document.status.in_((DocumentStatus.QUEUED, DocumentStatus.PROCESSING)), Document.updated_at < now - STUCK_AFTER)),
        },
        "agent": {"runs_today": sum(runs_today.values()), "failed_today": runs_today.get("FAILED", 0),
                  "events_waiting": await scalar(select(func.count(AgentEventOutbox.id)).where(*waiting))},
        "deliveries": {"dead": await scalar(select(func.count(Delivery.id)).where(Delivery.status == "dead"))},
    }


@router.get("/names", dependencies=[Depends(require_staff())])
async def names_of(tenants: str = "", workspaces: str = "", db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    """Names of organizations and workspaces by id (comma separated, up to 200 of each), for a live page meeting new ones."""
    from app.api.v1.sysadmin.common import names
    split = lambda s: [x for x in s.split(",") if x][:200]  # noqa: E731
    return await names(db, split(tenants), split(workspaces))


async def _timed(check: Callable[[], Awaitable[Dict[str, Any]]]) -> Dict[str, Any]:
    started = time.perf_counter()
    try:
        result = await asyncio.wait_for(check(), timeout=5)
    except Exception as exc:  # anything a check raises means the service didn't answer properly
        result = {"status": "down", "detail": str(exc)[:200] or type(exc).__name__}
    return {"latency_ms": round((time.perf_counter() - started) * 1000), **result}


@router.get("/health", dependencies=[Depends(require_staff())])
async def health(db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    """Each service the platform needs, checked now. "degraded" answers but is behind or running without a part."""
    now = datetime.now(timezone.utc)

    async def database():
        version = (await db.execute(text("SHOW server_version"))).scalar()
        return {"status": "ok", "detail": f"PostgreSQL {version}"}

    async def storage():
        exists = await asyncio.to_thread(storage_service.client.bucket_exists, settings.MINIO_BUCKET_NAME)
        return {"status": "ok" if exists else "down", "detail": f"Bucket {settings.MINIO_BUCKET_NAME}" + ("" if exists else " is missing")}

    async def sign_in():
        async with httpx.AsyncClient(timeout=4) as client:
            res = await client.get(f"{settings.OIDC_ISSUER.rstrip('/')}/.well-known/openid-configuration")
        return {"status": "ok" if res.status_code == 200 else "down", "detail": f"Keycloak answered {res.status_code}"}

    async def events():
        oldest = (await db.execute(select(func.min(AgentEventOutbox.created_at)).where(
            AgentEventOutbox.published_at.is_(None), AgentEventOutbox.cancelled_at.is_(None)))).scalar()
        if not agent_event_relay._producer:
            return {"status": "degraded", "detail": "Kafka isn't connected: events wait in the outbox"}
        if oldest and now - oldest > EVENTS_LATE_AFTER:
            return {"status": "degraded", "detail": f"Events have waited since {oldest:%H:%M} UTC"}
        return {"status": "ok", "detail": "Kafka connected, nothing waiting"}

    async def agent():
        last = (await db.execute(select(func.max(AgentExecutionTrace.started_at)))).scalar()
        late = (await db.execute(select(func.count(AgentEventOutbox.id)).where(
            AgentEventOutbox.published_at.is_not(None), AgentEventOutbox.published_at < now - EVENTS_LATE_AFTER,
            AgentEventOutbox.cancelled_at.is_(None), AgentEventOutbox.created_at > now - timedelta(hours=1),
            ~select(AgentExecutionTrace.id).where(AgentExecutionTrace.event_id == AgentEventOutbox.id).exists()))).scalar()
        if late:
            return {"status": "degraded", "detail": f"{late} event(s) sent over 5 minutes ago have no run yet"}
        return {"status": "ok", "detail": f"Last run {last:%d %b %H:%M} UTC" if last else "No runs yet"}

    checks = {"database": ("Database", database), "storage": ("File storage", storage), "sign_in": ("Sign-in", sign_in),
              "events": ("Event queue", events), "agent": ("Agent", agent)}
    # One after another: three of the checks share the request's database session, which can't run two queries at once.
    results = {}
    for key, (_, check) in checks.items():
        results[key] = await _timed(check)
    return {"checked_at": now, "services": [{"key": k, "name": name, **results[k]} for k, (name, _) in checks.items()]}
