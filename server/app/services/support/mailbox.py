"""
Mail to the support address: each new email becomes a reply on its ticket, or a new ticket. The mail service reads the
mailbox (whichever account is connected for "support") and hands each new message over (services/notifications/
mail_relay.collect), once.

    handle(db, raw) one email: finds its ticket by the [#T-1042] tag in the subject, else by our Message-IDs in its
                    In-Reply-To / References (services/support/mail.py), else by a customer message id it answers

Who may add to a ticket: its requester, or an owner or admin of its organization. Mail from anyone else, or that
matches no ticket, opens a new one: for an address with a Syruss account, in their organization; for an unknown
address, an "unverified" ticket without a confirmation email (a stranger can't use the support address to send mail
to someone else). Mail whose sender the receiving mail server could not confirm (its Authentication-Results say
DMARC failed, or SPF failed without a passing DKIM signature) is treated like mail from a stranger, since a From address
alone can be forged. Out-of-office and bulk mail, mail from our own addresses, and floods from one sender are dropped.
"""
import logging
import re
from datetime import datetime, timedelta, timezone
from email import message_from_bytes, policy as email_policy
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr
from html import unescape
from typing import List, Optional, Tuple
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.models.identity import OrgMember, User
from app.models.support import SupportMessage, SupportTicket
from app.services import mail_identity
from app.services.support import mail, tickets

logger = logging.getLogger("services.support.mailbox")
MAX_PER_SENDER_HOUR = 30
MAX_BODY = 20000


# ---- one email ------------------------------------------------------------------------------------

QUOTE_STARTS = (re.compile(r"^On .+wrote:\s*$", re.I), re.compile(r"^-{2,}\s*Original Message\s*-{2,}", re.I),
                re.compile(r"^From:\s.+", re.I), re.compile(r"^_{5,}\s*$"), re.compile(r"^Sent from my ", re.I))


def is_automatic(msg: EmailMessage) -> bool:
    auto = (msg.get("Auto-Submitted") or "no").strip().lower()
    precedence = (msg.get("Precedence") or "").strip().lower()
    return auto != "no" or bool(msg.get("X-Autoreply") or msg.get("X-Autorespond")) or precedence in ("bulk", "junk", "list", "auto_reply")


def authenticated(msg: EmailMessage) -> bool:
    """Whether the sender's domain vouched for this email, by the checks the receiving mail server recorded. With no
    record (a server that doesn't check), the address is taken as it is. A failure anywhere wins over a pass, so a
    sender can't vouch for themself by adding a header of their own."""
    results = " ".join(str(h) for h in msg.get_all("Authentication-Results", [])).lower()
    if not results:
        return True
    if "dmarc=fail" in results:
        return False
    if "dmarc=pass" in results:
        return True
    return not ("spf=fail" in results and "dkim=pass" not in results)


def body_of(msg: EmailMessage) -> str:
    """The new text of an email: its plain text (or its HTML as text), without the quoted earlier conversation."""
    part = msg.get_body(preferencelist=("plain", "html"))
    text = ""
    if part is not None:
        text = part.get_content()
        if part.get_content_type() == "text/html":
            text = unescape(re.sub(r"<[^>]+>", " ", re.sub(r"(?is)<(script|style).*?</\1>", " ", text)))
    lines = []
    for line in text.replace("\r\n", "\n").split("\n"):
        if any(p.match(line.strip()) for p in QUOTE_STARTS):
            break
        if line.lstrip().startswith(">"):
            continue
        lines.append(line.rstrip())
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()[:MAX_BODY]


def attachments_of(msg: EmailMessage) -> Tuple[List[Tuple[Optional[str], bytes]], int]:
    """The attached files a ticket may carry (PDFs and pictures, 5 at most), and how many others were left out."""
    keep, left = [], 0
    for part in msg.iter_attachments():
        data = part.get_payload(decode=True) or b""
        try:
            tickets.check_file(data)
        except ValueError:
            left += 1
            continue
        if len(keep) < tickets.MAX_FILES:
            keep.append((part.get_filename(), data))
        else:
            left += 1
    return keep, left


def _clean_subject(subject: str) -> str:
    subject = mail.TAG.sub("", subject or "")
    return re.sub(r"^\s*((re|fwd?|aw|sv)\s*:\s*)+", "", subject, flags=re.I).strip() or "Support request"


async def _ticket_for(db: AsyncSession, msg: EmailMessage) -> Optional[SupportTicket]:
    found = mail.TAG.search(msg.get("Subject") or "")
    if found:
        t = await tickets.find_by_number(db, found.group(1))
        if t:
            return t
    refs = " ".join(filter(None, [msg.get("In-Reply-To"), msg.get("References")]))
    for number in mail.OUR_ID.findall(refs):
        t = await tickets.find_by_number(db, number)
        if t:
            return t
    ids = re.findall(r"<[^<>\s]+>", refs)
    if ids:
        row = (await db.execute(select(SupportMessage.ticket_id).where(SupportMessage.email_message_id.in_(ids)).limit(1))).scalar_one_or_none()
        if row:
            return await db.get(SupportTicket, row)
    return None


