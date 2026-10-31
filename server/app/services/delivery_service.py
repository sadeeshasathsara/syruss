"""
Getting approved records out to the systems the business runs on.

  enqueue(...)   called inside the transaction that approves a record, so an approved record can never be lost on the way out. It writes one
                 delivery for each destination. A delivery waits (`held`) while anything is in doubt about its document.
  Worker         sends due deliveries, again and again with growing pauses when the other side fails, and gives up (`dead`) only after
                 every try. A destination that keeps failing is paused for the people to look at. Dead deliveries can be replayed.

The rule that matters most: nothing is sent for a document that is not approved, or that has a question, an open conversation or a review
waiting on it. That is checked when the delivery is written and again at the moment it is sent.
"""
import asyncio
import hashlib
import json
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.secrets import unseal
from app.models.conversation import Conversation
from app.models.delivery import Delivery, DeliveryAttempt, Destination
from app.models.document import Document, DocumentRole, DocumentStatus
from app.models.extraction import ExtractionResult
from app.models.question import AgentQuestion, AgentQuestionStatus
from app.models.review import ReviewStatus, ReviewTask
from app.models.template import DocumentTemplate
from app.services import mapping_service, workspace_events
from app.services.destinations import ADAPTERS, SendContext, SendResult
from app.services.ui_events import publish_ui_event

logger = logging.getLogger("core.deliveries")

# Seconds to wait before try 2, 3, 4 ... (the last one repeats). After DELIVERY_MAX_ATTEMPTS tries a delivery is dead.
BACKOFF = [60, 300, 1800, 7200, 21600, 43200, 86400]
STUCK_AFTER = timedelta(minutes=5)
PAUSED = "The destination is paused."
_told: Dict[str, datetime] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def data_hash(data: Any) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


# --- is anything in doubt? --------------------------------------------------------------------------------

async def hold_reasons(db: AsyncSession, template_id: str, documents: Iterable[Document]) -> Dict[str, Optional[str]]:
    """Why each document must not be sent yet, in a sentence a person can act on; None for the ones that are clear."""
    docs = list(documents)
    if not docs:
        return {}
    ids = {d.id for d in docs}
    in_review = {r for (r,) in (await db.execute(select(ReviewTask.document_id).where(
        ReviewTask.template_id == template_id, ReviewTask.document_id.in_(ids), ReviewTask.status == ReviewStatus.PENDING))).all()}
    asked: set = set()
    for q in (await db.execute(select(AgentQuestion).where(AgentQuestion.template_id == template_id, AgentQuestion.status == AgentQuestionStatus.OPEN))).scalars():
        asked |= ({q.related_document_id} if q.related_document_id else set()) | set(q.evidence_document_ids or [])
    talked: set = set()
    for c in (await db.execute(select(Conversation).where(
            Conversation.template_id == template_id, Conversation.kind.in_(("agent", "flag")), Conversation.status == "open"))).scalars():
        talked |= set((c.context_metadata or {}).get("document_ids") or [])
    out: Dict[str, Optional[str]] = {}
    for d in docs:
        if d.status != DocumentStatus.COMPLETED:
            out[d.id] = "The document is not approved yet."
        elif d.id in in_review:
            out[d.id] = "A reviewer still has to look at this document."
        elif d.id in asked:
            out[d.id] = "Syruss asked a question about this document and is waiting for the answer."
        elif d.id in talked:
            out[d.id] = "Syruss opened a conversation about this document that is not settled yet."
        else:
            out[d.id] = None
    return out


async def hold_reason(db: AsyncSession, document: Document) -> Optional[str]:
    return (await hold_reasons(db, document.template_id, [document]))[document.id]


def standing(delivery: Delivery, document: Document, reason: Optional[str]) -> Optional[str]:
    """The reason to hold a delivery, unless a person chose to send it anyway: that overrides doubts, never a missing approval."""
    if reason and (delivery.snapshot or {}).get("override") and document.status == DocumentStatus.COMPLETED:
        return None
    return reason


def override(delivery: Delivery, by: str) -> None:
    delivery.snapshot = {**(delivery.snapshot or {}), "override": {"by": by, "at": datetime.now(timezone.utc).isoformat()}}


