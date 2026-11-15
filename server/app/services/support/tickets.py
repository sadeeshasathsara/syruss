"""
Support tickets: opening one, adding to it, who may see it, and the service targets it is held to.

    open_ticket(...)     a new ticket with its first message (from the customer, the agent on their behalf, or email)
    add_message(...)     a reply or an internal note; it moves the ticket (a customer reply reopens it, a staff reply waits
                         for the customer) and keeps the times the targets are measured by
    visible(...)         the tickets a person in an organization may see: the ones they opened, and, for its owners and
                         admins, every ticket of the organization
    policy(db)           the targets (first response and resolution, by priority) and when waiting tickets close

Emails go out from services/support/mail.py once the caller has committed.
"""
import asyncio
import hashlib
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select
from app.models.support import CATEGORIES, PRIORITIES, SupportFile, SupportMessage, SupportTicket
from app.services import platform_controls
from app.services.billing.numbering import next_number
from app.services.billing.proofs import ALLOWED, MAX_BYTES
from app.services.storage_service import storage_service

POLICY = "support"
# Hours to the first staff answer, and to a solution, by priority (staff change them in the admin app)
DEFAULT_POLICY: Dict[str, Any] = {
    "first_response_hours": {"urgent": 1, "high": 4, "normal": 8, "low": 24},
    "resolve_hours": {"urgent": 8, "high": 24, "normal": 72, "low": 120},
    "auto_solve_days": 7,      # a ticket waiting for the customer this long is marked solved
    "auto_close_days": 14,     # a solved ticket this long is closed (a reply then opens a new one)
}
MAX_FILES = 5
ORG_MANAGERS = ("owner", "admin")


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def policy(db: AsyncSession) -> Dict[str, Any]:
    stored = await platform_controls.read_key(db, POLICY) or {}
    return {**DEFAULT_POLICY, **stored}


def _due(start: datetime, hours: Dict[str, int], priority: str) -> datetime:
    return start + timedelta(hours=hours.get(priority, hours.get("normal", 24)))


async def set_targets(db: AsyncSession, ticket: SupportTicket) -> None:
    p = await policy(db)
    start = ticket.created_at or _now()
    ticket.first_response_due = None if ticket.first_responded_at else _due(start, p["first_response_hours"], ticket.priority)
    ticket.resolve_due = _due(start, p["resolve_hours"], ticket.priority)


async def open_ticket(db: AsyncSession, *, subject: str, body: str, category: str, requester_email: str, requester_name: str,
                      channel: str, tenant_id: Optional[str] = None, template_id: Optional[str] = None, opened_by: Optional[str] = None,
                      author_kind: str = "customer", author_name: Optional[str] = None, priority: str = "normal",
                      related: Optional[Dict[str, str]] = None, unverified: bool = False, email_message_id: Optional[str] = None,
                      files: Sequence[Tuple[Optional[str], bytes]] = ()) -> Tuple[SupportTicket, SupportMessage]:
    """A new ticket and its first message. The caller commits, then sends the confirmation (mail.ticket_opened)."""
    category = category if category in CATEGORIES else "other"
    priority = priority if priority in PRIORITIES else "normal"
    now = _now()
    ticket = SupportTicket(number=f"T-{1000 + await next_number(db, 'support_ticket')}", tenant_id=tenant_id, template_id=template_id,
                           opened_by=opened_by, requester_email=requester_email.strip().lower(), requester_name=requester_name.strip()[:255] or requester_email,
                           subject=(subject.strip() or "Support request")[:255], category=category, priority=priority, status="new", channel=channel,
                           unverified=unverified, related=related or {}, created_at=now, last_customer_at=now)
    db.add(ticket)
    await db.flush()
    await set_targets(db, ticket)
    message = await add_message(db, ticket, body=body, author_kind=author_kind, author_name=author_name or ticket.requester_name,
                                author_id=opened_by, via="email" if channel == "email" else ("agent" if channel == "agent" else "app"),
                                email_message_id=email_message_id, files=files, opening=True)
    return ticket, message


async def add_message(db: AsyncSession, ticket: SupportTicket, *, body: str, author_kind: str, author_name: str, via: str,
                      author_id: Optional[str] = None, internal: bool = False, email_message_id: Optional[str] = None,
                      files: Sequence[Tuple[Optional[str], bytes]] = (), opening: bool = False) -> SupportMessage:
    """One message on the ticket, its files checked and kept, and the ticket moved along."""
    if len(files) > MAX_FILES:
        raise ValueError(f"Send at most {MAX_FILES} files with a message.")
    checked = [(name, data, *check_file(data)) for name, data in files]  # refuse the whole message before anything is kept
    now = _now()
    message = SupportMessage(ticket_id=ticket.id, author_kind=author_kind, author_id=author_id, author_name=author_name[:255],
                             body=body.strip(), internal=internal, via=via, email_message_id=email_message_id, created_at=now)
    db.add(message)
    await db.flush()
    for name, data, content_type, ext in checked:
        f = SupportFile(ticket_id=ticket.id, message_id=message.id, filename=_clean_name(name, ext), content_type=content_type,
                        size=len(data), sha256=hashlib.sha256(data).hexdigest(), object_key="")
        db.add(f)
        await db.flush()
        f.object_key = f"support/{ticket.id}/{f.id}.{ext}"
        await asyncio.to_thread(storage_service.upload_file, f.object_key, data, content_type)
    if internal:
        return message
    if author_kind in ("customer", "agent") and not opening:
        ticket.last_customer_at = now
        if ticket.status in ("pending", "solved"):
            ticket.status, ticket.solved_at = "open", None
    elif author_kind == "staff":
        ticket.last_staff_at = now
        if ticket.first_responded_at is None:
            ticket.first_responded_at = now
            ticket.first_response_due = None
        if ticket.status in ("new", "open"):
            ticket.status = "pending"
    ticket.updated_at = now
    return message


