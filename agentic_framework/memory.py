"""
The agent's own memory, kept in the agent service's database (never the core server's).

Notes are partitioned by workspace (tenant + template) and come in three kinds:
  observation  something noticed while working, e.g. "'OT' written next to hours on Site 12 rosters"
  lesson       how this business works, learned from answers and corrections,
               e.g. "'OT' means overtime, billed at 1.5x (answer from admin)"
  work_item    work parked until something happens, e.g. waiting for a human's answer

The agent decides what to write. Lessons are always loaded when it wakes up; observations
and work items are loaded by recency and relevance.
"""
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy import JSON, DateTime, Integer, String, Text, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from agentic_framework.config import settings

NOTE_KINDS = ("observation", "lesson", "work_item")


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

    # --- notes -------------------------------------------------------------

    async def add(self, tenant_id: str, template_id: str, kind: str, content: str,
                  tags: Optional[List[str]] = None, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if kind not in NOTE_KINDS:
            raise ValueError(f"kind must be one of {NOTE_KINDS}")
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
        words = [w for w in query.lower().split() if len(w) > 2][:8] or [query.lower()]
        async with self._session() as s:
            stmt = select(Note).where(Note.tenant_id == tenant_id, Note.template_id == template_id, Note.status == "active")
            if kind:
                stmt = stmt.where(Note.kind == kind)
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
