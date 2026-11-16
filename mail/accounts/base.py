"""
The mail interface. A MailAccount is one mailbox Syruss may use (an SMTP/IMAP server, a Google account). It says what
it can do at all (`supports`) and what it was given (`granted`: the SMTP or IMAP details it has, the Google permissions
someone approved). Nothing uses an account directly: `open(needs)` hands out a MailHandle with only the abilities asked
for, and refuses when the account wasn't given them. A purpose that only sends can never read a mailbox.
"""
import base64
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Flag, auto
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field


class Capability(Flag):
    NONE = 0
    READ = auto()
    SEND = auto()

    @classmethod
    def parse(cls, names) -> "Capability":
        out = cls.NONE
        for name in names or ():
            out |= cls[name.upper()]
        return out

    def names(self) -> List[str]:
        return [c.name.lower() for c in (Capability.READ, Capability.SEND) if c in self]


class MailError(Exception):
    """A message couldn't be sent or a mailbox couldn't be read. The text is safe to show staff. `permanent`: trying
    again won't help (the address was refused), so the job is given up at once."""

    def __init__(self, message: str, permanent: bool = False):
        super().__init__(message)
        self.permanent = permanent


class MailNotPermitted(Exception):
    """The account wasn't given what this use needs (sending, or reading)."""


class Attachment(BaseModel):
    filename: str
    content_type: str = "application/octet-stream"
    data_b64: str

    @property
    def data(self) -> bytes:
        return base64.b64decode(self.data_b64)


class Outbound(BaseModel):
    """One message to send, as publishers put it on mail.outbound. The account it goes through decides the From
    address; `sender_name` only names it."""
    id: str = Field(..., min_length=1, max_length=100)           # the publisher's id: the same id is sent once
    purpose: str = Field(..., min_length=1, max_length=40)
    to: str
    subject: str
    text: str
    html: Optional[str] = None
    sender_name: Optional[str] = None
    reply_to: Optional[str] = None
    message_id: Optional[str] = None
    in_reply_to: Optional[str] = None
    references: List[str] = Field(default_factory=list)
    automatic: bool = True                                         # marks it Auto-Submitted (keeps auto-replies away)
    attachments: List[Attachment] = Field(default_factory=list)
    meta: Dict[str, Any] = Field(default_factory=dict)             # passed back on mail.events (what it was about)


@dataclass
class SentReceipt:
    provider_id: Optional[str] = None   # the provider's id for the message, when it gives one


@dataclass
class TestResult:
    ok: bool
    detail: str


class MailAccount(ABC):
    """One mailbox. Subclasses send and read; `open` is the only way to reach them."""

    kind: str = "base"
    supports: Capability = Capability.NONE

    def __init__(self, *, id: str, address: str, name: str, granted: Capability):
        self.id, self.address, self.name = id, address, name
        self.granted = granted & self.supports  # an account can't be given what its kind can't do

    def open(self, needs: Capability) -> "MailHandle":
        missing = needs & ~self.granted
        if missing or needs == Capability.NONE:
            raise MailNotPermitted(f"The account {self.address} wasn't given {', '.join(missing.names()) or 'anything'}.")
        return MailHandle(self, needs)

    def describe(self) -> Dict[str, Any]:
        return {"id": self.id, "kind": self.kind, "address": self.address, "name": self.name, "granted": self.granted.names()}

    @abstractmethod
    async def _send(self, message: Outbound) -> SentReceipt: ...

    async def _fetch(self, cursor: Optional[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[bytes]]:
        raise MailError("This account can't read mail.", permanent=True)

    async def _test(self, needs: Capability) -> TestResult:
        return TestResult(True, "Nothing to check.")


@dataclass
class MailHandle:
    """An account, opened for one use: it does only what was asked for when it was opened."""
    account: MailAccount
    needs: Capability
    _: Any = field(default=None, repr=False)

    async def send(self, message: Outbound) -> SentReceipt:
        if Capability.SEND not in self.needs:
            raise MailNotPermitted("Opened without the right to send.")
        return await self.account._send(message)

    async def fetch(self, cursor: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Any], List[bytes]]:
        """(the new cursor, the raw messages that arrived since `cursor`). The first call only records where the
        mailbox is now: what was there already is left alone."""
        if Capability.READ not in self.needs:
            raise MailNotPermitted("Opened without the right to read.")
        return await self.account._fetch(cursor)

    async def test(self) -> TestResult:
        return await self.account._test(self.needs)
