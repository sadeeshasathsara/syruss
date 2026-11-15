"""
Purposes: what Syruss uses mail for, and what each needs. A purpose is routed to one account (set in the admin app);
without a route, or while its account needs reconnecting, it uses the account from the environment if that one can do
the job. `open(purpose)` returns the account opened for exactly what the purpose needs, nothing more.

    notifications  SEND          everything the server tells people: invitations, codes, billing, staff notices,
                                 records delivered by email
    accounts       SEND          Keycloak's own emails: verify an address, reset a password, a new staff member's set-up
    support        READ + SEND   support tickets: answers go out from the support address, replies are read from it
"""
import logging
from typing import Dict, Optional, Tuple
from mail.accounts import account_from_row, env_account
from mail.accounts.base import Capability, MailAccount, MailHandle, MailNotPermitted
from mail.store import Store, unseal

logger = logging.getLogger("mail.router")

PURPOSES: Dict[str, Capability] = {
    "notifications": Capability.SEND,
    "accounts": Capability.SEND,
    "support": Capability.READ | Capability.SEND,
}


class Router:
    def __init__(self, store: Store, fallback: Optional[MailAccount] = None):
        self.store = store
        self.fallback = fallback if fallback is not None else env_account()

    async def account_for(self, purpose: str) -> Tuple[MailAccount, bool]:
        """(the account a purpose uses now, whether it is its routed one rather than the fallback)."""
        if purpose not in PURPOSES:
            raise MailNotPermitted(f"Unknown mail purpose '{purpose}'.")
        account_id = (await self.store.routes()).get(purpose)
        row = await self.store.account(account_id) if account_id else None
        if row is not None and row.status == "active":
            account = account_from_row(row, unseal(row.secret), on_lost=self.store.lost)
            if PURPOSES[purpose] & ~account.granted == Capability.NONE:
                return account, True
            logger.warning("Mail account %s can't do everything %s needs; using the fallback", row.address, purpose)
        return self.fallback, False

    async def open(self, purpose: str) -> MailHandle:
        account, _ = await self.account_for(purpose)
        return account.open(PURPOSES[purpose])

    @staticmethod
    def check(account: MailAccount, purpose: str) -> None:
        """Refuses a route that can't work (checked when a route is saved, not when mail has to go out)."""
        account.open(PURPOSES[purpose])
