"""
Live updates over Server-Sent Events, through the same signed-in path as every other request. Who may listen is
decided exactly as for WebSockets (see services/channels.py): when the stream opens, and again every minute while
it stays open, so removing someone from a workspace cuts them off.
"""
import asyncio
import json
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from app.core.auth import AuthContext, get_current_auth
from app.core.database import AsyncSessionLocal
from app.services import channels
from app.services.event_broadcaster import broadcaster

router = APIRouter(prefix="/live", tags=["Live updates"])

RECHECK_SECONDS = 60
KEEPALIVE_SECONDS = 15


def _frame(event: dict) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"


async def _events(request: Request, channel: str, user_id: str, tenant_id: str, replay: bool):
    queue = broadcaster.subscribe(channel, replay=replay)
    try:
        yield _frame({"event_type": "connected", "channel_id": channel, "message": f"Connected to live stream for {channel}"})
        idle = 0
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=KEEPALIVE_SECONDS)
            except asyncio.TimeoutError:
                if await request.is_disconnected():
                    return
                idle += KEEPALIVE_SECONDS
                if idle >= RECHECK_SECONDS:
                    idle = 0
                    if not await channels.still_allowed(user_id, tenant_id, channel):
                        return
                yield ": keepalive\n\n"
                continue
            yield _frame(event)
    finally:
        broadcaster.unsubscribe(channel, queue)


@router.get("/{channel_id}")
async def live(channel_id: str, request: Request, auth: AuthContext = Depends(get_current_auth)):
    """Opens a live channel as the signed-in person. `notifications` is always your own."""
    channel = f"user:{auth.user_id}" if channel_id == "notifications" else channel_id
    if not await channels.still_allowed(auth.user_id, auth.tenant_id, channel):
        raise HTTPException(status_code=403, detail="You can't listen on that channel.")
    return StreamingResponse(
        _events(request, channel, auth.user_id, auth.tenant_id, replay=not channel.startswith("user:")),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )
