"""
Live support in a person's own chat: the agent hands the conversation to someone from the Syruss team, who talks with
them directly, and takes it back when they leave.

    request   the agent asks for a person (request_live_support). Only when someone on the staff is online; the chat
              is then "waiting" and shows in the staff's queue
    accept    the first staff member to accept takes it ("live"); from then on the person's messages go to them and the
              agent stays silent (conversation_service.post_user_message doesn't wake it, and a reply it was about to
              send is refused)
    end       either side ends it ("ended"); the agent answers the next message, with the whole exchange in its history

Events (services/live/ui_events): the person's private chat channel gets support_state, support_message and
support_typing; the staff get support:queue (chats waiting, taken, ended) and support:chat:{id} (one chat's messages
and typing), served to them over the admin app's live stream (api/v1/sysadmin/live.py).

The scheduler's `expire` job gives up on a chat nobody joined within WAIT_MINUTES (the agent offers a ticket instead)
and puts a chat back in the queue when the staff member in it has been gone for STAFF_GONE_MINUTES.
"""
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.conversation import ChatMessage, Conversation
from app.models.support_live import SupportPresence
from app.services.live.ui_events import publish_ui_event

PRESENCE_SECONDS = 90         # a heartbeat older than this counts as offline
WAIT_MINUTES = 10             # nobody joined: the chat goes back to the agent, which offers a ticket
STAFF_GONE_MINUTES = 3        # the staff member in a chat went offline: it goes back to the queue
COOLDOWN_MINUTES = 10         # after a live chat ends, before the agent may ask again
QUEUE = "support:queue"
ACTIVE = ("waiting", "live")


class LiveError(Exception):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def chat_channel(conversation_id: str) -> str:
    return f"support:chat:{conversation_id}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _when(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value else None


def info(conv: Conversation) -> Dict[str, Any]:
    return dict(conv.support or {})


def first_name(name: str) -> str:
    return (name or "Someone").split()[0]


def public(conv: Conversation) -> Optional[Dict[str, Any]]:
    """What the person sees about live support in their chat (never the agent's handover notes)."""
    if not conv.support_state:
        return None
    s = info(conv)
    return {"state": conv.support_state, "staff_name": s.get("staff_name"), "requested_at": s.get("requested_at"),
            "joined_at": s.get("joined_at"), "ended_at": s.get("ended_at"), "end_reason": s.get("end_reason"),
            "ticket_number": s.get("ticket_number"), "rating": s.get("rating")}


# ---- who is online ---------------------------------------------------------------------------------

async def online(db: AsyncSession, now: Optional[datetime] = None) -> List[SupportPresence]:
    now = now or _now()
    return list((await db.execute(select(SupportPresence).where(
        SupportPresence.status == "online", SupportPresence.seen_at > now - timedelta(seconds=PRESENCE_SECONDS))
        .order_by(SupportPresence.name))).scalars().all())


async def set_presence(db: AsyncSession, user_id: str, name: str, status: str) -> SupportPresence:
    row = await db.get(SupportPresence, user_id)
    if row is None:
        row = SupportPresence(user_id=user_id, name=name, status=status)
        db.add(row)
    row.name, row.status, row.seen_at = name, status, _now()
    return row


async def position(db: AsyncSession, conv: Conversation) -> int:
    """Its place in the queue (1 = next), by when it asked."""
    asked = info(conv).get("requested_at") or ""
    waiting = (await db.execute(select(Conversation.support).where(Conversation.support_state == "waiting"))).scalars().all()
    return 1 + sum(1 for s in waiting if (s or {}).get("requested_at", "") < asked)


# ---- events ----------------------------------------------------------------------------------------

def queue_item(conv: Conversation, org: Optional[str] = None, workspace: Optional[str] = None, person: Optional[str] = None) -> Dict[str, Any]:
    s = info(conv)
    return {"conversation_id": conv.id, "state": conv.support_state, "tenant_id": conv.tenant_id, "organization": org,
            "workspace": workspace, "person": person or s.get("person_name"), "reason": s.get("reason"), "summary": s.get("summary"),
            "requested_at": s.get("requested_at"), "joined_at": s.get("joined_at"), "staff_id": s.get("staff_id"),
            "staff_name": s.get("staff_name")}


async def tell_person(conv: Conversation, event: str, data: Dict[str, Any]) -> None:
    await publish_ui_event(conv.id, event, {"conversation_id": conv.id, **data}, tenant_id=conv.tenant_id,
                           template_id=conv.template_id, private_to=conv.user_id)


