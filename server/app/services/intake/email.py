"""
Documents sent by e-mail. A channel of kind "email" has an address of its own, made from the mailbox the mail service reads for the
"document inbound" function's job (set in the admin app, Email › Jobs): for a mailbox docs@example.com the channel's address is docs+<token>@example.com.
People write to that address; the mail service reads the mailbox and hands each message over (services/notifications/mail_relay.py),
and every file attached to a message sent there becomes a document of that channel, exactly as if it had been sent through the API.
This server runs no mail receiver of its own and knows no mail setting: the one mailbox, and the plus-addresses that tell channels
apart, are all there is.

What it does and does not check:
  * the message must be addressed to a live channel's plus-address (To, Cc, Delivered-To or X-Original-To), one channel per message;
  * the channel's allowed senders (addresses or @domains) are compared with the message's From header;
  * a channel can require that the mailbox's provider vouched for the sender (an Authentication-Results header with spf=pass or
    dkim=pass, which Gmail, Outlook and most mail servers add). This code does not verify SPF or DKIM itself;
  * only attached files count (not images in the body or the signature), and every one is held to the same rules as any other document.
A message that is refused is written to the channel's log with the reason, where its owner sees it. Nothing is mailed back to the
sender: the From address of unwanted mail is often forged, so a reply would only reach someone who never wrote.
"""
import hashlib
import logging
import re
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import getaddresses, parseaddr
from typing import List, Optional, Tuple

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.intake import IntakeChannel
from app.services.intake import service as intake_service
from app.services.intake.rules import IntakeRejected
from app.services import mail_uses

logger = logging.getLogger("core.intake.email")
ADDRESSED_TO = ("To", "Cc", "Delivered-To", "X-Original-To", "Envelope-To", "X-Envelope-To")


def mailbox() -> Optional[str]:
    """The address mail for documents arrives at (lowercase), from the mail service's directory: None until a mailbox that can read is
    chosen for the documents job."""
    job = mail_uses.job_entry(mail_uses.DOCUMENTS_INBOUND)
    if not job or not job.get("reads"):
        return None
    address = ((job.get("account") or {}).get("address") or "").strip().lower()
    return address if "@" in address else None


def address_for(channel: IntakeChannel, base: Optional[str] = None) -> Optional[str]:
    """The channel's own address: the documents mailbox with the channel's token after a plus sign."""
    base = base or mailbox()
    token = (channel.config or {}).get("email_token")
    if not (base and token and channel.kind == "email"):
        return None
    local, _, domain = base.rpartition("@")
    return f"{local}+{token}@{domain}"


def token_in(msg: EmailMessage, base: str) -> Optional[str]:
    """The channel token a message was addressed to (the part after the plus sign of the mailbox's address)."""
    local, _, domain = base.rpartition("@")
    for header in ADDRESSED_TO:
        for _, address in getaddresses([str(v) for v in msg.get_all(header, [])]):
            who, _, where = address.strip().lower().rpartition("@")
            if where == domain and who.startswith(local + "+") and len(who) > len(local) + 1:
                return who[len(local) + 1:]
    return None


async def channel_for_token(token: str) -> Optional[IntakeChannel]:
    async with AsyncSessionLocal() as db:
        return (await db.execute(select(IntakeChannel).where(IntakeChannel.kind == "email", IntakeChannel.config["email_token"].as_string() == token))).scalar_one_or_none()


def sender_allowed(address: str, allowlist: List[str]) -> bool:
    """An empty list allows anyone. Entries are full addresses or @domains."""
    if not allowlist:
        return True
    address = address.lower()
    allowlist = [a.strip().lower() for a in allowlist if a.strip()]
    return any(address == a or (a.startswith("@") and address.endswith(a)) or (not a.startswith("@") and "@" not in a and address.endswith("@" + a)) for a in allowlist)


def vouched_for(msg: EmailMessage) -> bool:
    """Whether the mailbox's provider recorded that the sender's domain passed SPF or DKIM."""
    results = " ".join(str(v) for v in msg.get_all("Authentication-Results", []))
    return bool(re.search(r"\b(spf|dkim)=pass\b", results, re.I))


def attachments_of(msg: EmailMessage) -> List[Tuple[str, bytes]]:
    """(file name, bytes) of each attached file. Pictures inside the body or signature (inline, or with a Content-ID) are not attachments."""
    found = []
    for part in msg.walk():
        if part.is_multipart() or part.get_content_maintype() == "text" and not part.get_filename():
            continue
        disposition = part.get_content_disposition()
        if disposition == "inline" or (disposition is None and part.get("Content-ID")):
            continue
        data = part.get_payload(decode=True)
        if data:
            found.append((part.get_filename() or f"attachment-{len(found) + 1}", data))
    return found


async def take_in(raw: bytes) -> str:
    """Turns one email read from the documents mailbox into documents. Returns what happened, in a word, for the logs: "ignored" (not
    for a channel), "refused" (a rule said no; it is in the channel's log) or "accepted". Raises when it couldn't be done now."""
    base = mailbox()
    if base is None:
        raise RuntimeError("The documents mailbox isn't known yet.")
    msg = message_from_bytes(raw, policy=policy.default)
    token = token_in(msg, base)
    channel = await channel_for_token(token) if token else None
    if channel is None:
        logger.info("Mail for the documents mailbox wasn't addressed to a channel's address; ignored")
        return "ignored"
    if channel.status != "active":
        logger.info("Mail for the paused channel %s was ignored", channel.id)
        return "ignored"
    sender = parseaddr(str(msg.get("From") or ""))[1] or "unknown"
    cfg = channel.config or {}
    who = intake_service.Sender(label=sender[:255], via="email", ip="")
    where = {"tenant_id": channel.tenant_id, "template_id": channel.template_id, "channel_id": channel.id}
    if not sender_allowed(sender, cfg.get("sender_allowlist") or []):
        await intake_service.reject(**where, sender=who, reason=f"{sender} is not one of the senders this channel accepts.")
        return "refused"
    if cfg.get("require_authentication") and not vouched_for(msg):
        await intake_service.reject(**where, sender=who, reason=f"The mail from {sender} was not vouched for by SPF or DKIM.")
        return "refused"
    files = attachments_of(msg)
    if not files:
        await intake_service.reject(**where, sender=who, reason="The message has no attached file. Attach the document as a PDF or an image file.")
        return "refused"
    message_id = re.sub(r"[^\w .:/#@+=,-]", "", str(msg.get("Message-ID") or "")).strip()[:150] or hashlib.sha1(raw).hexdigest()
    meta = {"subject": str(msg.get("Subject") or "")[:200], "from": sender[:200]}
    outcome = "accepted"
    for index, (name, data) in enumerate(files, start=1):
        try:
            await intake_service.ingest(channel.id, content=data, filename=name, sender=who, external_id=f"{message_id}#{index}", metadata=meta)
        except IntakeRejected:
            outcome = "refused"   # the rules said no to this file; ingest recorded why in the channel's log
    return outcome
