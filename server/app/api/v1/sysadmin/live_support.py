"""
Live support, the staff's side (services/support/live): who is online, the queue of chats waiting for a person, and the
chats a staff member took: read, answer, show typing, hand over, turn into a ticket, end.

Everything takes support.respond. A customer's chat is opened to the one staff member who accepted it, because the
customer asked for a person (no support-access grant is needed for that); before accepting, staff see only what the
assistant wrote for the handover. Accepting, handing over, ending and making a ticket are in the platform audit log and
the organization's activity. Live events come over the admin app's live stream (sysadmin/live.py, scope support|chat).
"""
from typing import Any, Dict, List, Literal, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.v1.sysadmin.access import Staff, record, require_staff
from app.api.v1.sysadmin.common import tell_organization
from app.api.v1.sysadmin.support import customer_context
from app.core.database import get_db_session
from app.core.platform_permissions import PLATFORM_GRANTS
from app.models.conversation import ChatMessage, Conversation
from app.models.identity import User
from app.models.platform import PlatformStaff
from app.models.template import DocumentTemplate
from app.models.tenant import Tenant
from app.services.support import live, mail, tickets

router = APIRouter(prefix="/support/live")
RESPOND = require_staff("support.respond")


def _fail(exc: live.LiveError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=str(exc))


async def _labels(db: AsyncSession, convs: List[Conversation]) -> List[Dict[str, Any]]:
    tenants = {t.id: t.name for t in (await db.execute(select(Tenant).where(Tenant.id.in_({c.tenant_id for c in convs})))).scalars()} if convs else {}
    spaces = {w.id: w.name for w in (await db.execute(select(DocumentTemplate).where(DocumentTemplate.id.in_({c.template_id for c in convs})))).scalars()} if convs else {}
    return [live.queue_item(c, tenants.get(c.tenant_id), spaces.get(c.template_id)) for c in convs]


async def _chat(db: AsyncSession, conversation_id: str, staff: Staff, lock: bool = False) -> Conversation:
    try:
        conv = await live.locked(db, conversation_id) if lock else await db.get(Conversation, conversation_id)
    except live.LiveError as exc:
        raise _fail(exc)
    if conv is None or not conv.support_state:
        raise HTTPException(status_code=404, detail="No such live chat.")
    return conv


def _mine(conv: Conversation, staff: Staff) -> None:
    if live.info(conv).get("staff_id") != staff.user_id:
        raise HTTPException(status_code=403, detail="This chat isn't yours: accept it from the queue first.")


# ---- presence ----------------------------------------------------------------------------------------

class PresenceIn(BaseModel):
    status: Literal["online", "away", "offline"]


