"""
Reading mailboxes. Each job that reads (help, document inbound, and any added in the admin app) has a poller of its own
that reads its account's mailbox every `poll_seconds` (the job's setting, else MAIL_POLL_SECONDS), opened for reading only,
from where it last left off (mail_cursors, per account and job). A slow or failing mailbox never holds up another. Each new
message is kept here sealed (mail_inbound) and published on mail.inbound whole (the email's own bytes as the message, its job,
account and size in the headers); the server takes it in and says so on mail.inbound.acks, and only then is it deleted here.
One the server hasn't acknowledged is published again after REPUBLISH_AFTER_SECONDS, so a server that was down misses nothing.
An email bigger than MAIL_MAX_MESSAGE_BYTES is not kept: a notice with no body (header too_large) tells the server it came.

The first read of a mailbox only notes where it is: what was there already is left alone. Mail nobody collects is
dropped after INBOUND_KEEP_DAYS.
"""
import asyncio
import logging
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional
from sqlalchemy import delete, select
from mail.accounts.base import Capability, MailError, MailNotPermitted
from mail.config import settings
from mail.router import Router
from mail.sealing import open_bytes, seal_bytes
from mail.store import CursorRow, InboundRow, Store

logger = logging.getLogger("mail.inbound")
# (what it is: id, job, account, size, and too_large for one too big to carry; the email's bytes, empty when too big)
Announce = Callable[[Dict[str, Any], bytes], Awaitable[None]]
SUPERVISE_SECONDS = 10
REPUBLISH_AFTER_SECONDS = 300


class Reader:
    def __init__(self, store: Store, router: Router, announce: Optional[Announce] = None):
        self.store, self.router, self.announce = store, router, announce

    async def read(self, purpose: str) -> int:
        """Reads one job's mailbox once. Returns how many new messages were kept."""
        account, _ = await self.router.account_for(purpose)
        if Capability.READ not in account.granted:
            return 0
        handle = account.open(Capability.READ)  # reading only, even when the job also sends
        async with self.store.session() as s:
            row = await s.get(CursorRow, (account.id, purpose))
            cursor = dict(row.cursor) if row is not None else None
        new_cursor, raws = await handle.fetch(cursor)
        kept: List[tuple] = []
        async with self.store.session() as s:
            for raw in raws:
                event = {"id": str(uuid.uuid4()), "purpose": purpose, "account_id": account.id, "size": len(raw)}
                if len(raw) > settings.MAIL_MAX_MESSAGE_BYTES:
                    logger.warning("A message of %d bytes for %s is over MAIL_MAX_MESSAGE_BYTES: not kept", len(raw), purpose)
                    kept.append(({**event, "too_large": True}, b""))
                    continue
                s.add(InboundRow(id=event["id"], purpose=purpose, account_id=account.id, raw=seal_bytes(raw)))
                kept.append((event, raw))
            await s.merge(CursorRow(account_id=account.id, purpose=purpose, cursor=new_cursor))
            await s.commit()  # the messages and the new cursor together: nothing is read twice or lost
        for event, raw in kept:
            if self.announce:
                await self.announce(event, raw)
        if kept:
            logger.info("Read %d new message(s) for %s from %s", len(kept), purpose, account.address)
        return len(kept)

    async def republish_stale(self, sent: Dict[str, float], every: float = REPUBLISH_AFTER_SECONDS) -> int:
        """Publishes again what the server hasn't acknowledged for `every` seconds (`sent`: when each was last published)."""
        if self.announce is None:
            return 0
        async with self.store.session() as s:
            rows = (await s.execute(select(InboundRow).where(InboundRow.created_at < datetime.now(timezone.utc) - timedelta(seconds=every))
                                    .order_by(InboundRow.created_at).limit(50))).scalars().all()
        now, n = time.monotonic(), 0
        for row in rows:
            if now - sent.get(row.id, 0.0) < every:
                continue
            sent[row.id] = now
            await self.announce({"id": row.id, "purpose": row.purpose, "account_id": row.account_id, "size": len(open_bytes(row.raw))}, open_bytes(row.raw))
            n += 1
        for gone in set(sent) - {r.id for r in rows}:
            sent.pop(gone, None)
        return n

    async def acknowledged(self, item_id: str) -> None:
        """The server took it in: it is deleted here."""
        async with self.store.session() as s:
            await s.execute(delete(InboundRow).where(InboundRow.id == item_id))
            await s.commit()

    async def prune(self) -> None:
        async with self.store.session() as s:
            await s.execute(delete(InboundRow).where(InboundRow.created_at < datetime.now(timezone.utc) - timedelta(days=settings.INBOUND_KEEP_DAYS)))
            await s.commit()


class Pollers:
    """One poller per job that reads: started when a job begins to read, stopped when it is removed."""

    def __init__(self, reader: Reader):
        self.reader, self.tasks = reader, {}
        self._published: Dict[str, float] = {}

    async def _poll(self, key: str) -> None:
        while True:
            try:
                await self.reader.read(key)
            except (MailError, MailNotPermitted) as e:
                logger.warning("Couldn't read the %s mailbox: %s", key, e)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("Reading the %s mailbox failed", key)
            job = await self.reader.store.job(key)
            await asyncio.sleep(max(15, int((job.settings.get("poll_seconds") if job else None) or settings.MAIL_POLL_SECONDS)))

    async def sync(self) -> None:
        """Starts a poller for each reading job that has none, and stops the ones whose job is gone (or no longer reads)."""
        wanted = {j.key for j in await self.reader.store.jobs() if j.reads}
        for key in wanted - set(self.tasks):
            self.tasks[key] = asyncio.create_task(self._poll(key))
        for key in set(self.tasks) - wanted:
            self.tasks.pop(key).cancel()
        for key, task in list(self.tasks.items()):
            if task.done():  # one that ended on its own is started again
                self.tasks.pop(key)

    async def forever(self) -> None:
        try:
            while True:
                try:
                    await self.sync()
                    await self.reader.republish_stale(self._published)
                    await self.reader.prune()
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001
                    logger.exception("Supervising the mailbox pollers failed")
                await asyncio.sleep(SUPERVISE_SECONDS)
        finally:
            for task in self.tasks.values():
                task.cancel()
