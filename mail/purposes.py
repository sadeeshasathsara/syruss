"""
Jobs: what Syruss uses mail for. Each job names a use (notifications, document inbound, promotions, help, ...), says
whether it sends, reads or both, and is routed to one mailbox (routes) with a few settings of its own.

The built-in jobs are always there and can't be removed. More can be added from the admin app: this service sends and
reads for them like any other; the server acts only on the ones it knows by key.

    notifications  send          everything the server tells people: invitations, codes, billing, staff notices
    documents      read          documents people send by email (the server turns attachments into documents)
    promotions     send          announcements and offers (a mailbox of its own; no campaign feature yet)
    support        send + read   help requests: answers go out from the help address, replies are read from it
    accounts       send          Keycloak's own emails: verify an address, reset a password, a staff set-up link
"""
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List

from mail.accounts.base import Capability

DIRECTIONS: Dict[str, Capability] = {
    "send": Capability.SEND,
    "read": Capability.READ,
    "both": Capability.READ | Capability.SEND,
}
KEY = re.compile(r"^[a-z][a-z0-9_]{1,38}$")
# What a job may set for itself (anything else is refused)
SETTINGS = {
    "sender_name": str,     # the name mail from this job is sent as
    "reply_to": str,        # where replies go (empty: the From address)
    "poll_seconds": int,    # how often it is read (empty: MAIL_POLL_SECONDS)
}


class JobError(ValueError):
    """A job that can't be made or changed, in words an owner can act on."""


@dataclass
class Job:
    key: str
    label: str
    direction: str
    description: str = ""
    system: bool = False
    settings: Dict[str, Any] = field(default_factory=dict)

    @property
    def needs(self) -> Capability:
        return DIRECTIONS[self.direction]

    @property
    def reads(self) -> bool:
        return Capability.READ in self.needs


BUILTIN: List[Job] = [
    Job("notifications", "Notifications", "send", "Invitations, sign-in codes, billing and staff notices.", system=True),
    Job("documents", "Document inbound", "read", "Documents people send by email become documents in their workspace.", system=True),
    Job("promotions", "Promotions", "send", "Announcements and offers.", system=True),
    Job("support", "Help", "both", "Help requests: answers go out from this mailbox and replies are read from it.", system=True),
    Job("accounts", "Sign-in and account emails", "send", "Keycloak's own emails: verify an address, reset a password, a staff set-up link.", system=True),
]
BUILTIN_NEEDS: Dict[str, Capability] = {j.key: j.needs for j in BUILTIN}


def clean_settings(values: Dict[str, Any]) -> Dict[str, Any]:
    """The settings a job may have, with each value the right kind (a setting set to nothing is dropped)."""
    out: Dict[str, Any] = {}
    for name, value in (values or {}).items():
        kind = SETTINGS.get(name)
        if kind is None:
            raise JobError(f"'{name}' isn't a setting a job has. They are: {', '.join(SETTINGS)}.")
        if value in (None, ""):
            continue
        try:
            out[name] = kind(value)
        except (TypeError, ValueError) as e:
            raise JobError(f"'{name}' must be {'a number' if kind is int else 'text'}.") from e
        if kind is int and out[name] < 15:
            raise JobError("poll_seconds can't be under 15.")
        if kind is str:
            out[name] = out[name].strip()[:200]
    return out


def validate_new(key: str, label: str, direction: str) -> None:
    if not KEY.match(key or ""):
        raise JobError("A job's key is lowercase letters, digits and underscores, starting with a letter (2 to 39 long).")
    if direction not in DIRECTIONS:
        raise JobError(f"A job sends, reads or does both ({', '.join(DIRECTIONS)}).")
    if not (label or "").strip():
        raise JobError("Give the job a name.")
