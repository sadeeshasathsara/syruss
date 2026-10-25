"""
Live channels over WebSocket. Every socket needs a single-use ticket (POST /ws/ticket, made as the
signed-in person); the channel is authorized when the ticket is issued, again when the socket opens,
and again every minute while it stays open, so removing someone from a workspace cuts them off.
"""
import asyncio
import json
import logging
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.auth import AuthContext, get_current_auth
from app.core.database import AsyncSessionLocal, get_db_session
from app.services import channels
from app.services.event_broadcaster import broadcaster

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ws", tags=["WebSockets"])

RECHECK_SECONDS = 60
CLOSE_UNAUTHORIZED = 4401
CLOSE_FORBIDDEN = 4403


class TicketIn(BaseModel):
    channel: str = Field(..., min_length=1, max_length=255, description="A channel id, or 'notifications' for your own")


@router.post("/ticket")
async def ws_ticket(body: TicketIn, db: AsyncSession = Depends(get_db_session), auth: AuthContext = Depends(get_current_auth)):
    """A pass to open one WebSocket on one channel, valid for seconds and usable once."""
    channel = f"user:{auth.user_id}" if body.channel == "notifications" else body.channel
    token = await channels.issue_ticket(db, auth, channel)
    if token is None:
        raise HTTPException(status_code=403, detail="You can't listen on that channel.")
    return {"ticket": token, "channel": channel, "expires_in": 30}


@router.websocket("/tenant/{tenant_id}")
async def websocket_tenant_stream(websocket: WebSocket, tenant_id: str):
    await _stream_channel(websocket, f"tenant:{tenant_id}", f"tenant {tenant_id}")


@router.websocket("/documents/{document_id}")
async def websocket_document_stream(websocket: WebSocket, document_id: str):
    await _stream_channel(websocket, document_id, "document")


@router.websocket("/templates/{session_id}")
async def websocket_template_stream(websocket: WebSocket, session_id: str):
    await _stream_channel(websocket, session_id, "template discovery session")


@router.websocket("/notifications")
async def websocket_notifications(websocket: WebSocket):
    """Your own notifications, live. The channel comes from your ticket, never from the URL."""
    ticket = await _redeem(websocket, None)
    if ticket is None:
        return
    await _serve(websocket, ticket.channel, "notifications", ticket.user_id, ticket.tenant_id, replay=False)


@router.websocket("/channel/{channel_id}")
async def websocket_generic_stream(websocket: WebSocket, channel_id: str):
    await _stream_channel(websocket, channel_id, "channel")


async def _redeem(websocket: WebSocket, channel):
    """Takes the ticket from the query string. Browsers can't set headers on a WebSocket."""
    token = websocket.query_params.get("ticket")
    if not token:
        await websocket.close(code=CLOSE_UNAUTHORIZED)
        return None
    async with AsyncSessionLocal() as db:
        if channel is None:  # notifications: the ticket says which user channel it is for
            channel = await channels.ticket_channel(db, token)
        ticket = await channels.redeem_ticket(db, token, channel) if channel else None
    if ticket is None:
        await websocket.close(code=CLOSE_UNAUTHORIZED)
    return ticket


async def _stream_channel(websocket: WebSocket, channel_id: str, label: str):
    ticket = await _redeem(websocket, channel_id)
    if ticket is None:
        return
    await _serve(websocket, channel_id, label, ticket.user_id, ticket.tenant_id)


async def _serve(websocket: WebSocket, channel_id: str, label: str, user_id: str, tenant_id: str, replay: bool = True):
    if not await channels.still_allowed(user_id, tenant_id, channel_id):
        await websocket.close(code=CLOSE_FORBIDDEN)
        return
    await websocket.accept()
    queue = broadcaster.subscribe(channel_id, replay=replay)
    logger.info("WebSocket client connected to %s '%s'.", label, channel_id)
    try:
        await websocket.send_text(json.dumps({
            "event_type": "connected", "channel_id": channel_id, "document_id": channel_id, "session_id": channel_id,
            "message": f"Connected to live stream for {label} {channel_id}",
        }))
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=RECHECK_SECONDS)
            except asyncio.TimeoutError:
                if not await channels.still_allowed(user_id, tenant_id, channel_id):
                    await websocket.close(code=CLOSE_FORBIDDEN)
                    return
                continue
            await websocket.send_text(json.dumps(event, default=str))
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected from %s '%s'.", label, channel_id)
    except Exception as ex:
        logger.error("WebSocket error on %s '%s': %s", label, channel_id, ex)
    finally:
        broadcaster.unsubscribe(channel_id, queue)
