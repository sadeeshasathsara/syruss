"""
Documents sent by e-mail. A channel of kind "email" has its own address (<token>@INTAKE_EMAIL_DOMAIN); each image attached to a message
sent there becomes a document of that channel, exactly as if it had been sent through the API. This is a small SMTP receiver that runs inside
the API process (port INTAKE_SMTP_PORT) and only accepts mail for those addresses, so it is never an open relay.

What it does and does not check:
  * the recipient must be a live channel address, one channel per message;
  * the channel's allowed senders (addresses or @domains) are compared with the message's From header;
  * a channel can require that the mail gateway in front of this receiver has vouched for the sender (an Authentication-Results header with
    spf=pass or dkim=pass). This receiver does not verify SPF or DKIM itself: put it behind a mail gateway that does, and turn that on;
  * only attached files count (not images in the body or the signature), and every one is held to the same rules as any other document.
Mail that is refused gets a permanent error that says why, so the sender sees it in their bounce.
"""
import asyncio
import hashlib
import logging
import re
from email import message_from_bytes, policy
from email.message import EmailMessage
from email.utils import parseaddr
from typing import List, Optional, Tuple
from aiosmtpd.smtp import SMTP, Envelope, Session
from sqlalchemy import select
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.intake import IntakeChannel
from app.services import intake_service
from app.services.intake_rules import IntakeRejected

logger = logging.getLogger("core.intake.email")
MAX_MESSAGE_BYTES = 40_000_000


def address_for(channel: IntakeChannel) -> Optional[str]:
    token = (channel.config or {}).get("email_token")
    return f"{token}@{settings.INTAKE_EMAIL_DOMAIN}" if token and channel.kind == "email" and settings.INTAKE_EMAIL_DOMAIN else None


async def channel_for_token(token: str) -> Optional[IntakeChannel]:
    async with AsyncSessionLocal() as db:
        return (await db.execute(select(IntakeChannel).where(IntakeChannel.kind == "email", IntakeChannel.config["email_token"].as_string() == token))).scalar_one_or_none()


def sender_allowed(address: str, allowlist: List[str]) -> bool:
    """An empty list allows anyone. Entries are full addresses or @domains."""
    if not allowlist:
        return True
    address = address.lower()
    return any(address == a or (a.startswith("@") and address.endswith(a)) or (not a.startswith("@") and "@" not in a and address.endswith("@" + a)) for a in allowlist)


def vouched_for(msg: EmailMessage) -> bool:
    """Whether a mail gateway upstream recorded that the sender's domain passed SPF or DKIM."""
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


def smtp_answer(rejected: IntakeRejected) -> str:
    reason = re.sub(r"\s+", " ", rejected.reason)[:200]
    if rejected.status == 413:
        return f"552 5.3.4 {reason}"
    if rejected.status in (429, 503):
        return f"451 4.7.1 {reason} Try again later."
    return f"554 5.7.1 {reason}"


class IntakeHandler:
    async def handle_RCPT(self, server, session: Session, envelope: Envelope, address: str, rcpt_options) -> str:
        local, _, domain = address.lower().rpartition("@")
        if domain != settings.INTAKE_EMAIL_DOMAIN.lower():
            return "550 5.7.1 Relaying is not allowed"
        channel = await channel_for_token(local)
        if channel is None:
            return "550 5.1.1 No such mailbox"
        if channel.status != "active":
            return "550 5.7.1 This mailbox is paused"
        if envelope.rcpt_tos:
            return "452 4.5.3 Send to one mailbox at a time"
        envelope.rcpt_tos.append(address)
        envelope.channel_id = channel.id          # type: ignore[attr-defined]
        return "250 OK"

    async def handle_DATA(self, server, session: Session, envelope: Envelope) -> str:
        channel_id = getattr(envelope, "channel_id", None)
        async with AsyncSessionLocal() as db:
            channel = await db.get(IntakeChannel, channel_id) if channel_id else None
        if channel is None:
            return "550 5.1.1 No such mailbox"
        try:
            msg = message_from_bytes(envelope.content, policy=policy.default)
            sender = parseaddr(str(msg.get("From") or ""))[1] or envelope.mail_from or "unknown"
            cfg = channel.config or {}
            who = intake_service.Sender(label=sender[:255], via="email", ip=(session.peer[0] if session.peer else ""))
            where = {"tenant_id": channel.tenant_id, "template_id": channel.template_id, "channel_id": channel.id}
            if not sender_allowed(sender, cfg.get("sender_allowlist") or []):
                await intake_service.reject(**where, sender=who, reason=f"{sender} is not one of the senders this channel accepts.")
                return "550 5.7.1 This mailbox does not accept mail from your address"
            if cfg.get("require_authentication") and not vouched_for(msg):
                await intake_service.reject(**where, sender=who, reason=f"The mail from {sender} was not vouched for by SPF or DKIM.")
                return "550 5.7.26 This mailbox only accepts mail that passes SPF or DKIM"
            files = attachments_of(msg)
            if not files:
                await intake_service.reject(**where, sender=who, reason="The message has no attached file. Attach the document as an image file.")
                return "554 5.6.0 No attached file found. Attach the document as an image file (JPG, PNG, TIFF or WebP)"
            message_id = re.sub(r"[^\w .:/#@+=,-]", "", str(msg.get("Message-ID") or "")).strip()[:150] or hashlib.sha1(envelope.content).hexdigest()
            meta = {"subject": str(msg.get("Subject") or "")[:200], "from": sender[:200]}
            for index, (name, data) in enumerate(files, start=1):
                await intake_service.ingest(channel.id, content=data, filename=name, sender=who, external_id=f"{message_id}#{index}", metadata=meta)
        except IntakeRejected as rejected:
            return smtp_answer(rejected)
        except Exception:  # noqa: BLE001
            logger.exception("Could not take in a message for channel %s", channel_id)
            return "451 4.3.0 Could not be processed right now. Try again later"
        return "250 OK, received"


class EmailReceiver:
    def __init__(self) -> None:
        self._server: Optional[asyncio.AbstractServer] = None
        self.port: Optional[int] = None

    async def start(self) -> None:
        """Begins listening when the server is set up for e-mail intake (INTAKE_SMTP_PORT)."""
        if settings.INTAKE_SMTP_PORT:
            await self.listen(settings.INTAKE_SMTP_HOST, settings.INTAKE_SMTP_PORT)

    async def listen(self, host: str, port: int) -> int:
        """Listens on this address (port 0: any free one) and returns the port."""
        if self._server is None:
            handler = IntakeHandler()
            self._server = await asyncio.get_running_loop().create_server(
                lambda: SMTP(handler, hostname=settings.INTAKE_EMAIL_DOMAIN, data_size_limit=MAX_MESSAGE_BYTES, enable_SMTPUTF8=True, ident="Syruss intake"), host, port)
            self.port = self._server.sockets[0].getsockname()[1]
            logger.info("E-mail intake listening on port %s for @%s", self.port, settings.INTAKE_EMAIL_DOMAIN)
        return self.port or 0

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None


email_receiver = EmailReceiver()