def check_file(data: bytes) -> Tuple[str, str]:
    """(content type, extension) of a file a ticket may carry, or ValueError saying what is wrong with it."""
    import filetype
    if not data:
        raise ValueError("That file is empty.")
    if len(data) > MAX_BYTES:
        raise ValueError(f"A file can be up to {MAX_BYTES // (1024 * 1024)} MB.")
    kind = filetype.guess(data)
    if kind is None or kind.mime not in ALLOWED:
        raise ValueError("Attach a PDF or a picture (JPEG, PNG or WebP).")
    return kind.mime, ALLOWED[kind.mime]


def _clean_name(name: Optional[str], ext: str) -> str:
    base = os.path.basename((name or "").replace("\\", "/")).strip() or f"file.{ext}"
    return "".join(c for c in base if c.isprintable() and c not in '"<>')[:200] or f"file.{ext}"


def set_status(ticket: SupportTicket, status: str) -> None:
    ticket.status = status
    ticket.solved_at = _now() if status in ("solved", "closed") else None
    ticket.updated_at = _now()


def visible(stmt: Select, *, tenant_id: str, user_id: str, org_role: Optional[str]) -> Select:
    """Only what this person may see: theirs, or, as an owner or admin, every ticket of the organization."""
    stmt = stmt.where(SupportTicket.tenant_id == tenant_id, SupportTicket.merged_into.is_(None))
    if org_role in ORG_MANAGERS:
        return stmt
    return stmt.where(SupportTicket.opened_by == user_id)


async def messages_of(db: AsyncSession, ticket_id: str, *, internal: bool) -> List[SupportMessage]:
    stmt = select(SupportMessage).where(SupportMessage.ticket_id == ticket_id).order_by(SupportMessage.created_at)
    if not internal:
        stmt = stmt.where(SupportMessage.internal.is_(False))
    return list((await db.execute(stmt)).scalars().all())


async def files_of(db: AsyncSession, ticket_id: str) -> Dict[str, List[SupportFile]]:
    rows = (await db.execute(select(SupportFile).where(SupportFile.ticket_id == ticket_id).order_by(SupportFile.created_at))).scalars().all()
    by_message: Dict[str, List[SupportFile]] = {}
    for f in rows:
        by_message.setdefault(f.message_id, []).append(f)
    return by_message


async def read_file(f: SupportFile) -> bytes:
    return await asyncio.to_thread(storage_service.read_object, f.object_key, MAX_BYTES)


def ticket_dict(t: SupportTicket) -> Dict[str, Any]:
    return {"id": t.id, "number": t.number, "subject": t.subject, "category": t.category, "priority": t.priority, "status": t.status,
            "channel": t.channel, "requester_name": t.requester_name, "requester_email": t.requester_email, "tenant_id": t.tenant_id,
            "assignee_id": t.assignee_id, "assignee_name": t.assignee_name, "unverified": t.unverified, "related": t.related or {},
            "first_response_due": t.first_response_due, "resolve_due": t.resolve_due, "first_responded_at": t.first_responded_at,
            "breached_at": t.breached_at, "solved_at": t.solved_at, "created_at": t.created_at, "updated_at": t.updated_at,
            "last_customer_at": t.last_customer_at, "last_staff_at": t.last_staff_at}


def message_dict(m: SupportMessage, files: Sequence[SupportFile] = ()) -> Dict[str, Any]:
    return {"id": m.id, "author_kind": m.author_kind, "author_name": m.author_name, "body": m.body, "internal": m.internal,
            "via": m.via, "created_at": m.created_at,
            "files": [{"id": f.id, "filename": f.filename, "content_type": f.content_type, "size": f.size} for f in files]}


async def find_by_number(db: AsyncSession, number: str) -> Optional[SupportTicket]:
    return (await db.execute(select(SupportTicket).where(SupportTicket.number == number.upper()))).scalar_one_or_none()


def search(stmt: Select, q: Optional[str]) -> Select:
    if not q or not q.strip():
        return stmt
    like = "%" + q.strip().lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    from sqlalchemy import func
    return stmt.where(or_(func.lower(SupportTicket.subject).like(like, escape="\\"), func.lower(SupportTicket.number).like(like, escape="\\"),
                          func.lower(SupportTicket.requester_email).like(like, escape="\\")))
