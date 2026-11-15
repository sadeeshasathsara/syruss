"""
Sending email. One function, `send`, takes a finished message. The server never talks to a mail server itself: the
message goes into the mail outbox (in the database, so nothing is lost while Kafka or the mail service is down), the
relay publishes it on Kafka (mail.outbound), and the mail service (mail/) sends it through the account set up for its
purpose. How each message went comes back on mail.events (services/notifications/mail_relay.py).

`send` succeeds once the message is safely queued; it raises `EmailError` (with a message that is safe to show a
person) only when it can't be queued at all.

    MAIL_BACKEND  service  queue it for the mail service (every real server)
                  console  only log it (local work without the mail service)
                  memory   keep it in `outbox` (tests)
"""
import base64
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from app.core.config import settings
from app.core.secrets import seal

logger = logging.getLogger(__name__)

PURPOSES = ("notifications", "support")  # what the server sends for (the mail service also has "accounts", for Keycloak)
MAX_JOB_BYTES = 900_000                   # a job travels as one Kafka message (1 MB at most)


class EmailError(Exception):
    """The message couldn't be queued for sending."""


@dataclass
class EmailMessage:
    to: str
    subject: str
    text: str
    html: str
    reply_to: Optional[str] = None
    # (file name, bytes, "type/subtype") for each file sent along
    attachments: Optional[List[tuple]] = None
    # What it is for: "notifications" (everything the app tells people) or "support" (tickets). The mail service sends
    # it through that purpose's account, which decides the From address; `sender_name` only names the sender.
    purpose: str = "notifications"
    sender_name: Optional[str] = None
    # A message in a conversation (support tickets): its own id, and the earlier messages it answers, so mail programs
    # thread it. A person's reply isn't marked automatic.
    message_id: Optional[str] = None
    in_reply_to: Optional[str] = None
    references: Optional[List[str]] = None
    automatic: bool = True
    # Passed back with how it went (mail.events), e.g. which invitation it was
    meta: Dict[str, Any] = field(default_factory=dict)


# Messages "sent" by the memory backend (tests read these).
outbox: List[EmailMessage] = []


def job_of(message: EmailMessage, job_id: str) -> Dict[str, Any]:
    """The message as the mail service takes it (mail/accounts/base.Outbound)."""
    return {
        "id": job_id, "purpose": message.purpose, "to": message.to, "subject": message.subject, "text": message.text,
        "html": message.html, "sender_name": message.sender_name,
        "reply_to": message.reply_to, "message_id": message.message_id,
        "in_reply_to": message.in_reply_to, "references": list(message.references or []), "automatic": message.automatic,
        "attachments": [{"filename": name, "content_type": kind, "data_b64": base64.b64encode(data).decode()}
                        for name, data, kind in message.attachments or []],
        "meta": message.meta or {},
    }


async def send(message: EmailMessage) -> None:
    if message.purpose not in PURPOSES:
        raise EmailError(f"Unknown mail purpose '{message.purpose}'.")
    backend = settings.MAIL_BACKEND.lower()
    if backend == "memory":
        outbox.append(message)
    elif backend == "console":
        logger.info("Email to %s (%s)\nSubject: %s\n\n%s", message.to, message.purpose, message.subject, message.text)
    elif backend == "service":
        await _queue(message)
    else:
        raise EmailError(f"Unknown MAIL_BACKEND '{settings.MAIL_BACKEND}'.")


async def _queue(message: EmailMessage) -> None:
    from app.core.database import AsyncSessionLocal
    from app.models.mail import MailOutbox
    from app.services.notifications.mail_relay import mail_relay
    job_id = str(uuid.uuid4())
    job = job_of(message, job_id)
    if len(json.dumps(job)) > MAX_JOB_BYTES:
        raise EmailError("The message is too large to send (attachments over about 650 KB).")
    try:
        async with AsyncSessionLocal() as db:
            db.add(MailOutbox(id=job_id, purpose=message.purpose, to=message.to[:320], subject=message.subject[:500],
                              payload=seal(job), status="queued", meta=message.meta or {}))
            await db.commit()
    except Exception as e:  # noqa: BLE001
        raise EmailError("The message couldn't be queued for sending.") from e
    mail_relay.notify()
    logger.info("Email to %s queued (%s: %s)", message.to, message.purpose, message.subject)


def check_mail_config() -> None:
    """Fails startup on an unknown backend, or a real server (the test server or production) not sending mail at all."""
    backend = settings.MAIL_BACKEND.lower()
    if backend not in ("service", "console", "memory"):
        raise RuntimeError("MAIL_BACKEND must be service, console or memory.")
    if settings.is_deployed and backend != "service":
        raise RuntimeError(f"MAIL_BACKEND must be 'service' on a real server ({settings.ENV}).")
