"""
Ticket email, from the support address: "we've got it" when a ticket opens, each staff answer, and "solved". It goes out
through the mail service's "support" purpose (the account connected for support sends it from its own address).

Every email carries the ticket's tag in its subject ("[#T-1042] …") and a Message-ID with the ticket number in it
(<t-1042.9f3c…@domain>), and answers the earlier ones (In-Reply-To, References): mail programs thread the conversation,
and a customer's reply finds its ticket by either (services/support/mailbox.py). Sending never raises.
"""
import logging
import re
import secrets
from typing import List, Optional
from sqlalchemy import select, update
from app.core.config import settings
from app.services import mail_identity
from app.core.database import AsyncSessionLocal
from app.models.support import SupportMessage, SupportTicket
from app.models.tenant import Tenant
from app.services.notifications import transport
from app.services.notifications.render import email_parts

logger = logging.getLogger("services.support.mail")
TAG = re.compile(r"\[#(T-\d{4,})\]", re.I)
OUR_ID = re.compile(r"<(t-\d{4,})\.[0-9a-f]{8,}@", re.I)


def support_address() -> str:
    """The support mailbox's address, as the mail service last told us (mail_identity.ensure asks it)."""
    return mail_identity.current().support_address


def _domain() -> str:
    return support_address().rpartition("@")[2] or "syruss.local"


def new_message_id(ticket: SupportTicket) -> str:
    return f"<{ticket.number.lower()}.{secrets.token_hex(8)}@{_domain()}>"


def subject_of(ticket: SupportTicket, reply: bool) -> str:
    return f"{'Re: ' if reply else ''}[#{ticket.number}] {ticket.subject}"


async def ticket_url(ticket: SupportTicket) -> str:
    async with AsyncSessionLocal() as db:
        tenant = await db.get(Tenant, ticket.tenant_id) if ticket.tenant_id else None
    if tenant is None:
        return ""
    return f"{settings.PUBLIC_APP_URL.rstrip('/')}/{tenant.slug}/settings/help/tickets?ticket={ticket.id}"


async def _thread(ticket_id: str) -> List[str]:
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(SupportMessage.email_message_id).where(
            SupportMessage.ticket_id == ticket_id, SupportMessage.email_message_id.is_not(None))
            .order_by(SupportMessage.created_at))).scalars().all()
    return [r for r in rows if r]


async def send(ticket: SupportTicket, template: str, *, message: Optional[SupportMessage] = None, reply: bool = False) -> bool:
    """One ticket email to the requester (`template`: support_ticket_opened, support_reply or support_ticket_solved)."""
    try:
        await mail_identity.ensure(required=True)  # the address in the email and at the end of its ids: never a guess
        refs = await _thread(ticket.id)
        # Our own id: kept on a staff answer (so later mail threads under it); otherwise a new one (never the customer's)
        own_id = message.email_message_id if reply and message is not None and message.email_message_id else new_message_id(ticket)
        refs = [r for r in refs if r != own_id]
        parts = email_parts(template, {
            "ticket": ticket.number, "subject": ticket.subject, "name": ticket.requester_name, "to": ticket.requester_email,
            "body": message.body if message is not None else "", "author": message.author_name if message is not None else "",
            "url": await ticket_url(ticket), "support": support_address()})
        if message is not None and not message.email_message_id:  # the first email about this message: later ones answer it
            async with AsyncSessionLocal() as db:
                await db.execute(update(SupportMessage).where(SupportMessage.id == message.id, SupportMessage.email_message_id.is_(None))
                                 .values(email_message_id=own_id))
                await db.commit()
        await transport.send(transport.EmailMessage(
            to=ticket.requester_email, subject=subject_of(ticket, reply), text=parts["text"], html=parts["html"],
            purpose="support", reply_to=support_address(), message_id=own_id,
            in_reply_to=refs[-1] if refs else None, references=refs or None, automatic=template != "support_reply",
            meta={"ticket": ticket.number, "template": template}))
        return True
    except Exception as exc:  # noqa: BLE001  (the ticket stands; the email is a courtesy)
        logger.warning("Ticket email %s for %s failed: %s", template, ticket.number, exc)
        return False