# --- writing deliveries -----------------------------------------------------------------------------------

def _snapshot(document: Document, result: ExtractionResult, actor: str) -> Dict[str, Any]:
    source = document.source or {}
    return {
        "document": {
            "id": document.id, "filename": document.original_filename, "content_type": document.content_type, "external_id": document.external_id,
            "source": {"via": source.get("via", "web"), "sender": source.get("sender"), "channel": source.get("channel")},
            "metadata": document.sender_metadata or {}, "received_at": document.created_at.isoformat() if document.created_at else None,
            "test": bool(document.is_test), "storage_key": document.minio_object_key,
        },
        "record": {"revision": result.revision, "data": result.extracted_data, "confidence": result.overall_confidence,
                   "approved_at": _now().isoformat(), "approved_by": actor},
    }


async def enqueue(db: AsyncSession, *, template: DocumentTemplate, document: Document, result: ExtractionResult, actor: str) -> List[Delivery]:
    """Writes the deliveries for a record that was just approved. Runs inside the approving transaction; the caller commits."""
    if document.role != DocumentRole.DOCUMENT:
        return []
    destinations = list((await db.execute(select(Destination).where(Destination.template_id == template.id))).scalars().all())
    if not destinations:
        return []
    created: List[Delivery] = []
    snapshot = _snapshot(document, result, actor)
    digest = data_hash(result.extracted_data)
    holds = await hold_reasons(db, template.id, [document])
    earlier = list((await db.execute(select(Delivery).where(Delivery.document_id == document.id))).scalars().all())
    for dest in destinations:
        if document.is_test and not dest.policy.get("include_test"):
            continue
        mine = [d for d in earlier if d.destination_id == dest.id]
        if any(d.payload_hash == digest and d.status in ("delivered", "pending", "retrying", "sending", "held") for d in mine):
            continue  # approved again with nothing changed: nothing new to tell them
        adapter = ADAPTERS.get(dest.kind)
        sent_before = any(d.status == "delivered" for d in mine)
        for d in mine:
            if d.status in ("held", "pending", "retrying"):
                d.status, d.last_error, d.next_attempt_at = "cancelled", f"Replaced by a newer approval (revision {result.revision}).", None
        row = Delivery(
            tenant_id=dest.tenant_id, template_id=template.id, destination_id=dest.id, document_id=document.id, revision=result.revision,
            event="document.corrected" if sent_before else "document.completed", snapshot=snapshot, payload_hash=digest,
            idempotency_key=f"{dest.id}:{document.id}:{result.revision}",
        )
        wants_update = dest.policy.get("send_updates", True) and bool(adapter and adapter.corrections)
        if sent_before and not wants_update:
            row.status, row.last_error = "cancelled", "Not sent: this destination does not take corrections. Replay it to send it anyway."
        else:
            reason = PAUSED if dest.status == "paused" else holds[document.id]
            row.status, row.hold_reason, row.next_attempt_at = ("held", reason, None) if reason else ("pending", None, _now())
        db.add(row)
        created.append(row)
    if created:
        await db.flush()
    return created


# --- what is sent -----------------------------------------------------------------------------------------

def secrets_of(dest: Destination) -> Dict[str, Any]:
    return unseal(dest.secrets)


def build_envelope(delivery: Delivery, dest: Destination, workspace: Dict[str, str]) -> tuple[Dict[str, Any], List[str]]:
    """(the message, problems). Mapping is applied now, not when the delivery was written, so fixing a mapping fixes a replay."""
    snap = delivery.snapshot
    record = snap["record"]
    adapter = ADAPTERS[dest.kind]
    problems: List[str] = []
    data: Any = record["data"]
    mode = "record"
    if (dest.mapping or {}).get("mode") == "mapped" or adapter.targets:
        required = tuple(t.key for t in adapter.targets if t.required and not t.line)
        data, problems = mapping_service.apply(dest.mapping or {}, record["data"], snap["document"], required)
        mode = "mapped"
    envelope = {
        "id": delivery.id, "type": delivery.event, "created_at": _now().isoformat(), "test": bool(snap["document"].get("test")), "workspace": workspace,
        "document": {k: v for k, v in snap["document"].items() if k != "storage_key"},   # where our own storage keeps the file is not for outsiders
        "record": {"revision": delivery.revision, "mode": mode, "confidence": record.get("confidence"), "approved_at": record.get("approved_at"),
                   "approved_by": record.get("approved_by"), "data": data},
    }
    return envelope, problems


