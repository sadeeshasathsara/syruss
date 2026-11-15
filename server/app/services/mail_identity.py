"""
What the mail service says our mail is: the address app mail comes from, and the support mailbox's address.

These live in the mail service's environment only (MAIL_FROM, SUPPORT_EMAIL_ADDRESS in mail.env); the server asks for them
(GET /identity) when it starts and again now and then, and keeps the last answer. It needs them for the support mailbox:
a ticket email says where to write and its message ids end at that domain, and mail that comes from either address is
ours, never a customer's, so it must not open a ticket.

When the mail service can't be reached the last answer stands; before the first answer there is none, and the code that
must not guess (`ensure(required=True)`) raises instead of using a development address.
"""
import logging
import time
from dataclasses import dataclass
from typing import Optional

from app.core.config import settings
from app.services import mail_service

logger = logging.getLogger("services.mail_identity")

STALE_SECONDS = 300
RETRY_SECONDS = 15


@dataclass(frozen=True)
class Identity:
    from_address: str
    support_address: str


# What a server without a mail service (MAIL_BACKEND console or memory: local work, tests) uses
DEVELOPMENT = Identity("no-reply@syruss.local", "no-reply@syruss.local")

_known: Optional[Identity] = None
_asked_at = 0.0
_failed_at = 0.0


def current() -> Identity:
    """The last answer, or the development address when there has been none."""
    return _known or DEVELOPMENT


def loaded() -> bool:
    return _known is not None


async def refresh() -> Identity:
    """Asks the mail service now. Raises MailServiceError when it can't answer."""
    global _known, _asked_at
    answer = await mail_service.call("GET", "/identity")
    _known = Identity(from_address=str(answer["from_address"]), support_address=str(answer["support_address"]))
    _asked_at = time.monotonic()
    return _known


async def ensure(required: bool = False) -> Identity:
    """The identity, asked for again when the last answer is old. `required`: raise instead of answering with a
    development address when the mail service has never answered."""
    global _failed_at
    if settings.MAIL_BACKEND.lower() != "service":
        return current()
    now = time.monotonic()
    if _known is not None and now - _asked_at < STALE_SECONDS:
        return _known
    if _known is None or now - _failed_at >= RETRY_SECONDS:
        try:
            return await refresh()
        except mail_service.MailServiceError as e:
            _failed_at = now
            if _known is None:
                logger.warning("The mail service hasn't told us its addresses yet: %s", e)
                if required:
                    raise
    return current()


def use(from_address: str, support_address: Optional[str] = None) -> None:
    """Tests: set the identity without a mail service."""
    global _known, _asked_at
    _known = Identity(from_address, support_address or from_address)
    _asked_at = float("inf")


def forget() -> None:
    global _known, _asked_at, _failed_at
    _known, _asked_at, _failed_at = None, 0.0, 0.0