async def tell_staff(channel: str, event: str, conv: Conversation, data: Dict[str, Any]) -> None:
    await publish_ui_event(channel, event, {"conversation_id": conv.id, **data}, tenant_id=conv.tenant_id, template_id=conv.template_id)


async def announce(conv: Conversation, note: Optional[ChatMessage] = None) -> None:
    """The chat's new state, to the person and to the staff queue (and the chat's own staff channel)."""
    data = {"support": public(conv), **({"note": message_dict(note)} if note is not None else {})}
    await tell_person(conv, "support_state", data)
    await tell_staff(QUEUE, "support.changed", conv, {"item": queue_item(conv)})
    await tell_staff(chat_channel(conv.id), "support.state", conv, data)


def message_dict(m: ChatMessage) -> Dict[str, Any]:
    staff = (m.agent_trace or {}).get("staff") or {}
    return {"id": m.id, "role": m.role, "content": m.content, "staff_name": staff.get("name"), "at": _iso(m.created_at)}


def _note(db: AsyncSession, conv: Conversation, text: str) -> ChatMessage:
    msg = ChatMessage(id=str(uuid.uuid4()), conversation_id=conv.id, tenant_id=conv.tenant_id, role="system", content=text,
                      evidence_citations=[], confidence=1.0, agent_trace={"support": True}, created_at=_now())
    db.add(msg)
    conv.updated_at = _now()
    return msg


# ---- the steps -------------------------------------------------------------------------------------

async def request(db: AsyncSession, conv: Conversation, *, reason: str, summary: str, person_name: str) -> Dict[str, Any]:
    """The agent asks for someone from the team to join this chat. Returns {"status": waiting|live|unavailable|too_soon}."""
    if conv.kind != "chat":
        raise LiveError("Live support is only for a person's own chat.", 403)
    if conv.support_state in ACTIVE:
        return {"status": conv.support_state, "position": await position(db, conv) if conv.support_state == "waiting" else None}
    s = info(conv)
    ended = _when(s.get("ended_at"))
    if ended and s.get("end_reason") != "no_staff" and _now() - ended < timedelta(minutes=COOLDOWN_MINUTES):
        return {"status": "too_soon"}
    if not await online(db):
        return {"status": "unavailable"}
    conv.support_state = "waiting"
    conv.support = {"reason": reason.strip()[:300], "summary": summary.strip()[:4000], "person_name": person_name,
                    "requested_at": _iso(_now()), "rating": None}
    note = _note(db, conv, "Connecting you to the Syruss team. Someone will join this chat shortly; you can keep writing meanwhile.")
    await db.commit()
    await announce(conv, note)
    return {"status": "waiting", "position": await position(db, conv)}


async def locked(db: AsyncSession, conversation_id: str) -> Conversation:
    conv = (await db.execute(select(Conversation).where(Conversation.id == conversation_id).with_for_update())).scalar_one_or_none()
    if conv is None or not conv.support_state:
        raise LiveError("No such live chat.", 404)
    return conv


async def accept(db: AsyncSession, conversation_id: str, *, staff_id: str, staff_name: str) -> Conversation:
    """The first to accept takes the chat (the row is locked, so two can't both take it)."""
    conv = await locked(db, conversation_id)
    if conv.support_state != "waiting":
        raise LiveError("Someone else already took this chat, or it ended." if conv.support_state == "live" else "This chat isn't waiting any more.")
    s = info(conv)
    s.update(staff_id=staff_id, staff_name=staff_name, joined_at=_iso(_now()))
    conv.support_state, conv.support = "live", s
    name = first_name(staff_name)
    note = _note(db, conv, f"{name} from the Syruss team joined. {name} can see this conversation; Syruss's assistant is quiet until {name} leaves.")
    await db.commit()
    await announce(conv, note)
    return conv


def _theirs(conv: Conversation, staff_id: str) -> None:
    if conv.support_state != "live" or info(conv).get("staff_id") != staff_id:
        raise LiveError("This chat isn't yours: accept it from the queue first.", 403)


async def staff_says(db: AsyncSession, conv: Conversation, *, staff_id: str, staff_name: str, body: str) -> ChatMessage:
    _theirs(conv, staff_id)
    msg = ChatMessage(id=str(uuid.uuid4()), conversation_id=conv.id, tenant_id=conv.tenant_id, role="staff", content=body.strip(),
                      evidence_citations=[], confidence=1.0, agent_trace={"staff": {"id": staff_id, "name": staff_name}}, created_at=_now())
    db.add(msg)
    conv.updated_at = _now()
    await db.commit()
    data = message_dict(msg)
    await tell_person(conv, "support_message", data)
    await tell_staff(chat_channel(conv.id), "support.message", conv, data)
    return msg


