"""
The agent's own memory store.

Pillar 4 — Single Store Unification:
  As of this release, lessons and observations live in the core server's /memories store (the
  unified single source of truth). This module retains the MemoryStore class and the Note ORM
  model for two reasons:
    1. The `work_item` kind is an agent-internal TODO list (things parked waiting for a human
       answer). It is intentionally NOT memory — it has no importance, no embeddings, no decay.
       It stays local to the agent service and continues to use the memory_notes table.
    2. Backward compatibility: callers that ask `memory.list(…, "lesson")` or
       `memory.list(…, "observation")` now receive an empty list, which makes the old
       `_move_older_notes()` migration bridge a no-op (there is nothing to migrate).

The database schema (memory_notes table) is NOT dropped: existing rows are preserved for
auditability and may be inspected on the memory admin page. Only new writes are redirected.

NOTE_KINDS accepted by add() now only includes work_item; lesson/observation calls raise
ValueError so callers notice and switch to `save_memory()` in agent/tools/memory.py.
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Dict, List, Optional
from sqlalchemy import JSON, DateTime, Integer, String, Text, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from agentic_framework.config import settings

logger = logging.getLogger("agent.memory_store")

# work_item is the only local kind now; lesson & observation live in the core server
NOTE_KINDS = ("work_item",)
# kept for external code that may import this symbol (briefing.py, tests)
_LEGACY_NOTE_KINDS = ("observation", "lesson", "work_item")


class Base(DeclarativeBase):
    pass


class Note(Base):
    __tablename__ = "memory_notes"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: f"n_{uuid.uuid4().hex[:12]}")
    tenant_id: Mapped[str] = mapped_column(String(64), index=True)
    template_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(20), index=True)
    content: Mapped[str] = mapped_column(Text)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    # observation: {evidence_document_ids, count}; work_item: {question_id, document_ids}
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)  # active | done | archived
    use_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )


class ProcessedEvent(Base):
    """Makes event handling idempotent: Kafka may deliver an event more than once."""
    __tablename__ = "processed_events"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(64))
    template_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32))
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


def note_dict(n: Note) -> Dict[str, Any]:
    return {
        "id": n.id, "kind": n.kind, "content": n.content, "tags": n.tags, "data": n.data,
        "status": n.status, "created_at": n.created_at.isoformat() if n.created_at else None,
    }


class MemoryStore:
    def __init__(self, database_url: Optional[str] = None):
        self._url = database_url or settings.MEMORY_DATABASE_URL
        self._engine = create_async_engine(self._url, pool_size=5, max_overflow=5)
        self._session = async_sessionmaker(self._engine, expire_on_commit=False, class_=AsyncSession)

    async def init(self) -> None:
        await self._ensure_database()
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def _ensure_database(self) -> None:
        url = self._engine.url
        admin = create_async_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
        try:
            async with admin.connect() as conn:
                exists = await conn.scalar(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": url.database})
                if not exists:
                    await conn.execute(text(f'CREATE DATABASE "{url.database}"'))
        finally:
            await admin.dispose()

    async def close(self) -> None:
        await self._engine.dispose()

    # --- idempotency -------------------------------------------------------

    async def was_processed(self, event_id: str) -> bool:
        async with self._session() as s:
            return await s.get(ProcessedEvent, event_id) is not None

    async def mark_processed(self, event_id: str, event_type: str, template_id: str, status: str) -> None:
        async with self._session() as s:
            await s.merge(ProcessedEvent(event_id=event_id, event_type=event_type, template_id=template_id, status=status))
            await s.commit()

    # --- work items (the only local notes kind) ----------------------------

    async def add(self, tenant_id: str, template_id: str, kind: str, content: str,
                  tags: Optional[List[str]] = None, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Adds a work_item to the local store.

        Pillar 4: lesson and observation are now rejected here — they must be written
        through save_memory() in agentic_framework/agent/tools/memory.py, which routes
        them to the core server's /memories endpoint.
        """
        if kind not in NOTE_KINDS:
            raise ValueError(
                f"kind must be one of {NOTE_KINDS}. "
                f"Lessons and observations now live in the core /memories store; "
                f"use save_memory() in agent/tools/memory.py instead."
            )
        async with self._session() as s:
            note = Note(tenant_id=tenant_id, template_id=template_id, kind=kind, content=content.strip(),
                        tags=[t.lower() for t in (tags or [])], data=data or {})
            s.add(note)
            await s.commit()
            return note_dict(note)

    async def update(self, tenant_id: str, template_id: str, note_id: str, *,
                     content: Optional[str] = None, status: Optional[str] = None,
                     data: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        async with self._session() as s:
            note = await s.get(Note, note_id)
            if not note or note.template_id != template_id or note.tenant_id != tenant_id:
                return None
            if content is not None:
                note.content = content.strip()
            if status is not None:
                note.status = status
            if data is not None:
                note.data = {**(note.data or {}), **data}
            await s.commit()
            return note_dict(note)

    async def list(self, tenant_id: str, template_id: str, kind: str, *, status: str = "active",
                   limit: int = 30) -> List[Dict[str, Any]]:
        """
        Returns active notes of the given kind.

        Pillar 4: asking for 'lesson' or 'observation' returns [] — these kinds now live
        in the core server and are retrieved via /memories/search. This makes the legacy
        _move_older_notes() migration bridge a no-op (nothing to migrate).
        """
        if kind not in NOTE_KINDS:
            # Silently return empty rather than raise, so existing callers like briefing.py
            # and _move_older_notes() degrade gracefully without code changes.
            logger.debug("MemoryStore.list(%s) → empty: kind '%s' now lives in core /memories", template_id, kind)
            return []
        async with self._session() as s:
            stmt = (
                select(Note)
                .where(Note.tenant_id == tenant_id, Note.template_id == template_id, Note.kind == kind, Note.status == status)
                .order_by(Note.updated_at.desc())
                .limit(limit)
            )
            return [note_dict(n) for n in (await s.execute(stmt)).scalars().all()]

    async def search(self, tenant_id: str, template_id: str, query: str, *, kind: Optional[str] = None,
                     limit: int = 15) -> List[Dict[str, Any]]:
        """Full-text search over local work items only (lessons/observations live in core)."""
        if kind and kind not in NOTE_KINDS:
            return []  # lesson/observation search → empty; use /memories/search instead
        words = [w for w in query.lower().split() if len(w) > 2][:8] or [query.lower()]
        async with self._session() as s:
            stmt = select(Note).where(Note.tenant_id == tenant_id, Note.template_id == template_id, Note.status == "active")
            if kind and kind in NOTE_KINDS:
                stmt = stmt.where(Note.kind == kind)
            else:
                stmt = stmt.where(Note.kind.in_(list(NOTE_KINDS)))
            stmt = stmt.where(or_(*[func.lower(Note.content).contains(w) for w in words]))
            notes = (await s.execute(stmt.order_by(Note.updated_at.desc()).limit(limit * 3))).scalars().all()
            ranked = sorted(notes, key=lambda n: sum(w in n.content.lower() for w in words), reverse=True)[:limit]
            for n in ranked:
                n.use_count += 1
            await s.commit()
            return [note_dict(n) for n in ranked]

    async def work_items_for_question(self, tenant_id: str, template_id: str, question_id: str) -> List[Dict[str, Any]]:
        items = await self.list(tenant_id, template_id, "work_item", limit=100)
        return [i for i in items if i["data"].get("question_id") == question_id]

    async def work_items_for_document(self, tenant_id: str, template_id: str, document_id: str) -> List[Dict[str, Any]]:
        items = await self.list(tenant_id, template_id, "work_item", limit=100)
        return [i for i in items if document_id in i["data"].get("document_ids", [])]
