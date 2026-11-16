"""
What the server needs to know about our mail: the address app mail comes from, the support mailbox's address, and every address
that is ours. It is read from the mail service's directory (services/mail_bus.py); there is no setting for any of it here.

The support mailbox needs them: a ticket email says where to write and its message ids end at that domain, and mail that comes
from any of our addresses is ours, never a customer's, so it must not open a ticket.

Before the mail service has been heard from, the code that must not guess (`ensure(required=True)`) raises instead of using a
development address; after, the last directory stands even if the service goes away.
"""
from dataclasses import dataclass
from typing import Optional

from app.core.config import settings
from app.services.mail_bus import MailBusError, bus


@dataclass(frozen=True)
class Identity:
    from_address: str
    support_address: str
    ours: frozenset = frozenset()    # every address of ours (lowercase): mail from one is never a customer's


# What a server without a mail service (MAIL_BACKEND console or memory: local work, tests) uses
DEVELOPMENT = Identity("no-reply@syruss.local", "no-reply@syruss.local", frozenset({"no-reply@syruss.local"}))

_override: Optional[Identity] = None


def _from_directory() -> Optional[Identity]:
    from app.services import mail_uses
    sender = mail_uses.address_of(mail_uses.APP_NOTIFICATIONS)
    if not sender:
        return None
    support = mail_uses.address_of(mail_uses.SUPPORT_MAIL) or sender
    ours = {a["address"].lower() for a in bus.entries("account:") if a.get("address")} | {sender.lower(), support.lower()}
    return Identity(sender, support, frozenset(ours))


def current() -> Identity:
    """The last directory's addresses, or the development address when there has been none."""
    return _override or _from_directory() or DEVELOPMENT


def loaded() -> bool:
    return _override is not None or _from_directory() is not None


async def ensure(required: bool = False) -> Identity:
    """The identity, waiting a little for the directory the first time. `required`: raise instead of answering with a
    development address when the mail service has never been heard from."""
    if settings.MAIL_BACKEND.lower() != "service" or loaded():
        return current()
    await bus.ready()
    if loaded():
        return current()
    if required:
        raise MailBusError("The mail service hasn't told us its addresses yet.", 503)
    return current()


def use(from_address: str, support_address: Optional[str] = None) -> None:
    """Tests: set the identity without a mail service."""
    global _override
    support = support_address or from_address
    _override = Identity(from_address, support, frozenset({from_address.lower(), support.lower()}))


def forget() -> None:
    global _override
    _override = None
