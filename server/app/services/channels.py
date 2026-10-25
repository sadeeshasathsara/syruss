"""
Who may listen to which live channel, and the single-use tickets that let a browser open a WebSocket.

A browser can't send an Authorization header on a WebSocket and a token must never sit in a URL, so the
web app's server asks for a ticket here (as the signed-in person), and the browser opens the socket with it.
A ticket is bound to one person and one channel, lasts seconds, and works once.

Channels: `user:{id}` (your notifications), `tenant:{id}` (your organization), `template:{id}` (a workspace,
for its members), a conversation id (a private chat is its person's alone), a document id (workspace
members who can see documents).
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.auth import auth_for
from app.core.auth.context import AuthContext
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.scope import membership
from app.models.conversation import Conversation
from app.models.document import Document
from app.models.identity import WsTicket
from app.models.template import DocumentTemplate

# Syruss's own conversations are for people whose role has this permission (kept in step with conversation_service)
AUDIENCE_PERMISSION = {"admin": "template.publish", "reviewer": "documents.review"}


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def can_listen(db: AsyncSession, auth: AuthContext, channel: str) -> bool:
    if channel.startswith("user:"):
        return channel == f"user:{auth.user_id}"
    if channel.startswith("tenant:"):
        return channel == f"tenant:{auth.tenant_id}"
    if channel.startswith("template:"):
        template = await db.get(DocumentTemplate, channel.removeprefix("template:"))
        return bool(template and template.tenant_id == auth.tenant_id and await membership(db, template.id, auth.user_id))
    conv = await db.get(Conversation, channel)
    if conv is not None:
        found = await membership(db, conv.template_id, auth.user_id) if conv.tenant_id == auth.tenant_id else None
        if found is None:
            return False
        if conv.kind in ("agent", "flag"):
            return any(p in (found[1].permissions or []) for p in [AUDIENCE_PERMISSION.get(conv.audience, "")] if p)
        return conv.user_id == auth.user_id  # a private chat is its person's alone
    doc = await db.get(Document, channel)
    if doc is not None and doc.tenant_id == auth.tenant_id:
        found = await membership(db, doc.template_id, auth.user_id)
        return bool(found and "documents.view" in (found[1].permissions or []))
    return False


async def still_allowed(user_id: str, tenant_id: str, channel: str) -> bool:
    """Whether this person may listen right now, from their current membership (not from anything remembered)."""
    auth = await auth_for(user_id, tenant_id)
    if auth is None:
        return False
    async with AsyncSessionLocal() as db:
        return await can_listen(db, auth, channel)


async def issue_ticket(db: AsyncSession, auth: AuthContext, channel: str) -> Optional[str]:
    """A ticket for this person to listen on `channel`, or None when they may not."""
    if not await can_listen(db, auth, channel):
        return None
    now = datetime.now(timezone.utc)
    await db.execute(delete(WsTicket).where(WsTicket.expires_at < now - timedelta(minutes=5)))
    token = secrets.token_urlsafe(24)
    db.add(WsTicket(token_hash=_hash(token), user_id=auth.user_id, tenant_id=auth.tenant_id, channel=channel,
                    expires_at=now + timedelta(seconds=settings.WS_TICKET_TTL_SECONDS)))
    await db.commit()
    return token


async def ticket_channel(db: AsyncSession, token: str) -> Optional[str]:
    """The channel a ticket was issued for (the notifications socket learns its channel from its ticket)."""
    return (await db.execute(select(WsTicket.channel).where(WsTicket.token_hash == _hash(token)))).scalar_one_or_none()


async def redeem_ticket(db: AsyncSession, token: str, channel: str) -> Optional[WsTicket]:
    """Marks the ticket used (once, atomically) and returns it if it was valid for this channel."""
    now = datetime.now(timezone.utc)
    result = await db.execute(
        update(WsTicket).where(WsTicket.token_hash == _hash(token), WsTicket.used_at.is_(None), WsTicket.expires_at > now,
                               WsTicket.channel == channel).values(used_at=now).returning(WsTicket.token_hash)
    )
    if result.first() is None:
        await db.commit()
        return None
    await db.commit()
    return (await db.execute(select(WsTicket).where(WsTicket.token_hash == _hash(token)))).scalar_one()
