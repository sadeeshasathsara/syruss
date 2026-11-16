"""
The directory: this service's public state, kept on a compacted Kafka topic (mail.directory) so the server (and, through it,
the admin app) always knows what mail looks like without ever being told a setting. One message per thing, keyed by it:

    meta            the public key people seal secrets to (and its fingerprint), which providers can be connected
    use:<key>       a function of the application that needs mail, and the job it uses now
    job:<key>       a job: its label, what it does, the account that serves it, when it last sent and read, its last error
    account:<id>    a connected mailbox: its address, kind, what it may do, its status, which jobs it serves

Nothing secret is ever in it: no password, token or username, no message text. A thing that goes away is published as a
tombstone (an empty value), which compaction then removes.
"""
import base64
import json
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set

from sqlalchemy import func, select

from mail import apps, sealing
from mail.accounts.base import Capability
from mail.router import Router
from mail.store import AccountRow, CursorRow, JobRow, Store

logger = logging.getLogger("mail.directory")
Send = Callable[[str, str, Optional[bytes]], Awaitable[None]]   # (topic, key, value; None is a tombstone)


def describe_account(row: Optional[AccountRow], fallback: Any, routes: Dict[str, str], job_keys: List[str]) -> Dict[str, Any]:
    """An account as people may see it: never its config beyond the host names, never a secret."""
    if row is None:
        a = fallback.describe()
        return {"id": a["id"], "kind": a["kind"], "address": a["address"], "name": a.get("name"), "granted": a.get("granted", []),
                "status": "active", "last_error": None, "checked_at": None, "created_at": None, "from_environment": True,
                "hosts": {}, "jobs": [k for k in job_keys if k not in routes]}
    c = row.config or {}
    return {"id": row.id, "kind": row.kind, "address": row.address, "name": row.name, "granted": row.granted, "status": row.status,
            "last_error": row.last_error, "checked_at": row.checked_at.isoformat() if row.checked_at else None,
            "created_at": row.created_at.isoformat() if row.created_at else None, "from_environment": False,
            "hosts": {k: c[k] for k in ("smtp_host", "imap_host") if c.get(k)}, "jobs": [p for p, a in routes.items() if a == row.id]}


class Directory:
    def __init__(self, store: Store, router: Router, send: Send):
        self.store, self.router, self.send = store, router, send
        self._known: Set[str] = set()
        self._primed = False

    async def snapshot(self) -> Dict[str, Dict[str, Any]]:
        jobs = await self.store.jobs()
        routes = await self.store.routes()
        keys = [j.key for j in jobs]
        out: Dict[str, Dict[str, Any]] = {}
        _, public = await self.store.box()
        signer_private, signer_public = await self.store.signer()
        key, fingerprint = base64.b64encode(public).decode(), sealing.fingerprint(public)
        out["meta"] = {"public_key": key, "fingerprint": fingerprint, "providers": {p: apps.describe(p) for p in apps.PROVIDERS},
                       # signed, so a server that knows our signing key believes this key only when we said it (Kafka has no login)
                       "signer": base64.b64encode(signer_public).decode(),
                       "signature": base64.b64encode(sealing.sign(signer_private, sealing.directory_message(key, fingerprint))).decode()}
        if self.router.fallback.kind != "none":
            out["account:" + self.router.fallback.id] = describe_account(None, self.router.fallback, routes, keys)
        for row in await self.store.accounts():
            out["account:" + row.id] = describe_account(row, self.router.fallback, routes, keys)
        async with self.store.session() as s:
            sent = dict((await s.execute(select(JobRow.purpose, func.max(JobRow.done_at)).where(JobRow.status == "sent").group_by(JobRow.purpose))).all())
            read = dict((await s.execute(select(CursorRow.purpose, func.max(CursorRow.read_at)).group_by(CursorRow.purpose))).all())
            # How each job's latest message went: a problem is only one that is still true (the latest message failed, and through the
            # account that serves the job now). A mailbox that has since been changed, or a message that has since gone out, clears it.
            latest = {purpose: (status, error, account_id) for purpose, status, error, account_id in (await s.execute(
                select(JobRow.purpose, JobRow.status, JobRow.last_error, JobRow.account_id)
                .distinct(JobRow.purpose).order_by(JobRow.purpose, JobRow.created_at.desc()))).all()}
        for use in await self.store.uses():
            out["use:" + use.key] = {"key": use.key, "label": use.label, "description": use.description, "direction": use.direction,
                                     "needs": use.needs.names(), "default_job": use.default_job, "job": use.effective_job, "assigned": bool(use.job)}
        for job in jobs:
            account, routed = await self.router.account_for(job.key)
            out["job:" + job.key] = {
                "key": job.key, "label": job.label, "direction": job.direction, "description": job.description, "system": job.system,
                "settings": job.settings, "needs": job.needs.names(),
                "account": ({"id": account.id, "address": account.address, "kind": account.kind, "from_environment": account.id == self.router.fallback.id}
                            if account.kind != "none" else None),
                "routed": routed, "reads": Capability.READ in job.needs and Capability.READ in account.granted,
                "last_sent_at": sent[job.key].isoformat() if sent.get(job.key) else None,
                "last_read_at": read[job.key].isoformat() if read.get(job.key) else None,
                "last_error": self._problem(latest.get(job.key), account),
            }
        return out

    @staticmethod
    def _problem(latest: Optional[tuple], account: Any) -> Optional[str]:
        if not latest:
            return None
        status, error, account_id = latest
        if status not in ("dead", "retrying") or not error:
            return None
        return error if account_id in (None, account.id) else None

    async def _on_topic(self, topic: str) -> Set[str]:
        """The keys already on the (compacted) topic, so a thing published by an earlier run that no longer exists can be removed."""
        from aiokafka import AIOKafkaConsumer
        from mail.config import settings
        from mail.kafka_security import kafka_security
        consumer = AIOKafkaConsumer(topic, bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS, **kafka_security(), group_id=None,
                                    enable_auto_commit=False, auto_offset_reset="earliest")
        keys: Set[str] = set()
        await consumer.start()
        try:
            end = await consumer.end_offsets(list(consumer.assignment()))
            behind = True
            while behind:
                for batch in (await consumer.getmany(timeout_ms=1000)).values():
                    for message in batch:
                        if message.key is None:
                            continue
                        (keys.add if message.value else keys.discard)(message.key.decode())
                behind = False
                for tp, offset in end.items():
                    if await consumer.position(tp) < offset:
                        behind = True
        finally:
            await consumer.stop()
        return keys

    async def publish_all(self, topic: str) -> int:
        """Publishes every thing, and a tombstone for each one that was published before and is gone now (including by an earlier run:
        the first time, what is already on the topic is read)."""
        if not self._primed:
            try:
                self._known |= await self._on_topic(topic)
                self._primed = True
            except Exception:  # noqa: BLE001  (the topic may not be readable yet: publish anyway, and try again next time)
                logger.exception("Couldn't read what is already in the directory")
        state = await self.snapshot()
        for key, value in state.items():
            await self.send(topic, key, json.dumps(value, default=str).encode())
        for gone in self._known - set(state):
            await self.send(topic, gone, None)
        self._known = set(state)
        return len(state)
