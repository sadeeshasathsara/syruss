"""
Support, the staff's side: the ticket inbox, a ticket with the customer's context, answering (by email and in the app),
internal notes, assigning, status and priority, merging duplicates, saved replies, and the service targets.

Anyone on the staff can read; answering and changing tickets takes support.respond (support, billing and owners), and
the targets are an owner's (staff.manage). Tickets are messages to Syruss support, so reading them needs no access
grant; the customer's context shows only what staff already see elsewhere (their organization, plan and bills).
Every change is in the platform audit log; answers and status changes also show in the organization's activity.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.v1.support import file_response
from app.api.v1.sysadmin.access import Staff, record, require_staff
from app.core.database import get_db_session
from app.core.platform_permissions import PLATFORM_GRANTS
from app.models.audit import AuditLog
from app.models.billing import Invoice
from app.models.conversation import Conversation
from app.models.identity import User
from app.models.platform import PlatformStaff
from app.models.support import CATEGORIES, PRIORITIES, STATUSES, SupportFile, SupportSavedReply, SupportTicket
from app.models.tenant import Tenant
from app.services import platform_controls
from app.services.billing import invoices as invoice_service
from app.services.support import mail, tickets

router = APIRouter(prefix="/support")
VIEW = Depends(require_staff())
RESPOND = require_staff("support.respond")
ACTIVE = ("new", "open", "pending")


async def _ticket(db: AsyncSession, ticket_id: str) -> SupportTicket:
    t = await db.get(SupportTicket, ticket_id)
    if t is None:
        raise HTTPException(status_code=404, detail="No such ticket.")
    return t


@router.get("/summary", dependencies=[VIEW])
async def summary(staff: Staff = Depends(require_staff()), db: AsyncSession = Depends(get_db_session)) -> Dict[str, int]:
    """Counts for the navigation and the inbox tabs."""
    count = lambda *where: db.scalar(select(func.count(SupportTicket.id)).where(SupportTicket.merged_into.is_(None), *where))  # noqa: E731
    active = SupportTicket.status.in_(ACTIVE)
    return {"new": await count(SupportTicket.status == "new"), "unassigned": await count(active, SupportTicket.assignee_id.is_(None)),
            "mine": await count(active, SupportTicket.assignee_id == staff.user_id), "breached": await count(active, SupportTicket.breached_at.is_not(None)),
            "active": await count(active),
            "live_waiting": int(await db.scalar(select(func.count(Conversation.id)).where(Conversation.support_state == "waiting")) or 0)}


@router.get("/tickets", dependencies=[VIEW])
async def inbox(status: str = Query("active", pattern="^(active|new|open|pending|solved|closed|all)$"),
                assignee: Optional[str] = Query(None, max_length=100), priority: Optional[str] = Query(None, pattern="^(low|normal|high|urgent)$"),
                category: Optional[str] = Query(None, pattern="^(billing|account|technical|other)$"), breached: bool = False,
                tenant_id: Optional[str] = Query(None, max_length=64), q: Optional[str] = Query(None, max_length=100),
                limit: int = Query(100, ge=1, le=300), staff: Staff = Depends(require_staff()), db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    """Tickets, the most urgent and oldest-waiting first: breached, then by priority, then by when the customer last wrote."""
    stmt = select(SupportTicket).where(SupportTicket.merged_into.is_(None))
    if status == "active":
        stmt = stmt.where(SupportTicket.status.in_(ACTIVE))
    elif status != "all":
        stmt = stmt.where(SupportTicket.status == status)
    if assignee == "me":
        stmt = stmt.where(SupportTicket.assignee_id == staff.user_id)
    elif assignee == "none":
        stmt = stmt.where(SupportTicket.assignee_id.is_(None))
    elif assignee:
        stmt = stmt.where(SupportTicket.assignee_id == assignee)
    for column, value in ((SupportTicket.priority, priority), (SupportTicket.category, category), (SupportTicket.tenant_id, tenant_id)):
        if value:
            stmt = stmt.where(column == value)
    if breached:
        stmt = stmt.where(SupportTicket.breached_at.is_not(None))
    stmt = tickets.search(stmt, q)
    order = case({p: n for n, p in enumerate(reversed(PRIORITIES))}, value=SupportTicket.priority, else_=9)
    rows = (await db.execute(stmt.order_by(SupportTicket.breached_at.is_(None), order, SupportTicket.last_customer_at.asc().nulls_last())
                             .limit(limit))).scalars().all()
    names = {t.id: t.name for t in (await db.execute(select(Tenant).where(Tenant.id.in_({r.tenant_id for r in rows if r.tenant_id})))).scalars()}
    return {"items": [{**tickets.ticket_dict(t), "organization": names.get(t.tenant_id)} for t in rows]}


async def customer_context(db: AsyncSession, tenant_id: Optional[str], person_id: Optional[str]) -> Dict[str, Any]:
    """Who the customer is, from what staff already see elsewhere: their organization and plan, the person, recent bills."""
    tenant = await db.get(Tenant, tenant_id) if tenant_id else None
    person = await db.get(User, person_id) if person_id else None
    bills, plan = [], None
    if tenant:
        from app.services.billing import service as billing
        for i in (await db.execute(select(Invoice).where(Invoice.tenant_id == tenant.id, Invoice.status != "draft")
                                   .order_by(Invoice.created_at.desc()).limit(5))).scalars():
            bills.append({"id": i.id, "number": i.number, "status": i.status, "total_cents": i.total_cents, "currency": i.currency,
                          "due_cents": invoice_service.amount_due(i), "due_at": i.due_at})
        plan = (await billing.summary(db, tenant.id)).get("plan")
    return {
        "organization": {"id": tenant.id, "name": tenant.name, "slug": tenant.slug, "status": tenant.status, "plan": plan} if tenant else None,
        "person": {"id": person.id, "name": person.display_name, "email": person.email, "status": person.status,
                   "last_seen_at": person.last_seen_at} if person else None,
        "bills": bills,
    }


@router.get("/tickets/{ticket_id}", dependencies=[VIEW])
async def ticket(ticket_id: str, db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    """The whole conversation (internal notes included), its files, and the customer's context."""
    t = await _ticket(db, ticket_id)
    files = await tickets.files_of(db, t.id)
    others = (await db.execute(select(SupportTicket).where(
        SupportTicket.id != t.id, or_(SupportTicket.requester_email == t.requester_email, SupportTicket.tenant_id == t.tenant_id) if t.tenant_id
        else SupportTicket.requester_email == t.requester_email).order_by(SupportTicket.created_at.desc()).limit(5))).scalars().all()
    return {
        **tickets.ticket_dict(t),
        "messages": [tickets.message_dict(m, files.get(m.id, ())) for m in await tickets.messages_of(db, t.id, internal=True)],
        "context": {
            **await customer_context(db, t.tenant_id, t.opened_by),
            "other_tickets": [{"id": o.id, "number": o.number, "subject": o.subject, "status": o.status, "created_at": o.created_at} for o in others],
        },
    }


