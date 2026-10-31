"""
Syruss's system memory (app.models.system_lesson): lessons about working in the platform itself.

Every run's briefing carries the active lessons. The agent can propose a lesson; it stays a proposal
until a platform admin approves it. The seed lessons below are written once (by id) and can then be
edited or retired like any other.
"""
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.system_lesson import SystemLesson

MAX_ACTIVE_IN_BRIEFING = 30

SEED: List[Dict[str, Any]] = [
    {"id": "sys_no_guessing", "importance": 1.0, "content":
        "Never guess who someone is. If an ID or a name can't be confirmed by known entities, past records or a "
        "person's answer, record exactly what is written and don't treat it as certain."},
    {"id": "sys_clear_or_ask", "importance": 1.0, "content":
        "When nothing in the database confirms what you read (no matching records or entities yet), commit only if "
        "every field is completely clear on the page. If anything is unclear, flag it (one quick question), open a "
        "conversation if it needs discussion, or send the document to review."},
    {"id": "sys_refusals", "importance": 0.9, "content":
        "A refusal from the platform is information: read its reason and do what it says (ask a person, correct, "
        "send to review). Never resubmit the same record unchanged."},
    {"id": "sys_unknown_people", "importance": 0.9, "content":
        "Where registering people and things isn't allowed, a commit naming someone nobody knows is refused. Ask "
        "first (one flag listing them, with the document parked), then register what a person approved and commit."},
    {"id": "sys_records_first", "importance": 0.8, "content":
        "Answer questions about saved documents from the database (query_records, get_document, search_past_records). "
        "Look at a page image only when the record can't answer: pages are costly and capped."},
    {"id": "sys_compare_records", "importance": 0.7, "content":
        "To compare with earlier documents, compare their records, not their pages."},
]


def _normalize(text: str) -> str:
    return re.sub(r"[^\w ]+", "", re.sub(r"\s+", " ", text.strip().lower()))


def lesson_dict(lesson: SystemLesson, with_origin: bool = False) -> Dict[str, Any]:
    out = {"id": lesson.id, "content": lesson.content, "status": lesson.status, "source": lesson.source,
           "importance": lesson.importance, "why": lesson.why, "times_proposed": lesson.times_proposed,
           "created_at": lesson.created_at, "reviewed_by": lesson.reviewed_by, "reviewed_at": lesson.reviewed_at}
    if with_origin:
        out["template_id"] = lesson.template_id
    return out


async def ensure_seeded(db: AsyncSession) -> None:
    """Writes any seed lesson that isn't there yet (by id); existing ones keep their edits."""
    existing = set((await db.execute(select(SystemLesson.id).where(SystemLesson.id.in_([s["id"] for s in SEED])))).scalars())
    for seed in SEED:
        if seed["id"] not in existing:
            db.add(SystemLesson(id=seed["id"], content=seed["content"], importance=seed["importance"],
                                status="active", source="seed", created_by="system"))
    await db.commit()


async def active(db: AsyncSession) -> List[SystemLesson]:
    return list((await db.execute(
        select(SystemLesson).where(SystemLesson.status == "active")
        .order_by(SystemLesson.importance.desc(), SystemLesson.created_at).limit(MAX_ACTIVE_IN_BRIEFING)
    )).scalars().all())


async def propose(db: AsyncSession, *, content: str, why: str, template_id: Optional[str], event_id: Optional[str]) -> Dict[str, Any]:
    """The agent proposes a lesson. Saying the same thing again strengthens the existing one instead."""
    key = _normalize(content)
    for lesson in (await db.execute(select(SystemLesson).where(SystemLesson.status != "retired"))).scalars().all():
        if _normalize(lesson.content) == key:
            lesson.times_proposed += 1
            await db.commit()
            return {**lesson_dict(lesson), "already_known": True}
    lesson = SystemLesson(content=content.strip(), why=why.strip() or None, status="proposed", source="agent",
                          template_id=template_id, event_id=event_id)
    db.add(lesson)
    await db.commit()
    return lesson_dict(lesson)


async def review(db: AsyncSession, lesson: SystemLesson, *, status: Optional[str], content: Optional[str],
                 importance: Optional[float], reviewer: str) -> SystemLesson:
    if status:
        lesson.status = status
    if content is not None and content.strip():
        lesson.content = content.strip()
    if importance is not None:
        lesson.importance = importance
    lesson.reviewed_by, lesson.reviewed_at = reviewer, datetime.now(timezone.utc)
    await db.commit()
    return lesson
