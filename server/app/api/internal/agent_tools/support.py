"""
The agent opening a support ticket for the person it is chatting with, when it can't solve their problem itself.

Only in a chat a person started (never in work runs, and never for anyone else): the ticket is theirs, in their
organization, with the agent's summary as its first message and the documents or invoice it is about attached as
context. They get the confirmation email and see the ticket under Help & support.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from app.models.billing import Invoice
from app.models.identity import User
from app.models.outbox import AgentEventOutbox
from app.models.support import CATEGORIES, SupportTicket
from app.services.display_names import name_documents
from app.services.support import mail, tickets
from app.api.internal.agent_tools.common import audit, document_in, on_a_persons_request
from app.api.internal.agent_tools.scope import Workspace, get_workspace

router = APIRouter()
PER_DAY = 3  # tickets the agent may open for one person in a day


class TicketBody(BaseModel):
    subject: str = Field(..., min_length=5, max_length=200, description="The problem in a few words, as the person would put it")
    summary: str = Field(..., min_length=20, max_length=6000, description=(
        "For the support team: what the person asked, what you checked and found, what you already tried, and what they need. "
        "Name documents and invoices as people know them."))
    category: str = Field("other", description="billing, account, technical or other")
    document_id: Optional[str] = Field(None, description="A document of this workspace the problem is about")
    invoice_number: Optional[str] = Field(None, description="An invoice of their organization the problem is about (SYR-2026-00042)")


@router.post("/support/tickets", status_code=201)
async def open_ticket(body: TicketBody, ws: Workspace = Depends(get_workspace)):
    """Opens a support ticket for the person in this chat. Tell them its number, and that answers come by email and under Help & support."""
    actor = await on_a_persons_request(ws)
    if actor is None:
        raise HTTPException(status_code=403, detail="Only in a chat a person started: a ticket is opened for the person who asked.")
    person = await ws.db.get(User, actor.get("id"))
    if person is None or not person.email:
        raise HTTPException(status_code=403, detail="This person has no email address to answer them at.")
    since = datetime.now(timezone.utc) - timedelta(days=1)
    today = await ws.db.scalar(select(func.count(SupportTicket.id)).where(
        SupportTicket.opened_by == person.id, SupportTicket.channel == "agent", SupportTicket.created_at > since))
    if (today or 0) >= PER_DAY:
        raise HTTPException(status_code=429, detail=f"{PER_DAY} tickets were opened for them today already: point them to Help & support instead.")
    event = await ws.db.get(AgentEventOutbox, ws.event_id) if ws.event_id else None
    related = {"conversation_id": (event.payload or {}).get("conversation_id")} if event else {}
    if body.document_id:
        await document_in(ws, body.document_id)
        related["document_id"] = body.document_id
    if body.invoice_number:
        invoice = (await ws.db.execute(select(Invoice).where(Invoice.number == body.invoice_number.strip(),
                                                             Invoice.tenant_id == ws.tenant_id))).scalar_one_or_none()
        if invoice is None:
            raise HTTPException(status_code=404, detail="No invoice with that number in their organization.")
        related["invoice_id"] = invoice.id
    summary = await name_documents(ws.db, ws.template_id, body.summary)
    ticket, first = await tickets.open_ticket(
        ws.db, subject=body.subject, body=summary, category=body.category if body.category in CATEGORIES else "other", channel="agent",
        tenant_id=ws.tenant_id, template_id=ws.template_id, opened_by=person.id, requester_email=person.email,
        requester_name=person.display_name, author_kind="agent", author_name=f"Syruss assistant, for {person.display_name}", related=related)
    audit(ws, "SUPPORT_TICKET_OPENED", "SupportTicket", ticket.id, {"number": ticket.number, "for": person.display_name})
    await ws.db.commit()
    await mail.send(ticket, "support_ticket_opened", message=first)
    return {"number": ticket.number, "status": ticket.status, "where": "Settings → Help & support",
            "note": f"Tell them ticket {ticket.number} is open, that the Syruss team answers by email and under Help & support, "
                    "and that they can reply by email too."}
