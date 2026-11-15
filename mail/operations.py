"""
What an owner can do with mail, as plain operations on the service's own state: connect, test and remove accounts, choose which
account serves a job, add and change jobs, read the delivery log, send a given-up message again, connect Google. Commands
(commands.py) call these; each raises OperationError with words an owner can act on, never anything secret.
"""
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import delete, select, update

from mail import apps, oauth
from mail.accounts import ENV_ACCOUNT_ID, account_from_row
from mail.accounts.base import MailNotPermitted
from mail.purposes import Job, JobError, Use, clean_settings, validate_new, validate_use
from mail.router import Router
from mail.store import AccountRow, CursorRow, JobRow, PurposeRow, Store, seal, unseal


class OperationError(Exception):
    """Refused, with a reason that is safe to show an owner."""


class Operations:
    def __init__(self, store: Store, router: Router):
        self.store, self.router = store, router

    # ---- accounts ----------------------------------------------------------------------------------------

    async def _account(self, account_id: str):
        if account_id == ENV_ACCOUNT_ID:
            return None, self.router.fallback
        row = await self.store.account(account_id)
        if row is None:
            raise OperationError("No such mail account.")
        return row, account_from_row(row, unseal(row.secret), on_lost=self.store.lost, on_secret=self.store.save_secret)

    async def add_imap(self, *, by: str, address: str, name: str, config: Dict[str, Any], secret: Dict[str, str], jobs: List[str]) -> AccountRow:
        if not config.get("smtp_host") and not config.get("imap_host"):
            raise OperationError("Give an SMTP server to send, an IMAP server to read, or both.")
        address = (address or "").strip().lower()
        if "@" not in address:
            raise OperationError("Give the mailbox's address.")
        row = AccountRow(id=f"imap-{uuid.uuid4().hex[:12]}", kind="smtp_imap", address=address, name=(name or "Syruss")[:120],
                         config=config, secret=seal(secret), created_by=by, status="active")
        row.granted = account_from_row(row, {}).granted.names()
        for key in jobs:                       # refuse before anything is kept
            await self._check(account_from_row(row, secret), key)
        async with self.store.session() as s:
            s.add(row)
            if jobs:
                await s.execute(update(PurposeRow).where(PurposeRow.key.in_(jobs)).values(account_id=row.id))
            await s.commit()
        return row

    async def _check(self, account, key: str) -> Job:
        job = await self.store.job(key)
        if job is None:
            raise OperationError(f"There's no job called '{key}'.")
        try:
            Router.check(account, job.needs)
        except MailNotPermitted as e:
            raise OperationError(f"{e} The {job.label} job needs to {' and '.join(job.needs.names())}.") from e
        return job

    async def test(self, account_id: str, purpose: Optional[str] = None) -> Dict[str, Any]:
        row, account = await self._account(account_id)
        job = await self.store.job(purpose) if purpose else None
        try:
            result = await account.open(job.needs if job is not None else account.granted).test()
        except MailNotPermitted as e:
            return {"ok": False, "detail": str(e)}
        if row is not None:
            async with self.store.session() as s:
                r = await s.get(AccountRow, row.id)
                r.checked_at, r.last_error = datetime.now(timezone.utc), None if result.ok else result.detail
                if result.ok and r.status == "needs_reconnect" and r.kind not in oauth.KINDS:
                    r.status = "active"
                await s.commit()
        return {"ok": result.ok, "detail": result.detail}

    async def remove(self, account_id: str) -> None:
        row, _ = await self._account(account_id)
        if row is None:
            raise OperationError("The account from the environment isn't removed here.")
        if row.kind in oauth.KINDS:
            await oauth.revoke(oauth.KINDS[row.kind], unseal(row.secret).get("refresh_token", ""))
            oauth.forget_tokens(row.id)
        async with self.store.session() as s:
            await s.execute(update(PurposeRow).where(PurposeRow.account_id == row.id).values(account_id=None))
            await s.execute(delete(CursorRow).where(CursorRow.account_id == row.id))
            await s.execute(delete(AccountRow).where(AccountRow.id == row.id))
            await s.commit()

    # ---- jobs --------------------------------------------------------------------------------------------

    async def route(self, purpose: str, account_id: Optional[str]) -> None:
        """Makes `account_id` serve a job (None, or "env": the account from the environment again)."""
        if await self.store.job(purpose) is None:
            raise OperationError("No such job.")
        if account_id in (None, ENV_ACCOUNT_ID):
            await self.store.set_route(purpose, None)
        else:
            _, account = await self._account(account_id)
            await self._check(account, purpose)
            await self.store.set_route(purpose, account_id)

    async def create_job(self, key: str, label: str, direction: str, description: str = "", settings: Optional[Dict[str, Any]] = None) -> Job:
        try:
            validate_new(key, label, direction)
            if await self.store.job(key) is not None:
                raise JobError(f"There's a job called '{key}' already.")
            job = Job(key=key, label=label.strip()[:120], direction=direction, description=(description or "")[:500], system=False,
                      settings=clean_settings(settings or {}))
        except JobError as e:
            raise OperationError(str(e)) from e
        await self.store.save_job(job)
        return job

    async def update_job(self, key: str, label: Optional[str] = None, description: Optional[str] = None, settings: Optional[Dict[str, Any]] = None) -> Job:
        job = await self.store.job(key)
        if job is None:
            raise OperationError("No such job.")
        try:
            if label is not None:
                if not label.strip():
                    raise JobError("Give the job a name.")
                job.label = label.strip()[:120]
            if description is not None:
                job.description = description[:500]
            if settings is not None:
                job.settings = clean_settings(settings)
        except JobError as e:
            raise OperationError(str(e)) from e
        await self.store.save_job(job)
        return job

    async def delete_job(self, key: str) -> None:
        job = await self.store.job(key)
        if job is None:
            raise OperationError("No such job.")
        if job.system:
            raise OperationError(f"{job.label} is built in and can't be removed.")
        await self.store.delete_job(key)

    # ---- functions (uses) ------------------------------------------------------------------------------------------

    async def register_uses(self, items: List[Dict[str, Any]]) -> int:
        """The application tells this service what functions it has (idempotent: sent at every start)."""
        uses = []
        try:
            for i in items[:100]:
                u = Use(key=str(i.get("key", "")), label=str(i.get("label", ""))[:120], description=str(i.get("description", ""))[:300],
                        direction=str(i.get("direction", "")), default_job=str(i.get("default_job", "")))
                validate_use(u.key, u.label, u.direction, u.default_job)
                uses.append(u)
        except JobError as e:
            raise OperationError(str(e)) from e
        await self.store.register_uses(uses)
        return len(uses)

    async def assign_use(self, key: str, job_key: Optional[str]) -> None:
        """Chooses the job a function uses (None: its default again). The job must be able to do what the function does."""
        use = await self.store.use(key)
        if use is None:
            raise OperationError("No such function.")
        if job_key:
            job = await self.store.job(job_key)
            if job is None:
                raise OperationError("No such job.")
            if use.needs & ~job.needs:
                raise OperationError(f"{use.label} needs a job that can {' and '.join(use.needs.names())}; {job.label} can't.")
        await self.store.set_use_job(key, job_key or None)

    # ---- the delivery log ----------------------------------------------------------------------------------

    async def delivery_log(self, status: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        async with self.store.session() as s:
            stmt = select(JobRow).order_by(JobRow.created_at.desc()).limit(max(1, min(int(limit), 500)))
            if status:
                stmt = stmt.where(JobRow.status == status)
            rows = (await s.execute(stmt)).scalars().all()
        return [{"id": r.id, "purpose": r.purpose, "to": r.to, "subject": r.subject, "status": r.status, "attempts": r.attempts,
                 "last_error": r.last_error, "account_id": r.account_id, "created_at": r.created_at.isoformat() if r.created_at else None,
                 "done_at": r.done_at.isoformat() if r.done_at else None,
                 "next_attempt_at": r.next_attempt_at.isoformat() if r.next_attempt_at else None,
                 "retryable": r.status == "dead" and r.payload is not None} for r in rows]

    async def retry(self, job_id: str) -> Dict[str, Any]:
        async with self.store.session() as s:
            row = await s.get(JobRow, job_id)
            if row is None or row.status not in ("dead", "retrying"):
                raise OperationError("Only mail that was given up or is waiting can be sent again now.")
            if row.payload is None:
                raise OperationError("This message isn't kept any more; it can't be sent again.")
            row.status, row.attempts, row.next_attempt_at = "retrying", 0, datetime.now(timezone.utc)
            await s.commit()
        return {"id": job_id, "status": "retrying"}

    # ---- Google and Microsoft ------------------------------------------------------------------------------

    async def _needs(self) -> Dict[str, Any]:
        return {j.key: j.needs for j in await self.store.jobs()}

    async def oauth_start(self, provider: str, jobs: List[str], redirect_uri: str, by: str) -> str:
        try:
            return oauth.start(provider, jobs, redirect_uri, by, await self._needs())
        except oauth.ConnectError as e:
            raise OperationError(str(e)) from e

    async def oauth_finish(self, provider: str, code: str, state: str, redirect_uri: str, by: str) -> Dict[str, Any]:
        try:
            done = await oauth.finish(self.store, provider=provider, code=code, state=state, redirect_uri=redirect_uri, by=by)
        except oauth.ConnectError as e:
            raise OperationError(str(e)) from e
        for key in done["purposes"]:  # it serves what it was connected for
            await self.store.set_route(key, done["id"])
        return done

    # ---- provider apps -------------------------------------------------------------------------------------

    async def app_set(self, provider: str, client_id: str, client_secret: str, tenant: str = "") -> None:
        if provider not in apps.PROVIDERS:
            raise OperationError("Only Google and Microsoft apps can be set.")
        client_id, tenant = client_id.strip(), tenant.strip()
        existing = apps.get(provider)
        if not client_id:   # a blank client id keeps the one already set (the id is never shown, so it can't be re-typed to keep it)
            if existing is None:
                raise OperationError("Give the app's client id.")
            client_id = existing["client_id"]
        if not client_secret:   # a blank secret keeps the one already there (only the id or tenant changed)
            if existing is None or existing.get("from_environment"):
                raise OperationError("Give the app's client secret.")
            client_secret = existing["client_secret"]
        if provider == "microsoft" and tenant and not all(c.isalnum() or c in ".-" for c in tenant):
            raise OperationError("The tenant is 'common', 'organizations', 'consumers', or your directory's id or domain.")
        await apps.save(self.store, provider, client_id, client_secret, tenant if provider == "microsoft" else "")

    async def app_clear(self, provider: str) -> None:
        if provider not in apps.PROVIDERS:
            raise OperationError("Only Google and Microsoft apps can be set.")
        await apps.clear(self.store, provider)
