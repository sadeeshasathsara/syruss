"""
How Syruss works: the platform-wide lessons every workspace's agent is briefed with. The agent proposes them; platform
staff with system.manage approve, edit, retire or add them here. Every change is in the platform audit log.
"""
from typing import Any, Dict, Literal, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.v1.sysadmin.access import Staff, record, require_staff
from app.core.database import get_db_session
from app.models.system_lesson import SystemLesson
from app.services import system_lessons

router = APIRouter(prefix="/lessons")


@router.get("", dependencies=[Depends(require_staff())])
async def lessons(db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    rows = (await db.execute(select(SystemLesson).order_by(SystemLesson.status, SystemLesson.importance.desc(), SystemLesson.created_at))).scalars().all()
    return {"items": [system_lessons.lesson_dict(r, with_origin=True) for r in rows]}


class LessonIn(BaseModel):
    content: str = Field(..., min_length=5, max_length=1000)
    importance: float = Field(0.7, ge=0, le=1)


@router.post("", status_code=201)
async def add(body: LessonIn, staff: Staff = Depends(require_staff("system.manage")), db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    lesson = SystemLesson(content=body.content.strip(), importance=body.importance, status="active", source="admin", created_by=staff.user_id)
    db.add(lesson)
    await db.flush()
    record(db, staff, "lesson.added", "lesson", lesson.id, details={"content": lesson.content[:200]})
    await db.commit()
    return system_lessons.lesson_dict(lesson, with_origin=True)


class LessonPatch(BaseModel):
    status: Optional[Literal["active", "proposed", "retired"]] = None
    content: Optional[str] = Field(None, max_length=1000)
    importance: Optional[float] = Field(None, ge=0, le=1)


@router.patch("/{lesson_id}")
async def update(lesson_id: str, body: LessonPatch, staff: Staff = Depends(require_staff("system.manage")),
                 db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    lesson = await db.get(SystemLesson, lesson_id)
    if lesson is None:
        raise HTTPException(status_code=404, detail="Lesson not found.")
    record(db, staff, f"lesson.{body.status or 'edited'}", "lesson", lesson.id, details={"content": lesson.content[:200]})
    lesson = await system_lessons.review(db, lesson, status=body.status, content=body.content, importance=body.importance, reviewer=staff.name)
    return system_lessons.lesson_dict(lesson, with_origin=True)
