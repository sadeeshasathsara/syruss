"""Accounts for working without a mail server: `console` only logs what it would send, `memory` keeps it (tests)."""
import logging
from typing import Any, Dict, List, Optional, Tuple
from mail.accounts.base import Capability, MailAccount, MailError, Outbound, SentReceipt

logger = logging.getLogger("mail.local")


class ConsoleAccount(MailAccount):
    kind = "console"
    supports = Capability.SEND

    def __init__(self, *, id: str, address: str, name: str):
        super().__init__(id=id, address=address, name=name, granted=Capability.SEND)

    async def _send(self, message: Outbound) -> SentReceipt:
        logger.info("Email to %s (%s)\nSubject: %s\n\n%s", message.to, message.purpose, message.subject, message.text)
        return SentReceipt()


class MemoryAccount(MailAccount):
    """Keeps what it sends in `sent`, and reads whatever a test puts in `inbox`. `fail` makes the next sends fail."""
    kind = "memory"
    supports = Capability.READ | Capability.SEND

    def __init__(self, *, id: str = "memory", address: str = "mail@syruss.test", name: str = "Syruss",
                 granted: Capability = Capability.READ | Capability.SEND):
        super().__init__(id=id, address=address, name=name, granted=granted)
        self.sent: List[Outbound] = []
        self.inbox: List[bytes] = []
        self.fail: Optional[MailError] = None

    async def _send(self, message: Outbound) -> SentReceipt:
        if self.fail is not None:
            raise self.fail
        self.sent.append(message)
        return SentReceipt(provider_id=f"memory-{len(self.sent)}")

    async def _fetch(self, cursor: Optional[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[bytes]]:
        seen = int((cursor or {}).get("seen", 0)) if cursor is not None else len(self.inbox)
        return {"seen": len(self.inbox)}, self.inbox[seen:] if cursor is not None else []
