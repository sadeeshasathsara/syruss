"""
The directory: this service's public state, kept on a compacted Kafka topic (mail.directory) so the server (and, through it,
the admin app) always knows what mail looks like without ever being told a setting. One message per thing, keyed by it:

    meta            the public key people seal secrets to (and its fingerprint), which providers can be connected
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

    async def snapshot(self) -> Dict[str, Dict[str, Any]]:
        jobs = await self.store.jobs()
        routes = await self.store.routes()
        keys = [j.key for j in jobs]
        out: Dict[str, Dict[str, Any]] = {}
        _, public = await self.store.box()
        out["meta"] = {"public_key": base64.b64encode(public).decode(), "fingerprint": sealing.fingerprint(public),
                       "providers": {p: apps.describe(p) for p in apps.PROVIDERS}}
        if self.router.fallback.kind != "none":
            out["account:" + self.router.fallback.id] = describe_account(None, self.router.fallback, routes, keys)
        for row in await self.store.accounts():
            out["account:" + row.id] = describe_account(row, self.router.fallback, routes, keys)
        async with self.store.session() as s:
            sent = dict((await s.execute(select(JobRow.purpose, func.max(JobRow.done_at)).where(JobRow.status == "sent").group_by(JobRow.purpose))).all())
            read = dict((await s.execute(select(CursorRow.purpose, func.max(CursorRow.read_at)).group_by(CursorRow.purpose))).all())
            failing = {}
            for purpose, error, at in (await s.execute(select(JobRow.purpose, JobRow.last_error, JobRow.created_at)
                                                        .where(JobRow.status.in_(("dead", "retrying")), JobRow.last_error.is_not(None))
                                                        .order_by(JobRow.created_at))).all():
                failing[purpose] = error
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
                "last_error": failing.get(job.key),
            }
        return out

    async def publish_all(self, topic: str) -> int:
        """Publishes every thing, and a tombstone for each one that was published before and is gone now."""
        state = await self.snapshot()
        for key, value in state.items():
            await self.send(topic, key, json.dumps(value, default=str).encode())
        for gone in self._known - set(state):
            await self.send(topic, gone, None)
        self._known = set(state)
        return len(state)
