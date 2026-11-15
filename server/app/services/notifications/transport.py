"""
Sending email. One function, `send`, takes a finished message and hands it to the configured backend; everything that
can go wrong with a mail server comes back as an `EmailError` with a message that is safe to show a person.
"""
import asyncio
import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage as MimeMessage
from email.utils import formataddr, formatdate, make_msgid
from typing import List, Optional, Tuple
from app.core.config import settings

logger = logging.getLogger(__name__)


class EmailError(Exception):
    """The message could not be delivered to the mail server."""


@dataclass
class EmailMessage:
    to: str
    subject: str
    text: str
    html: str
    reply_to: Optional[str] = None
    # (file name, bytes, "type/subtype") for each file sent along
    attachments: Optional[List[tuple]] = None
    # A message in a conversation (support tickets): who it is from, its own id, and the earlier messages it answers,
    # so mail programs thread it. A person's reply isn't marked automatic.
    sender: Optional[Tuple[str, str]] = None        # (name, address); the app's MAIL_FROM when not set
    message_id: Optional[str] = None
    in_reply_to: Optional[str] = None
    references: Optional[List[str]] = None
    automatic: bool = True
    smtp: Optional["SmtpServer"] = None              # another mail server (the support mailbox's), else SMTP_*


@dataclass(frozen=True)
class SmtpServer:
    host: str
    port: int
    username: str
    password: str
    security: str = "starttls"


# Messages "sent" by the memory backend (tests read these).
outbox: List[EmailMessage] = []


def _mime(message: EmailMessage) -> MimeMessage:
    mime = MimeMessage()
    name, address = message.sender or (settings.MAIL_FROM_NAME, settings.MAIL_FROM)
    mime["From"] = formataddr((name, address))
    mime["To"] = message.to
    mime["Subject"] = message.subject
    mime["Date"] = formatdate(localtime=False)
    mime["Message-ID"] = message.message_id or make_msgid(domain=address.rpartition("@")[2] or None)
    if message.in_reply_to:
        mime["In-Reply-To"] = message.in_reply_to
    if message.references:
        mime["References"] = " ".join(message.references[-20:])
    if message.automatic:
        mime["Auto-Submitted"] = "auto-generated"  # keeps out-of-office replies from answering back
    reply_to = message.reply_to or settings.MAIL_REPLY_TO
    if reply_to:
        mime["Reply-To"] = reply_to
    mime.set_content(message.text)
    mime.add_alternative(message.html, subtype="html")
    for name, data, kind in message.attachments or []:
        maintype, _, subtype = kind.partition("/")
        mime.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    return mime


def _deliver_smtp(mime: MimeMessage, via: Optional[SmtpServer] = None) -> None:
    via = via or SmtpServer(settings.SMTP_HOST, settings.SMTP_PORT, settings.SMTP_USERNAME, settings.SMTP_PASSWORD, settings.SMTP_SECURITY)
    security = via.security.lower()
    try:
        if security == "ssl":
            server = smtplib.SMTP_SSL(via.host, via.port, timeout=settings.SMTP_TIMEOUT_SECONDS, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(via.host, via.port, timeout=settings.SMTP_TIMEOUT_SECONDS)
        with server:
            if security == "starttls":
                server.starttls(context=ssl.create_default_context())
            if via.username:
                server.login(via.username, via.password)
            server.send_message(mime)
    except smtplib.SMTPAuthenticationError as e:
        raise EmailError("The mail server refused our sign-in.") from e
    except smtplib.SMTPRecipientsRefused as e:
        raise EmailError("The mail server refused that address.") from e
    except (smtplib.SMTPException, OSError, ssl.SSLError) as e:
        raise EmailError("The mail server couldn't be reached or didn't accept the message.") from e


async def send(message: EmailMessage) -> None:
    backend = settings.MAIL_BACKEND.lower()
    if backend == "memory":
        outbox.append(message)
    elif backend == "console":
        logger.info("Email to %s\nSubject: %s\n\n%s", message.to, message.subject, message.text)
    elif backend == "smtp":
        await asyncio.to_thread(_deliver_smtp, _mime(message), message.smtp)
        logger.info("Email sent to %s (%s)", message.to, message.subject)
    else:
        raise EmailError(f"Unknown MAIL_BACKEND '{settings.MAIL_BACKEND}'.")


def check_mail_config() -> None:
    """Fails startup when production would send mail nowhere useful."""
    backend = settings.MAIL_BACKEND.lower()
    if backend not in ("smtp", "console", "memory"):
        raise RuntimeError("MAIL_BACKEND must be smtp, console or memory.")
    if settings.is_production and backend != "smtp":
        raise RuntimeError("MAIL_BACKEND must be 'smtp' in production.")
    if settings.is_production and settings.SMTP_HOST in ("localhost", "127.0.0.1"):
        raise RuntimeError("SMTP_HOST must point at a real mail server in production.")
    if settings.SMTP_SECURITY.lower() not in ("none", "starttls", "ssl"):
        raise RuntimeError("SMTP_SECURITY must be none, starttls or ssl.")