async def _may_write(db: AsyncSession, ticket: SupportTicket, sender: str) -> bool:
    if sender == ticket.requester_email:
        return True
    if not ticket.tenant_id:
        return False
    user = (await db.execute(select(User).where(func.lower(User.email) == sender))).scalar_one_or_none()
    if user is None:
        return False
    role = (await db.execute(select(OrgMember.role).where(OrgMember.tenant_id == ticket.tenant_id, OrgMember.user_id == user.id))).scalar_one_or_none()
    return role in tickets.ORG_MANAGERS


async def handle(db: AsyncSession, raw: bytes) -> Optional[SupportTicket]:
    """One email: added to its ticket, or a new ticket. Returns the ticket, or None when the email was dropped."""
    msg = message_from_bytes(raw, policy=email_policy.default)
    name, sender = parseaddr(msg.get("From") or "")
    sender = sender.strip().lower()
    known = await mail_identity.ensure(required=True)  # raises when the mail service never answered: the mail waits and is tried again
    ours = {a.lower() for a in (*known.ours, known.support_address, known.from_address) if a}
    if not sender or "@" not in sender or sender in ours or is_automatic(msg):
        return None
    if any(addr.lower() in ours for _, addr in getaddresses(msg.get_all("Reply-To", []))):
        return None  # mail that would answer back to ourselves
    message_id = (msg.get("Message-ID") or "").strip()[:255] or None
    if message_id and (await db.execute(select(SupportMessage.id).where(SupportMessage.email_message_id == message_id))).first():
        return None  # already taken in
    hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    recent = await db.scalar(select(func.count(SupportMessage.id)).join(SupportTicket, SupportTicket.id == SupportMessage.ticket_id).where(
        SupportMessage.via == "email", SupportMessage.author_kind == "customer", SupportTicket.requester_email == sender,
        SupportMessage.created_at > hour_ago))
    if (recent or 0) >= MAX_PER_SENDER_HOUR:
        logger.warning("Dropped support email from %s: more than %s in an hour", sender, MAX_PER_SENDER_HOUR)
        return None
    body = body_of(msg)
    files, left_out = attachments_of(msg)
    if left_out:
        body += f"\n\n[{left_out} attached file(s) weren't added: only PDFs and pictures up to 10 MB, 5 per message.]"
    if not body and not files:
        return None
    display = (name or sender.split("@")[0]).strip()
    trusted = authenticated(msg)

    ticket = await _ticket_for(db, msg)
    if ticket is not None and ticket.merged_into:
        ticket = await db.get(SupportTicket, ticket.merged_into) or ticket
    if ticket is not None and trusted and await _may_write(db, ticket, sender):
        if ticket.status == "closed":  # closed for good: a new ticket that points to it
            fresh, _, _ = await _new_ticket(db, msg, sender, display, body, files, message_id, trusted, follow_up_of=ticket.number)
            return fresh
        await tickets.add_message(db, ticket, body=body or "(files attached)", author_kind="customer", author_name=display, via="email",
                                  email_message_id=message_id, files=files)
        await db.commit()
        await _tell_assignee(ticket, display)
        return ticket
    fresh, known, first = await _new_ticket(db, msg, sender, display, body, files, message_id, trusted)
    if known:
        await mail.send(fresh, "support_ticket_opened", message=first)
    return fresh


async def _new_ticket(db: AsyncSession, msg: EmailMessage, sender: str, display: str, body: str, files, message_id: Optional[str],
                      trusted: bool, follow_up_of: Optional[str] = None) -> Tuple[SupportTicket, bool, SupportMessage]:
    user = (await db.execute(select(User).where(func.lower(User.email) == sender, User.status == "active"))).scalar_one_or_none() if trusted else None
    tenant_id = None
    if user is not None:
        tenant_id = user.last_org_id or (await db.execute(select(OrgMember.tenant_id).where(OrgMember.user_id == user.id).limit(1))).scalar_one_or_none()
    ticket, first = await tickets.open_ticket(
        db, subject=_clean_subject(msg.get("Subject") or ""), body=body or "(files attached)", category="other", requester_email=sender,
        requester_name=user.display_name if user else display, channel="email", tenant_id=tenant_id, opened_by=user.id if user else None,
        unverified=user is None or tenant_id is None, email_message_id=message_id, files=files,
        related={"follow_up_of": follow_up_of} if follow_up_of else None)
    await db.commit()
    return ticket, user is not None, first


async def _tell_assignee(ticket: SupportTicket, who: str) -> None:
    if not ticket.assignee_id:
        return
    from app.services import staff_service
    from app.core.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        staff = await db.get(User, ticket.assignee_id)
    if staff is not None:
        await staff_service.tell(staff.email, f"{who} replied on {ticket.number}", f"{ticket.subject}: a new reply is waiting.",
                                 path=f"/support/tickets/{ticket.id}")