async def person_said(conv: Conversation, msg: ChatMessage) -> None:
    """A message the person wrote while waiting or live: to the staff (it is already stored)."""
    await tell_staff(chat_channel(conv.id), "support.message", conv, message_dict(msg))


async def typing(conv: Conversation, *, side: str, name: str) -> None:
    """`side`: who is typing ("staff" or "person"); the other side is told."""
    if conv.support_state != "live":
        return
    if side == "staff":
        await tell_person(conv, "support_typing", {"name": first_name(name)})
    else:
        await tell_staff(chat_channel(conv.id), "support.typing", conv, {"name": first_name(name)})


async def end(db: AsyncSession, conv: Conversation, *, by: str, reason: str = "ended") -> Conversation:
    """Ends live support (`by`: who ended it, for the note). The agent answers the next message."""
    if conv.support_state not in ACTIVE:
        return conv
    s = info(conv)
    was_live = conv.support_state == "live"
    s.update(ended_at=_iso(_now()), end_reason=reason)
    conv.support_state, conv.support = "ended", s
    if reason == "no_staff":
        text = ("Nobody from the Syruss team could join right now. Syruss can open a support request for you instead: "
                "the team answers it by email and under Settings > Help & support.")
    elif was_live:
        text = f"{first_name(s.get('staff_name') or 'The team')} left the chat. Syruss's assistant is back: write here as before."
    else:
        text = "You stopped waiting for the Syruss team. Syruss's assistant is back."
    s["ended_by"] = by
    conv.support = s
    note = _note(db, conv, text)
    await db.commit()
    await announce(conv, note)
    return conv


async def transfer(db: AsyncSession, conv: Conversation, *, staff_id: str, to_id: str, to_name: str) -> Conversation:
    _theirs(conv, staff_id)
    s = info(conv)
    before = first_name(s.get("staff_name") or "")
    s.update(staff_id=to_id, staff_name=to_name)
    conv.support = s
    note = _note(db, conv, f"{before} handed this chat to {first_name(to_name)} from the Syruss team.")
    await db.commit()
    await announce(conv, note)
    return conv


async def rate(db: AsyncSession, conv: Conversation, *, rating: str, note: Optional[str]) -> None:
    if conv.support_state != "ended" or not info(conv).get("joined_at"):
        raise LiveError("Only a live chat that has ended can be rated.")
    s = info(conv)
    s.update(rating=rating, rating_note=(note or "").strip()[:1000] or None)
    conv.support = s
    await db.commit()
    await tell_staff(QUEUE, "support.changed", conv, {"item": queue_item(conv)})


def transcript(conv: Conversation, messages: List[ChatMessage]) -> str:
    """The live part of a chat as text, for a ticket made from it."""
    since = info(conv).get("requested_at") or ""
    lines = []
    for m in messages:
        if _iso(m.created_at) < since or m.role == "system":
            continue
        who = {"user": info(conv).get("person_name") or "Customer", "assistant": "Syruss assistant"}.get(m.role) \
            or f"{((m.agent_trace or {}).get('staff') or {}).get('name', 'Staff')} (Syruss team)"
        lines.append(f"{who}: {m.content}")
    return "\n\n".join(lines)


async def waiting_count(db: AsyncSession) -> int:
    return int(await db.scalar(select(func.count(Conversation.id)).where(Conversation.support_state == "waiting")) or 0)


async def expire(db: AsyncSession, now: Optional[datetime] = None) -> None:
    """The scheduler's job: gives up on chats nobody joined, and requeues chats whose staff member is gone."""
    now = now or _now()
    present = {p.user_id for p in (await db.execute(select(SupportPresence).where(
        SupportPresence.status == "online", SupportPresence.seen_at > now - timedelta(minutes=STAFF_GONE_MINUTES)))).scalars()}
    rows = (await db.execute(select(Conversation).where(Conversation.support_state.in_(ACTIVE)))).scalars().all()
    for conv in rows:
        s = info(conv)
        if conv.support_state == "waiting" and _when(s.get("requested_at")) and _when(s["requested_at"]) < now - timedelta(minutes=WAIT_MINUTES):
            await end(db, conv, by="", reason="no_staff")
        elif conv.support_state == "live" and s.get("staff_id") not in present:
            name = first_name(s.get("staff_name") or "")
            s.update(staff_id=None, staff_name=None, joined_at=None, requested_at=_iso(now))
            conv.support_state, conv.support = "waiting", s
            note = _note(db, conv, f"{name} lost their connection. Someone else from the Syruss team will join shortly.")
            await db.commit()
            await announce(conv, note)