# --- the worker -------------------------------------------------------------------------------------------

def backoff_seconds(attempts: int, retry_after: Optional[int] = None) -> float:
    base = BACKOFF[min(max(attempts, 1) - 1, len(BACKOFF) - 1)]
    return min(86400.0, max(base * random.uniform(0.9, 1.1), float(retry_after or 0)))


async def _tell(db: AsyncSession, dest: Destination, kind: str, title: str, body: str, *, minutes: int = 60) -> None:
    """A notification for the people who manage connections, at most once an hour for each destination and kind."""
    key = f"{dest.id}:{kind}"
    if key in _told and _now() - _told[key] < timedelta(minutes=minutes):
        return
    _told[key] = _now()
    await workspace_events.emit(db, tenant_id=dest.tenant_id, template_id=dest.template_id, type=kind, title=title, body=body,
                                path="connections/publish", severity="error", data={"destination_id": dest.id})


async def pause_destination(db: AsyncSession, dest: Destination, reason: str, by: str) -> bool:
    """Stops sending to it and keeps what is waiting. False if it was already paused (so only one caller announces it)."""
    paused = (await db.execute(update(Destination).where(Destination.id == dest.id, Destination.status == "active")
                               .values(status="paused", paused_reason=reason[:300], paused_by=by).returning(Destination.id)
                               .execution_options(synchronize_session=False))).first()
    if paused is None:
        return False
    await db.execute(update(Delivery).where(Delivery.destination_id == dest.id, Delivery.status.in_(("pending", "retrying")))
                     .values(status="held", hold_reason=PAUSED, next_attempt_at=None).execution_options(synchronize_session=False))
    return True


async def resume_destination(db: AsyncSession, dest: Destination) -> None:
    dest.status, dest.paused_reason, dest.paused_by, dest.consecutive_failures = "active", None, None, 0


async def replay(db: AsyncSession, delivery: Delivery) -> None:
    """Sends it again from the start (the same idempotency key, so a receiver can tell it is the same delivery)."""
    dest = await db.get(Destination, delivery.destination_id)
    delivery.attempts, delivery.last_error, delivery.last_status_code = 0, None, None
    if dest is not None and dest.status == "paused":
        delivery.status, delivery.hold_reason, delivery.next_attempt_at = "held", PAUSED, None
    else:
        delivery.status, delivery.hold_reason, delivery.next_attempt_at = "pending", None, _now()


async def load_secrets(destination_id: str) -> Dict[str, Any]:
    async with AsyncSessionLocal() as db:
        dest = await db.get(Destination, destination_id)
        return unseal(dest.secrets) if dest is not None else {}


async def save_secrets(destination_id: str, changed: Dict[str, Any]) -> None:
    """Merges changed credentials (a new sign-in token) into a destination's sealed secrets, in a transaction of their own so a failed delivery cannot undo it."""
    from app.core.secrets import seal
    async with AsyncSessionLocal() as db:
        dest = (await db.execute(select(Destination).where(Destination.id == destination_id).with_for_update())).scalar_one_or_none()
        if dest is not None:
            dest.secrets = seal({**unseal(dest.secrets), **changed})
            await db.commit()


