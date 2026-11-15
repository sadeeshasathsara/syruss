"""
Reading mailboxes. Every MAIL_POLL_SECONDS, each purpose that reads (support) reads its account's mailbox, opened for
reading only, from where it last left off (mail_cursors, per account and purpose). Each new message is kept here
(mail_inbound) and announced on mail.inbound; the server collects it through the API (api.py) and says when it is done.

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
from mail.accounts.base import Capability, MailError
from mail.config import settings
from mail.router import PURPOSES, Router
from mail.store import CursorRow, InboundRow, Store

logger = logging.getLogger("mail.inbound")
Announce = Callable[[Dict[str, Any]], Awaitable[None]]


class Reader:
    def __init__(self, store: Store, router: Router, announce: Optional[Announce] = None):
        self.store, self.router, self.announce = store, router, announce

    async def read(self, purpose: str) -> int:
        """Reads one purpose's mailbox once. Returns how many new messages were kept."""
        account, _ = await self.router.account_for(purpose)
        if Capability.READ not in account.granted:
            return 0
        handle = account.open(Capability.READ)  # reading only, even when the purpose also sends
        async with self.store.session() as s:
            row = await s.get(CursorRow, (account.id, purpose))
            cursor = dict(row.cursor) if row is not None else None
        new_cursor, raws = await handle.fetch(cursor)
        kept: List[Dict[str, Any]] = []
        async with self.store.session() as s:
            for raw in raws:
                item = InboundRow(id=str(uuid.uuid4()), purpose=purpose, account_id=account.id, raw=raw)
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

    async def forever(self) -> None:
        while True:
            for purpose, needs in PURPOSES.items():
                if Capability.READ not in needs:
                    continue
                try:
                    await self.read(purpose)
                except MailError as e:
                    logger.warning("Couldn't read the %s mailbox: %s", purpose, e)
                except Exception:  # noqa: BLE001
                    logger.exception("Reading the %s mailbox failed", purpose)
            try:
                await self.prune()
            except Exception:  # noqa: BLE001
                logger.exception("Pruning collected mail failed")
            await asyncio.sleep(settings.MAIL_POLL_SECONDS)


def kafka_announcer(producer) -> Announce:
    async def announce(event: Dict[str, Any]) -> None:
        try:
            await producer.send_and_wait(settings.MAIL_INBOUND_TOPIC, key=event["id"].encode(), value=json.dumps(event).encode())
        except Exception:  # noqa: BLE001  (the message stays; the server also collects anything left on a schedule)
            logger.exception("Couldn't announce inbound mail %s", event["id"])
    return announce
