"""
Jobs and their mailboxes. A job (purposes.py) is routed to one account (set in the admin app); without a route, or while
its account needs reconnecting, it uses the account from the environment if that one can do the job. `open(purpose)`
returns the account opened for exactly what the job needs, nothing more.
"""
import logging
from typing import Optional, Tuple, Union
from mail.accounts import account_from_row, env_account
from mail.accounts.base import Capability, MailAccount, MailHandle, MailNotPermitted
from mail.purposes import BUILTIN_NEEDS
from mail.store import Store, unseal

logger = logging.getLogger("mail.router")

# The built-in jobs and what each needs (the full list, with the ones added in the admin app, is in the database)
PURPOSES = BUILTIN_NEEDS


class Router:
    def __init__(self, store: Store, fallback: Optional[MailAccount] = None):
        self.store = store
        self.fallback = fallback if fallback is not None else env_account()

    async def needs(self, purpose: str) -> Capability:
        """What a job needs. Raises MailNotPermitted for a job nobody made."""
        job = await self.store.job(purpose)
        if job is None:
            raise MailNotPermitted(f"Unknown mail purpose '{purpose}'.")
        return job.needs

    async def account_for(self, purpose: str) -> Tuple[MailAccount, bool]:
        """(the account a purpose uses now, whether it is its routed one rather than the fallback)."""
        needs = await self.needs(purpose)
        account_id = (await self.store.routes()).get(purpose)
        row = await self.store.account(account_id) if account_id else None
        if row is not None and row.status == "active":
            account = account_from_row(row, unseal(row.secret), on_lost=self.store.lost, on_secret=self.store.save_secret)
            if needs & ~account.granted == Capability.NONE:
                return account, True
            logger.warning("Mail account %s can't do everything %s needs; using the fallback", row.address, purpose)
        return self.fallback, False

    async def open(self, purpose: str) -> MailHandle:
        account, _ = await self.account_for(purpose)
        return account.open(await self.needs(purpose))

    @staticmethod
    def check(account: MailAccount, purpose: Union[str, Capability]) -> None:
        """Refuses a route that can't work (checked when a route is saved, not when mail has to go out). `purpose` is a
        built-in job's key, or what the job needs."""
        account.open(PURPOSES[purpose] if isinstance(purpose, str) else purpose)
