"""
Help & support, the customer's side: the tickets a person may see, opening one, the conversation on it, closing it.

A person sees the tickets they opened; an organization's owners and admins see all of its tickets (services/support/
tickets.visible). Staff notes on a ticket are never shown here. Staff answer from the admin app (api/v1/sysadmin/
support.py), and everything is also mailed from the support address, so the conversation can go on by email.
"""
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core import ratelimit
from app.core.auth import AuthContext, get_current_auth
from app.core.database import get_db_session
from app.core.downloads import content_disposition
from app.models.identity import User
from app.models.support import CATEGORIES, SupportFile, SupportMessage, SupportTicket
from app.services.support import mail, tickets

router = APIRouter(prefix="/support", tags=["Support"])


def _person(auth: AuthContext) -> AuthContext:
    if auth.user_id.startswith("apikey:"):
        raise HTTPException(status_code=403, detail="Support is for people, not API keys.")
    return auth


async def _ticket(db: AsyncSession, auth: AuthContext, ticket_id: str) -> SupportTicket:
    stmt = tickets.visible(select(SupportTicket), tenant_id=auth.tenant_id, user_id=auth.user_id, org_role=auth.org_role)
    ticket = (await db.execute(stmt.where(SupportTicket.id == ticket_id))).scalar_one_or_none()
    if ticket is None:
        raise HTTPException(status_code=404, detail="No such ticket.")
    return ticket


async def _files(uploads: Optional[List[UploadFile]]) -> List[tuple]:
    out = []
    for f in (uploads or [])[:tickets.MAX_FILES + 1]:
        out.append((f.filename, await f.read(10 * 1024 * 1024 + 1)))  # one byte over the limit is enough to refuse it
    return out


@router.get("/tickets")
async def my_tickets(status: Optional[str] = Query(None, pattern="^(active|done)$"), auth: AuthContext = Depends(get_current_auth),
                     db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    """The tickets you may see, newest activity first. active: still being worked on; done: solved or closed."""
    _person(auth)
    stmt = tickets.visible(select(SupportTicket), tenant_id=auth.tenant_id, user_id=auth.user_id, org_role=auth.org_role)
    if status == "active":
        stmt = stmt.where(SupportTicket.status.in_(("new", "open", "pending")))
    elif status == "done":
        stmt = stmt.where(SupportTicket.status.in_(("solved", "closed")))
    rows = (await db.execute(stmt.order_by(SupportTicket.updated_at.desc()).limit(100))).scalars().all()
    return {"items": [tickets.ticket_dict(t) for t in rows], "categories": list(CATEGORIES), "can_see_all": auth.org_role in tickets.ORG_MANAGERS}


@router.post("/tickets", status_code=201)
async def open_ticket(subject: str = Form(..., min_length=3, max_length=200), body: str = Form(..., min_length=5, max_length=20000),
                      category: str = Form("other"), files: Optional[List[UploadFile]] = File(None),
                      auth: AuthContext = Depends(get_current_auth), db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    """Opens a ticket for you in this organization. You get a confirmation email, and replies by email and here."""
    _person(auth)
    ratelimit.limit(f"support-open:{auth.user_id}", 10, 3600)
    if category not in CATEGORIES:
        raise HTTPException(status_code=422, detail=f"Category is one of: {', '.join(CATEGORIES)}.")
    user = await db.get(User, auth.user_id)
    try:
        ticket, first = await tickets.open_ticket(db, subject=subject, body=body, category=category, channel="app", tenant_id=auth.tenant_id,
                                              opened_by=auth.user_id, requester_email=(user.email if user else auth.email) or "",
                                              requester_name=(user.display_name if user else auth.username) or "", files=await _files(files))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await db.commit()
    await mail.send(ticket, "support_ticket_opened", message=first)
    return tickets.ticket_dict(ticket)


@router.get("/tickets/{ticket_id}")
async def ticket(ticket_id: str, auth: AuthContext = Depends(get_current_auth), db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    t = await _ticket(db, _person(auth), ticket_id)
    files = await tickets.files_of(db, t.id)
    return {**tickets.ticket_dict(t), "messages": [tickets.message_dict(m, files.get(m.id, ())) for m in await tickets.messages_of(db, t.id, internal=False)]}


@router.post("/tickets/{ticket_id}/messages", status_code=201)
async def reply(ticket_id: str, body: str = Form(..., min_length=1, max_length=20000), files: Optional[List[UploadFile]] = File(None),
                auth: AuthContext = Depends(get_current_auth), db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    """Adds to the ticket (it opens again if it was solved). A closed ticket takes no more replies: open a new one."""
    t = await _ticket(db, _person(auth), ticket_id)
    ratelimit.limit(f"support-reply:{auth.user_id}", 60, 3600)
    if t.status == "closed":
        raise HTTPException(status_code=409, detail="This ticket is closed. Open a new one, and mention its number.")
    user = await db.get(User, auth.user_id)
    try:
        m = await tickets.add_message(db, t, body=body, author_kind="customer", author_id=auth.user_id,
                                      author_name=(user.display_name if user else auth.username) or "You", via="app", files=await _files(files))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    await db.commit()
    from app.services.support.mailbox import _tell_assignee
    await _tell_assignee(t, m.author_name)
    return tickets.message_dict(m, (await tickets.files_of(db, t.id)).get(m.id, ()))


@router.post("/tickets/{ticket_id}/close")
async def close(ticket_id: str, auth: AuthContext = Depends(get_current_auth), db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    """You don't need it any more: it's marked solved (a reply opens it again)."""
    t = await _ticket(db, _person(auth), ticket_id)
    if t.status not in ("solved", "closed"):
        user = await db.get(User, auth.user_id)
        await tickets.add_message(db, t, body="Marked solved by the customer.", author_kind="system",
                                  author_name=(user.display_name if user else auth.username) or "The customer", via="app")
        tickets.set_status(t, "solved")
        await db.commit()
    return tickets.ticket_dict(t)


@router.get("/tickets/{ticket_id}/files/{file_id}")
async def ticket_file(ticket_id: str, file_id: str, auth: AuthContext = Depends(get_current_auth), db: AsyncSession = Depends(get_db_session)):
    t = await _ticket(db, _person(auth), ticket_id)
    f = await db.get(SupportFile, file_id)
    if f is None or f.ticket_id != t.id:
        raise HTTPException(status_code=404, detail="No such file.")
    if (await db.get(SupportMessage, f.message_id)).internal:
        raise HTTPException(status_code=404, detail="No such file.")
    return file_response(f, await tickets.read_file(f))


def file_response(f: SupportFile, data: bytes) -> Response:
    """A picture is shown, unable to run anything; a PDF is downloaded. Never read as anything but its own type."""
    headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
    if f.content_type == "application/pdf":
        headers["Content-Disposition"] = content_disposition("attachment", f.filename)
    else:
        headers["Content-Disposition"] = content_disposition("inline", f.filename)
        headers["Content-Security-Policy"] = "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; sandbox"
    return Response(content=data, media_type=f.content_type, headers=headers)
