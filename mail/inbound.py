"""
Reading mailboxes. Each job that reads (help, document inbound, and any added in the admin app) has a poller of its own
that reads its account's mailbox every `poll_seconds` (the job's setting, else MAIL_POLL_SECONDS), opened for reading only,
from where it last left off (mail_cursors, per account and job). A slow or failing mailbox never holds up another. Each new
message is kept here sealed (mail_inbound) and announced on mail.inbound; the server collects it and says when it is done.

The first read of a mailbox only notes where it is: what was there already is left alone. Mail nobody collects is
dropped after INBOUND_KEEP_DAYS.
"""
import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional
from sqlalchemy import delete
from mail.accounts.base import Capability, MailError, MailNotPermitted
from mail.config import settings
from mail.router import Router
from mail.sealing import seal_bytes
from mail.store import CursorRow, InboundRow, Store

logger = logging.getLogger("mail.inbound")
Announce = Callable[[Dict[str, Any]], Awaitable[None]]
SUPERVISE_SECONDS = 10


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
        kept: List[Dict[str, Any]] = []
        async with self.store.session() as s:
            for raw in raws:
                item = InboundRow(id=str(uuid.uuid4()), purpose=purpose, account_id=account.id, raw=seal_bytes(raw))
                s.add(item)
                kept.append({"id": item.id, "purpose": purpose, "account_id": account.id, "size": len(raw)})
            await s.merge(CursorRow(account_id=account.id, purpose=purpose, cursor=new_cursor))
            await s.commit()  # the messages and the new cursor together: nothing is read twice or lost
        for event in kept:
            if self.announce:
                await self.announce(event)
        if kept:
            logger.info("Read %d new message(s) for %s from %s", len(kept), purpose, account.address)
        return len(kept)

    async def prune(self) -> None:
        async with self.store.session() as s:
            await s.execute(delete(InboundRow).where(InboundRow.created_at < datetime.now(timezone.utc) - timedelta(days=settings.INBOUND_KEEP_DAYS)))
            await s.commit()


class Pollers:
    """One poller per job that reads: started when a job begins to read, stopped when it is removed."""

    def __init__(self, reader: Reader):
        self.reader, self.tasks = reader, {}

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
                    await self.reader.prune()
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001
                    logger.exception("Supervising the mailbox pollers failed")
                await asyncio.sleep(SUPERVISE_SECONDS)
        finally:
            for task in self.tasks.values():
                task.cancel()


def kafka_announcer(producer) -> Announce:
    async def announce(event: Dict[str, Any]) -> None:
        try:
            await producer.send_and_wait(settings.MAIL_INBOUND_TOPIC, key=event["id"].encode(), value=json.dumps(event).encode())
        except Exception:  # noqa: BLE001  (the message stays; the server also collects anything left on a schedule)
            logger.exception("Couldn't announce inbound mail %s", event["id"])
    return announce