@router.post("/tickets/{ticket_id}/messages", status_code=201)
async def answer(ticket_id: str, body: str = Form(..., min_length=1, max_length=20000), internal: bool = Form(False),
                 then: Optional[Literal["pending", "solved"]] = Form(None), files: Optional[List[UploadFile]] = File(None),
                 staff: Staff = Depends(RESPOND), db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    """
    An answer to the customer (emailed from the support address, and shown in their app), or, with `internal`, a note
    only staff see. `then` sets the status after an answer: pending (waiting for them, the default) or solved.
    """
    t = await _ticket(db, ticket_id)
    if t.merged_into:
        raise HTTPException(status_code=409, detail="This ticket was merged into another: answer there.")
    uploads = [(f.filename, await f.read(10 * 1024 * 1024 + 1)) for f in (files or [])[:tickets.MAX_FILES + 1]]
    try:
        m = await tickets.add_message(db, t, body=body, author_kind="staff", author_id=staff.user_id, author_name=staff.name,
                                      via="app", internal=internal, files=uploads)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    if not internal:
        m.email_message_id = mail.new_message_id(t)
        if t.assignee_id is None:  # whoever answers first takes it
            t.assignee_id, t.assignee_name = staff.user_id, staff.name
        if then == "solved":
            tickets.set_status(t, "solved")
        if t.tenant_id:
            db.add(AuditLog(tenant_id=t.tenant_id, event_type="SUPPORT_ANSWERED", entity_type="SupportTicket", entity_id=t.id,
                            actor="platform", details={"staff": staff.name, "number": t.number}))
    record(db, staff, "support.noted" if internal else "support.answered", "ticket", t.id, tenant_id=t.tenant_id,
           details={"number": t.number, "then": then})
    await db.commit()
    if not internal:
        await mail.send(t, "support_reply", message=m, reply=True)
    return tickets.message_dict(m, (await tickets.files_of(db, t.id)).get(m.id, ()))


class Change(BaseModel):
    status: Optional[Literal["new", "open", "pending", "solved", "closed"]] = None
    priority: Optional[Literal["low", "normal", "high", "urgent"]] = None
    category: Optional[Literal["billing", "account", "technical", "other"]] = None
    assignee_id: Optional[str] = Field(None, description="A staff member's id, \"me\", or \"none\" to unassign")


@router.patch("/tickets/{ticket_id}")
async def change(ticket_id: str, body: Change, staff: Staff = Depends(RESPOND), db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    t = await _ticket(db, ticket_id)
    before = {"status": t.status, "priority": t.priority, "category": t.category, "assignee": t.assignee_name}
    if body.assignee_id is not None:
        if body.assignee_id == "none":
            t.assignee_id, t.assignee_name = None, None
        else:
            who = staff.user_id if body.assignee_id == "me" else body.assignee_id
            if who != staff.user_id and who not in {s["id"] for s in await responders(db)}:
                raise HTTPException(status_code=422, detail="That person doesn't answer tickets (their role has no support.respond).")
            user = await db.get(User, who)
            t.assignee_id, t.assignee_name = who, (user.display_name if user else staff.name)
    if body.priority and body.priority != t.priority:
        t.priority = body.priority
        await tickets.set_targets(db, t)
    if body.category:
        t.category = body.category
    solved_now = body.status == "solved" and t.status != "solved"
    if body.status and body.status != t.status:
        tickets.set_status(t, body.status)
        await tickets.add_message(db, t, body=f"{staff.name} set the status to {body.status}.", author_kind="system", author_name=staff.name,
                                  via="app", internal=True)
    t.updated_at = datetime.now(timezone.utc)
    after = {"status": t.status, "priority": t.priority, "category": t.category, "assignee": t.assignee_name}
    record(db, staff, "support.changed", "ticket", t.id, tenant_id=t.tenant_id,
           details={"number": t.number, **{k: [before[k], after[k]] for k in after if before[k] != after[k]}})
    await db.commit()
    if solved_now:
        await mail.send(t, "support_ticket_solved")
    return tickets.ticket_dict(t)


class MergeIn(BaseModel):
    into: str = Field(..., min_length=3, max_length=16, description="The number of the ticket to keep (T-1042)")


@router.post("/tickets/{ticket_id}/merge")
async def merge(ticket_id: str, body: MergeIn, staff: Staff = Depends(RESPOND), db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    """A duplicate folded into the ticket that's kept: it closes, and later replies to it land on the kept one."""
    t = await _ticket(db, ticket_id)
    target = await tickets.find_by_number(db, body.into)
    if target is None or target.id == t.id or target.merged_into:
        raise HTTPException(status_code=422, detail="Give the number of another ticket that is still in use.")
    t.merged_into = target.id
    tickets.set_status(t, "closed")
    await tickets.add_message(db, target, body=f"{t.number} ({t.subject}) was merged into this ticket by {staff.name}.",
                              author_kind="system", author_name=staff.name, via="app", internal=True)
    record(db, staff, "support.merged", "ticket", t.id, tenant_id=t.tenant_id, details={"number": t.number, "into": target.number})
    await db.commit()
    return tickets.ticket_dict(target)


@router.get("/tickets/{ticket_id}/files/{file_id}", dependencies=[VIEW])
async def ticket_file(ticket_id: str, file_id: str, db: AsyncSession = Depends(get_db_session)):
    f = await db.get(SupportFile, file_id)
    if f is None or f.ticket_id != ticket_id:
        raise HTTPException(status_code=404, detail="No such file.")
    return file_response(f, await tickets.read_file(f))


async def responders(db: AsyncSession) -> List[Dict[str, str]]:
    roles = [r for r, grants in PLATFORM_GRANTS.items() if "support.respond" in grants]
    rows = (await db.execute(select(PlatformStaff, User).join(User, User.id == PlatformStaff.user_id)
                             .where(PlatformStaff.role.in_(roles), User.status == "active"))).all()
    return [{"id": u.id, "name": u.display_name} for _, u in rows]


@router.get("/staff", dependencies=[VIEW])
async def staff_list(db: AsyncSession = Depends(get_db_session)) -> List[Dict[str, str]]:
    """Who tickets can be assigned to."""
    return await responders(db)


# ---- saved replies -----------------------------------------------------------------------------

class SavedReplyIn(BaseModel):
    title: str = Field(..., min_length=2, max_length=120)
    body: str = Field(..., min_length=2, max_length=10000)
    shortcut: Optional[str] = Field(None, max_length=40, pattern=r"^[a-z0-9-]+$")


def _saved(r: SupportSavedReply) -> Dict[str, Any]:
    return {"id": r.id, "title": r.title, "body": r.body, "shortcut": r.shortcut, "created_by_name": r.created_by_name, "updated_at": r.updated_at}


@router.get("/saved-replies", dependencies=[VIEW])
async def saved_replies(db: AsyncSession = Depends(get_db_session)) -> List[Dict[str, Any]]:
    return [_saved(r) for r in (await db.execute(select(SupportSavedReply).order_by(SupportSavedReply.title))).scalars()]


async def _shortcut_free(db: AsyncSession, shortcut: Optional[str], other_than: Optional[str] = None) -> None:
    if shortcut and (await db.execute(select(SupportSavedReply.id).where(SupportSavedReply.shortcut == shortcut,
                                                                         SupportSavedReply.id != (other_than or "")))).first():
        raise HTTPException(status_code=409, detail=f"/{shortcut} is already used by another saved reply.")


@router.post("/saved-replies", status_code=201)
async def add_saved_reply(body: SavedReplyIn, staff: Staff = Depends(RESPOND), db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    await _shortcut_free(db, body.shortcut)
    r = SupportSavedReply(title=body.title.strip(), body=body.body, shortcut=body.shortcut, created_by_name=staff.name)
    db.add(r)
    await db.flush()
    record(db, staff, "support.saved_reply_added", "saved_reply", r.id, details={"title": r.title})
    await db.commit()
    return _saved(r)


@router.patch("/saved-replies/{reply_id}")
async def edit_saved_reply(reply_id: str, body: SavedReplyIn, staff: Staff = Depends(RESPOND), db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    r = await db.get(SupportSavedReply, reply_id)
    if r is None:
        raise HTTPException(status_code=404, detail="No such saved reply.")
    await _shortcut_free(db, body.shortcut, other_than=r.id)
    r.title, r.body, r.shortcut = body.title.strip(), body.body, body.shortcut
    record(db, staff, "support.saved_reply_changed", "saved_reply", r.id, details={"title": r.title})
    await db.commit()
    return _saved(r)


@router.delete("/saved-replies/{reply_id}", status_code=204)
async def remove_saved_reply(reply_id: str, staff: Staff = Depends(RESPOND), db: AsyncSession = Depends(get_db_session)) -> None:
    r = await db.get(SupportSavedReply, reply_id)
    if r is not None:
        record(db, staff, "support.saved_reply_removed", "saved_reply", r.id, details={"title": r.title})
        await db.delete(r)
        await db.commit()


# ---- service targets ---------------------------------------------------------------------------

class TargetsIn(BaseModel):
    first_response_hours: Dict[Literal["low", "normal", "high", "urgent"], int]
    resolve_hours: Dict[Literal["low", "normal", "high", "urgent"], int]
    auto_solve_days: int = Field(..., ge=1, le=60)
    auto_close_days: int = Field(..., ge=1, le=180)
    reason: str = Field(..., min_length=5, max_length=500)


@router.get("/settings", dependencies=[VIEW])
async def targets(db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    from app.services.mail_bus import bus as mail_bus
    support = mail_bus.overview()["purposes"].get("support")
    if support:
        mailbox = {"address": support["address"], "reading": bool(support["reads"]), "connected": bool(support["routed"])}
    else:
        mailbox = {"address": mail.support_address(), "reading": None, "connected": None}
    return {**await tickets.policy(db), "mailbox": mailbox,
            "categories": list(CATEGORIES), "priorities": list(PRIORITIES), "statuses": list(STATUSES)}


@router.put("/settings")
async def set_targets(body: TargetsIn, staff: Staff = Depends(require_staff("staff.manage", recent=True)),
                      db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    for hours in (body.first_response_hours, body.resolve_hours):
        if set(hours) != set(PRIORITIES) or any(not 1 <= h <= 24 * 30 for h in hours.values()):
            raise HTTPException(status_code=422, detail="Give hours (1 to 720) for every priority: low, normal, high and urgent.")
    value = body.model_dump(exclude={"reason"})
    await platform_controls.save(db, tickets.POLICY, value, by_id=staff.user_id, by_name=staff.name)
    record(db, staff, "support.targets_changed", "platform", tickets.POLICY, reason=body.reason, details=value)
    await db.commit()
    return value