class DeliveryWorker:
    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._wake = asyncio.Event()

    def start(self) -> None:
        if settings.DELIVERY_WORKER_ENABLED and self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    def notify(self) -> None:
        self._wake.set()

    async def _run(self) -> None:
        while True:
            try:
                busy = await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("Delivery worker tick failed")
                busy = 0
            if busy:
                continue
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=settings.DELIVERY_POLL_SECONDS)
            except asyncio.TimeoutError:
                pass
            self._wake.clear()

    async def tick(self, limit: int = 25) -> int:
        """One pass: release what is clear, rescue what got stuck, send what is due. Returns how many it sent."""
        await self.release_held()
        await self.recover_stuck()
        claimed = await self.claim_due(limit)
        if claimed:
            gate = asyncio.Semaphore(8)

            async def one(delivery_id: str) -> None:
                async with gate:
                    try:
                        await self.process(delivery_id)
                    except Exception:  # noqa: BLE001
                        logger.exception("Delivery %s failed unexpectedly", delivery_id)
                        await self._fail_unexpected(delivery_id)

            await asyncio.gather(*(one(i) for i in claimed))
        return len(claimed)

    async def release_held(self) -> None:
        """Held deliveries whose document is clear now (and whose destination is running) become due."""
        async with AsyncSessionLocal() as db:
            held = list((await db.execute(select(Delivery).where(Delivery.status == "held").order_by(Delivery.created_at).limit(200))).scalars().all())
            if not held:
                return
            destinations = {d.id: d for d in (await db.execute(select(Destination).where(Destination.id.in_({h.destination_id for h in held})))).scalars()}
            by_template: Dict[str, List[Delivery]] = {}
            for h in held:
                by_template.setdefault(h.template_id, []).append(h)
            changed = 0
            for template_id, rows in by_template.items():
                documents = list((await db.execute(select(Document).where(Document.id.in_({r.document_id for r in rows})))).scalars().all())
                reasons = await hold_reasons(db, template_id, documents)
                for row in rows:
                    dest = destinations.get(row.destination_id)
                    doc = next((x for x in documents if x.id == row.document_id), None)
                    reason = PAUSED if dest is not None and dest.status == "paused" else (
                        standing(row, doc, reasons.get(row.document_id)) if doc is not None else "The document is not approved yet.")
                    if reason is None:
                        row.status, row.hold_reason, row.next_attempt_at = "pending", None, _now()
                        changed += 1
                    elif reason != row.hold_reason:
                        row.hold_reason = reason
            await db.commit()
            if changed:
                for template_id in by_template:
                    await publish_ui_event(f"template:{template_id}", "delivery_changed", {"released": changed}, tenant_id=by_template[template_id][0].tenant_id, template_id=template_id)

    async def recover_stuck(self) -> None:
        """A delivery left `sending` by a crashed process is tried again."""
        async with AsyncSessionLocal() as db:
            await db.execute(update(Delivery).where(Delivery.status == "sending", Delivery.locked_at < _now() - STUCK_AFTER)
                             .values(status="retrying", next_attempt_at=_now(), locked_at=None))
            await db.commit()

    async def claim_due(self, limit: int) -> List[str]:
        async with AsyncSessionLocal() as db:
            rows = list((await db.execute(
                select(Delivery).where(Delivery.status.in_(("pending", "retrying")), Delivery.next_attempt_at <= _now())
                .order_by(Delivery.next_attempt_at).limit(limit).with_for_update(skip_locked=True)
            )).scalars().all())
            for row in rows:
                row.status, row.locked_at = "sending", _now()
            await db.commit()
            return [r.id for r in rows]

    async def _fail_unexpected(self, delivery_id: str) -> None:
        async with AsyncSessionLocal() as db:
            d = await db.get(Delivery, delivery_id)
            if d is not None and d.status == "sending":
                d.status, d.next_attempt_at, d.locked_at, d.last_error = "retrying", _now() + timedelta(seconds=backoff_seconds(d.attempts + 1)), None, "An unexpected error stopped this try."
                await db.commit()

    async def process(self, delivery_id: str) -> None:
        async with AsyncSessionLocal() as db:
            d = await db.get(Delivery, delivery_id)
            if d is None or d.status != "sending":
                return
            dest = await db.get(Destination, d.destination_id)
            document = await db.get(Document, d.document_id)
            template = await db.get(DocumentTemplate, d.template_id)
            if dest is None or document is None or template is None:
                return
            d.locked_at = None
            if dest.status == "paused":
                d.status, d.hold_reason, d.next_attempt_at = "held", PAUSED, None
                await db.commit()
                return
            # The check that matters most, made again at the moment of sending: the document may have been re-opened since it was written.
            reason = standing(d, document, await hold_reason(db, document))
            if reason:
                d.status, d.hold_reason, d.next_attempt_at = "held", reason, None
                await db.commit()
                return
            current = document.extraction.revision if document.extraction else d.revision
            if current > d.revision:
                d.status, d.last_error, d.next_attempt_at = "cancelled", f"Replaced by a newer approval (revision {current}).", None
                await db.commit()
                return
            adapter = ADAPTERS.get(dest.kind)
            workspace = {"id": template.id, "name": template.name}
            attempt_no = d.attempts + 1
            if adapter is None:
                result = SendResult(False, error=f"This system ({dest.kind}) is not supported by this version.", retryable=False)
            else:
                envelope, problems = build_envelope(d, dest, workspace)
                if problems:
                    result = SendResult(False, error="The mapping could not be applied: " + " ".join(problems), retryable=False, request=json.dumps(envelope, default=str)[:1500])
                else:
                    ctx = SendContext(destination_id=dest.id, destination_name=dest.name, config=dest.config or {}, secrets=secrets_of(dest), delivery_id=d.id,
                                      idempotency_key=d.idempotency_key, event=d.event, revision=d.revision, envelope=envelope, test=bool(document.is_test), document_key=document.minio_object_key,
                                      save_secrets=lambda changed, _id=dest.id: save_secrets(_id, changed), reload_secrets=lambda _id=dest.id: load_secrets(_id))
                    try:
                        result = await asyncio.wait_for(adapter.send(ctx), timeout=settings.DELIVERY_TIMEOUT_SECONDS * 3)
                    except asyncio.TimeoutError:
                        result = SendResult(False, error="The other side did not answer in time.")
                    except Exception as exc:  # noqa: BLE001  an adapter bug must not lose the delivery
                        logger.exception("Adapter %s raised", dest.kind)
                        result = SendResult(False, error=f"The connector failed: {type(exc).__name__}.")
            db.add(DeliveryAttempt(delivery_id=d.id, attempt_no=attempt_no, ok=result.ok, status_code=result.status_code, error=result.error,
                                   request=result.request[:4000] or None, response=result.response[:4000] or None))
            d.attempts, d.last_status_code = attempt_no, result.status_code
            now = _now()
            if result.ok:
                d.status, d.delivered_at, d.last_error, d.next_attempt_at, d.hold_reason = "delivered", now, None, None, None
                d.external_ref = result.external_ref or d.external_ref
                await db.execute(update(Destination).where(Destination.id == dest.id).values(consecutive_failures=0, last_success_at=now, last_error=None)
                                 .execution_options(synchronize_session=False))
            else:
                d.last_error = (result.error or "Failed.")[:2000]
                max_attempts = int(dest.policy.get("max_attempts") or settings.DELIVERY_MAX_ATTEMPTS)
                if not result.retryable or attempt_no >= max_attempts:
                    d.status, d.next_attempt_at = "dead", None
                else:
                    d.status, d.next_attempt_at = "retrying", now + timedelta(seconds=backoff_seconds(attempt_no, result.retry_after))
                # Counted in the database, not in memory: several deliveries to one destination fail at the same moment.
                counts = result.retryable or result.status_code in (401, 403)
                failures = (await db.execute(update(Destination).where(Destination.id == dest.id).values(
                    last_failure_at=now, last_error=d.last_error[:500],
                    **({"consecutive_failures": Destination.consecutive_failures + 1} if counts else {})
                ).returning(Destination.consecutive_failures).execution_options(synchronize_session=False))).scalar_one()
                if d.status == "dead":
                    await _tell(db, dest, "delivery.dead", f"A delivery to {dest.name} could not be sent",
                                f"{document.original_filename}: {d.last_error} You can replay it once the problem is fixed.")
                if failures >= settings.DELIVERY_BREAKER_FAILURES and await pause_destination(
                        db, dest, f"Paused automatically after {failures} failed tries in a row. Last problem: {d.last_error}", "system"):
                    await _tell(db, dest, "destination.failing", f"{dest.name} was paused",
                                f"It failed {failures} times in a row, so nothing more is sent to it until you resume it. Waiting deliveries are kept.", minutes=1)
            tenant_id, template_id, status = d.tenant_id, d.template_id, d.status
            await db.commit()
            await workspace_events.dispatch(db)
        await publish_ui_event(f"template:{template_id}", "delivery_changed", {"delivery_id": delivery_id, "status": status}, tenant_id=tenant_id, template_id=template_id)


delivery_worker = DeliveryWorker()
