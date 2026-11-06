"""
The platform's live view over Server-Sent Events, through the admin app's signed-in proxy (services/platform_feed).

  scope=ops                       facts from every organization (filter with tenant_id, workspace_id, kinds)
  scope=run&id=<run>              one run; with content open, everything it thinks and does as it happens
  scope=document&id=<document>    one document; with content open, every event about it

The stream starts with a snapshot (what is running now), so a page is right before the first event. Who is listening is
checked again every minute (still on the staff, the content grant still open), and the stream ends after a few minutes so
the browser reconnects through the proxy with a fresh sign-in.
"""
import asyncio
import json
from datetime import datetime, timezone
from typing import Literal, Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from app.api.v1.sysadmin.access import Staff, require_staff, staff_role
from app.api.v1.sysadmin.runs import _labelled
from app.api.v1.sysadmin.support_access import active_grant
from app.core.database import AsyncSessionLocal
from app.core.platform_permissions import PLATFORM_GRANTS
from app.models.document import Document
from app.models.identity import User
from app.models.trace import AgentExecutionTrace as Run
from app.services import platform_feed

router = APIRouter()

KEEPALIVE_SECONDS = 15
RECHECK_SECONDS = 60
STREAM_SECONDS = 300
RECONNECT_MS = 2000


def _frame(event: dict) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"


async def _still_allowed(staff: Staff, tenant_id: Optional[str], content: bool) -> bool:
    async with AsyncSessionLocal() as db:
        role, _ = await staff_role(db, staff.user_id)
        user = await db.get(User, staff.user_id)
        if role is None or "platform.view" not in PLATFORM_GRANTS[role] or (user is not None and user.status != "active"):
            return False
        if content:
            staff = staff.model_copy(update={"role": role, "permissions": sorted(PLATFORM_GRANTS[role])})
            return await active_grant(db, staff, tenant_id) is not None
    return True


@router.get("/live")
async def live(
    request: Request,
    scope: Literal["ops", "run", "document"] = "ops",
    id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    workspace_id: Optional[str] = None,
    kinds: Optional[str] = None,
    staff: Staff = Depends(require_staff()),
):
    filters = {"tenants": {tenant_id} if tenant_id else None, "workspaces": {workspace_id} if workspace_id else None,
               "kinds": {k for k in (kinds or "").split(",") if k} or None}
    content, owner, snapshot = False, None, None
    async with AsyncSessionLocal() as db:
        if scope == "ops":
            running = list((await db.execute(select(Run).where(Run.status == "RUNNING").order_by(Run.started_at).limit(500))).scalars().all())
            snapshot = {"kind": "snapshot", "at": datetime.now(timezone.utc), **await _labelled(db, staff, running)}
            listener = platform_feed.listen("ops", **filters)
        else:
            if not id:
                raise HTTPException(status_code=422, detail="Say which run or document to follow (id).")
            if scope == "run":
                r = await db.get(Run, id) or (await db.execute(select(Run).where(Run.run_id == id).limit(1))).scalar_one_or_none()
                if r is None or not r.run_id:
                    raise HTTPException(status_code=404, detail="No such run.")
                owner, key, only = r.tenant_id, f"run:{r.run_id}", {"run_id": r.run_id}
                snapshot = {"kind": "snapshot", "run_id": r.run_id, "status": r.status}
            else:
                d = await db.get(Document, id)
                if d is None:
                    raise HTTPException(status_code=404, detail="No such document.")
                owner, key, only = d.tenant_id, f"document:{d.id}", {"document_id": d.id}
                snapshot = {"kind": "snapshot", "document_id": d.id, "status": d.status.value}
            content = await active_grant(db, staff, owner) is not None
            # With content open, everything about it; without, its facts from the platform view.
            listener = platform_feed.listen(key) if content else platform_feed.listen("ops", only=only)
            snapshot["content_open"] = content

    async def events():
        started = asyncio.get_running_loop().time()
        checked = started
        try:
            yield f"retry: {RECONNECT_MS}\n\n"
            yield _frame(snapshot)
            while True:
                now = asyncio.get_running_loop().time()
                if now - started > STREAM_SECONDS:
                    yield _frame({"kind": "reconnect"})
                    return
                if now - checked > RECHECK_SECONDS:
                    checked = now
                    if not await _still_allowed(staff, owner, content):
                        yield _frame({"kind": "closed", "reason": "access_changed"})
                        return
                try:
                    event = await asyncio.wait_for(listener.queue.get(), timeout=KEEPALIVE_SECONDS)
                except asyncio.TimeoutError:
                    if await request.is_disconnected():
                        return
                    yield ": keepalive\n\n"
                    continue
                missed = listener.take_missed()
                if missed:
                    yield _frame({"kind": "missed", "count": missed})
                yield _frame(event)
        finally:
            platform_feed.stop(listener)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})
