"""
Sending a job, once, and trying again when it fails.

    accept(job)   records a job by its id: a job published twice is sent once (Kafka delivers at least once)
    attempt(id)   sends it through its purpose's account, then marks it sent, to try again (waiting longer each time),
                  or given up (an address refused, or MAX_ATTEMPTS used). A job being sent holds a lease: if this process
                  dies mid-send, the retry loop takes it again once the lease runs out.
    due()         the jobs whose next try has come (the retry loop)

Each outcome becomes an event for the server (mail.events): sent, retrying (with why) or dead.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert
from mail.accounts.base import MailError, MailNotPermitted, Outbound
from mail.config import settings
from mail.router import Router
from mail.store import JobRow, Store, seal, unseal

logger = logging.getLogger("mail.delivery")
LEASE = timedelta(minutes=10)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def with_defaults(job: Outbound, job_settings: Optional[Dict[str, Any]] = None) -> Outbound:
    """The sender's name and the reply address the message didn't set itself: the job's own settings, else this service's
    (support mail is in the support name and answered at the support address), so no publisher needs to know them."""
    support = job.purpose == "support"
    mine = job_settings or {}
    sender_name = job.sender_name or mine.get("sender_name") or (settings.SUPPORT_FROM_NAME if support else settings.MAIL_FROM_NAME)
    reply_to = (job.reply_to or mine.get("reply_to")
                or ((settings.SUPPORT_EMAIL_ADDRESS or settings.MAIL_FROM) if support else settings.MAIL_REPLY_TO) or None)
    return job.model_copy(update={"sender_name": sender_name, "reply_to": reply_to})


def backoff(attempts: int) -> timedelta:
    """How long to wait after the `attempts`-th failure: RETRY_BASE_SECONDS, doubling, at most RETRY_MAX_SECONDS."""
    return timedelta(seconds=min(settings.RETRY_BASE_SECONDS * 2 ** max(0, attempts - 1), settings.RETRY_MAX_SECONDS))


class Delivery:
    def __init__(self, store: Store, router: Router):
        self.store, self.router = store, router

    async def accept(self, job: Outbound) -> bool:
        """Records a new job (sealed). False when this id was seen before: it isn't sent again."""
        async with self.store.session() as s:
            result = await s.execute(insert(JobRow).values(
                id=job.id, purpose=job.purpose, to=job.to[:320], subject=job.subject[:500], status="queued", attempts=0,
                next_attempt_at=_now(), payload=seal(job.model_dump()), meta=job.meta or {}, created_at=_now(),
            ).on_conflict_do_nothing(index_elements=["id"]).returning(JobRow.id))
            await s.commit()
            return result.first() is not None

    async def _claim(self, job_id: str) -> Optional[Outbound]:
        """Takes a job to send now (queued, or due to try again), holding it for LEASE. None when it isn't ours to send."""
        async with self.store.session() as s:
            row = (await s.execute(select(JobRow).where(JobRow.id == job_id).with_for_update(skip_locked=True))).scalar_one_or_none()
            if row is None or row.status in ("sent", "dead") or (row.status != "queued" and row.next_attempt_at > _now()):
                return None
            row.status, row.next_attempt_at = "sending", _now() + LEASE
            job = Outbound(**unseal(row.payload))
            await s.commit()
            return job

    async def attempt(self, job_id: str) -> Optional[Dict[str, Any]]:
        """Sends the job if it is due. Returns the event for the server, or None when there was nothing to do."""
        job = await self._claim(job_id)
        if job is None:
            return None
        defined = await self.store.job(job.purpose)
        job = with_defaults(job, defined.settings if defined is not None else None)
        account_id, provider_id, error, permanent = None, None, None, False
        try:
            account, _ = await self.router.account_for(job.purpose)
            account_id = account.id
            if account.kind == "none":   # no mailbox yet: wait for one, and don't count it against the job's tries
                return await self._wait(job, "No mailbox is connected for this job yet: connect one in the admin app (Email).")
            receipt = await account.open(await self.router.needs(job.purpose)).send(job)
            provider_id = receipt.provider_id
        except MailError as e:
            error, permanent = str(e), e.permanent
        except MailNotPermitted as e:  # nothing can send for this purpose now: an admin can fix it, so try again later
            error = str(e)
        except Exception as e:  # noqa: BLE001  (a job must never stop the service)
            logger.exception("Sending mail job %s failed unexpectedly", job_id)
            error = f"Unexpected error: {type(e).__name__}"
        return await self._record(job, account_id, provider_id, error, permanent)

    async def _wait(self, job: Outbound, why: str) -> Dict[str, Any]:
        async with self.store.session() as s:
            row = await s.get(JobRow, job.id)
            row.status, row.last_error, row.next_attempt_at = "retrying", why, _now() + timedelta(seconds=60)
            event = {"id": row.id, "purpose": row.purpose, "status": "retrying", "attempts": row.attempts, "account_id": row.account_id,
                     "provider_id": None, "error": why, "meta": row.meta or {}, "next_attempt_at": row.next_attempt_at.isoformat()}
            await s.commit()
        logger.warning("Mail job %s (%s) is waiting: %s", job.id, job.purpose, why)
        return event

    async def _record(self, job: Outbound, account_id: Optional[str], provider_id: Optional[str], error: Optional[str],
                      permanent: bool) -> Dict[str, Any]:
        async with self.store.session() as s:
            row = await s.get(JobRow, job.id)
            row.attempts += 1
            row.account_id = account_id or row.account_id
            if error is None:
                row.status, row.provider_id, row.last_error, row.next_attempt_at = "sent", provider_id, None, None
            elif permanent or row.attempts >= settings.MAX_ATTEMPTS:
                row.status, row.last_error, row.next_attempt_at = "dead", error, None
            else:
                row.status, row.last_error, row.next_attempt_at = "retrying", error, _now() + backoff(row.attempts)
            if row.status in ("sent", "dead"):
                row.payload, row.done_at = None, _now()  # the message itself isn't kept once it's done
            event = {"id": row.id, "purpose": row.purpose, "status": row.status, "attempts": row.attempts, "account_id": row.account_id,
                     "provider_id": row.provider_id, "error": row.last_error, "meta": row.meta or {},
                     "next_attempt_at": row.next_attempt_at.isoformat() if row.next_attempt_at else None}
            await s.commit()
        if error:
            logger.warning("Mail job %s (%s) to %s: %s (%s)", job.id, job.purpose, job.to, error, event["status"])
        else:
            logger.info("Mail job %s (%s) sent to %s", job.id, job.purpose, job.to)
        return event

    async def due(self, limit: int = 20) -> List[str]:
        """Jobs to try now: waiting to try again, or held by a sender whose lease ran out."""
        async with self.store.session() as s:
            return list((await s.execute(select(JobRow.id).where(
                or_(JobRow.status == "retrying", JobRow.status == "sending", JobRow.status == "queued"),
                JobRow.next_attempt_at <= _now()).order_by(JobRow.next_attempt_at).limit(limit))).scalars())