@router.get("/presence")
async def my_presence(staff: Staff = Depends(RESPOND), db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    from app.models.support_live import SupportPresence
    row = await db.get(SupportPresence, staff.user_id)
    fresh = row is not None and any(p.user_id == row.user_id for p in await live.online(db))
    return {"status": row.status if row is not None and (row.status != "online" or fresh) else "offline"}


@router.put("/presence")
async def set_presence(body: PresenceIn, staff: Staff = Depends(RESPOND), db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    """Online takes chats; the admin app sends this every 30 seconds while you are online (a heartbeat)."""
    await live.set_presence(db, staff.user_id, staff.name, body.status)
    await db.commit()
    return {"status": body.status}


# ---- the queue ---------------------------------------------------------------------------------------

@router.get("/queue")
async def queue(staff: Staff = Depends(RESPOND), db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    """Chats waiting for a person (longest waiting first), yours, how many others are live, and who is online."""
    waiting = list((await db.execute(select(Conversation).where(Conversation.support_state == "waiting"))).scalars().all())
    waiting.sort(key=lambda c: live.info(c).get("requested_at") or "")
    active = list((await db.execute(select(Conversation).where(Conversation.support_state == "live"))).scalars().all())
    mine = [c for c in active if live.info(c).get("staff_id") == staff.user_id]
    return {"waiting": await _labels(db, waiting), "mine": await _labels(db, mine), "others_live": len(active) - len(mine),
            "online": [{"id": p.user_id, "name": p.name} for p in await live.online(db)]}


@router.post("/chats/{conversation_id}/accept")
async def accept(conversation_id: str, staff: Staff = Depends(RESPOND), db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    """Takes a waiting chat. The first to accept gets it; anyone else is told it's taken."""
    try:
        conv = await live.accept(db, conversation_id, staff_id=staff.user_id, staff_name=staff.name)
    except live.LiveError as exc:
        raise _fail(exc)
    record(db, staff, "support.live_accepted", "conversation", conv.id, tenant_id=conv.tenant_id,
           details={"reason": live.info(conv).get("reason")})
    tell_organization(db, staff, conv.tenant_id, f"{staff.name} of Syruss support joined a chat at a member's request",
                      "Asked for live support in their chat", conversation_id=conv.id)
    await db.commit()
    await live.tell_staff(live.QUEUE, "support.changed", conv, {"item": live.queue_item(conv)})
    return await chat(conv.id, staff, db)


@router.get("/chats/{conversation_id}")
async def chat(conversation_id: str, staff: Staff = Depends(RESPOND), db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    """A chat you took: the whole conversation (what the person and the assistant said before), the assistant's
    handover note, and who the customer is."""
    conv = await _chat(db, conversation_id, staff)
    _mine(conv, staff)
    rows = (await db.execute(select(ChatMessage).where(ChatMessage.conversation_id == conv.id)
                             .order_by(ChatMessage.created_at.desc()).limit(200))).scalars().all()
    workspace = await db.get(DocumentTemplate, conv.template_id)
    tenant = await db.get(Tenant, conv.tenant_id)
    return {
        **live.queue_item(conv, tenant.name if tenant else None, workspace.name if workspace else None),
        "title": conv.title, "support": live.public(conv), "rating_note": live.info(conv).get("rating_note"),
        "messages": [live.message_dict(m) for m in reversed(rows)],
        "context": await customer_context(db, conv.tenant_id, conv.user_id),
    }


class SayIn(BaseModel):
    body: str = Field(..., min_length=1, max_length=5000)


@router.post("/chats/{conversation_id}/messages", status_code=201)
async def say(conversation_id: str, body: SayIn, staff: Staff = Depends(RESPOND), db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    conv = await _chat(db, conversation_id, staff)
    try:
        msg = await live.staff_says(db, conv, staff_id=staff.user_id, staff_name=staff.name, body=body.body)
    except live.LiveError as exc:
        raise _fail(exc)
    return live.message_dict(msg)


@router.post("/chats/{conversation_id}/typing", status_code=204)
async def typing(conversation_id: str, staff: Staff = Depends(RESPOND), db: AsyncSession = Depends(get_db_session)) -> None:
    """You are typing: the person sees "Ana is typing…" for a few seconds. Send it at most every 3 seconds."""
    conv = await _chat(db, conversation_id, staff)
    _mine(conv, staff)
    await live.typing(conv, side="staff", name=staff.name)


class TransferIn(BaseModel):
    to: str = Field(..., description="The staff member taking over (online, and able to answer)")


@router.post("/chats/{conversation_id}/transfer")
async def transfer(conversation_id: str, body: TransferIn, staff: Staff = Depends(RESPOND), db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    conv = await _chat(db, conversation_id, staff, lock=True)
    target = next((p for p in await live.online(db) if p.user_id == body.to), None)
    role = (await db.execute(select(PlatformStaff.role).where(PlatformStaff.user_id == body.to))).scalar_one_or_none()
    from app.core.config import settings
    able = body.to in settings.platform_admin_ids or (role is not None and "support.respond" in PLATFORM_GRANTS.get(role, ()))
    if target is None or not able or body.to == staff.user_id:
        raise HTTPException(status_code=422, detail="Hand it to someone else who answers support and is online now.")
    try:
        await live.transfer(db, conv, staff_id=staff.user_id, to_id=target.user_id, to_name=target.name)
    except live.LiveError as exc:
        raise _fail(exc)
    record(db, staff, "support.live_transferred", "conversation", conv.id, tenant_id=conv.tenant_id, details={"to": target.name})
    await db.commit()
    return {"ok": True, "to": target.name}


@router.post("/chats/{conversation_id}/ticket", status_code=201)
async def to_ticket(conversation_id: str, staff: Staff = Depends(RESPOND), db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    """Turns the chat into a ticket for follow-up (assigned to you), with the conversation as its first message."""
    conv = await _chat(db, conversation_id, staff)
    _mine(conv, staff)
    s = live.info(conv)
    if s.get("ticket_number"):
        raise HTTPException(status_code=409, detail=f"This chat is already ticket {s['ticket_number']}.")
    person = await db.get(User, conv.user_id)
    if person is None or not person.email:
        raise HTTPException(status_code=422, detail="This person has no email address for a ticket.")
    rows = (await db.execute(select(ChatMessage).where(ChatMessage.conversation_id == conv.id).order_by(ChatMessage.created_at))).scalars().all()
    summary = s.get("summary") or ""
    body = (f"From a live chat. What the assistant handed over:\n{summary}\n\n" if summary else "From a live chat.\n\n") + live.transcript(conv, rows)
    ticket, first = await tickets.open_ticket(
        db, subject=(s.get("reason") or conv.title or "Live chat")[:200], body=body[:20000], category="other", channel="app",
        tenant_id=conv.tenant_id, template_id=conv.template_id, opened_by=person.id, requester_email=person.email,
        requester_name=person.display_name, author_kind="system", author_name="Live chat", related={"conversation_id": conv.id})
    ticket.assignee_id, ticket.assignee_name = staff.user_id, staff.name
    s.update(ticket_id=ticket.id, ticket_number=ticket.number)
    conv.support = s
    record(db, staff, "support.live_ticket", "conversation", conv.id, tenant_id=conv.tenant_id, details={"number": ticket.number})
    await db.commit()
    await mail.send(ticket, "support_ticket_opened", message=first)
    await live.announce(conv)
    return {"id": ticket.id, "number": ticket.number}


@router.post("/chats/{conversation_id}/end")
async def end(conversation_id: str, staff: Staff = Depends(RESPOND), db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    conv = await _chat(db, conversation_id, staff, lock=True)
    _mine(conv, staff)
    record(db, staff, "support.live_ended", "conversation", conv.id, tenant_id=conv.tenant_id)
    tell_organization(db, staff, conv.tenant_id, f"{staff.name} of Syruss support left the member's chat", "Live support ended",
                      conversation_id=conv.id)
    await live.end(db, conv, by="staff")
    return {"ok": True, "support": live.public(conv)}
