"""
How Syruss works: its platform-wide lessons. Everyone can read the active ones; platform administrators
(the people who run Syruss, not any one organization's admins) approve or retire what Syruss proposes,
edit lessons and add their own.
"""
from typing import Literal, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.auth import AuthContext, get_current_auth
from app.core.database import get_db_session
from app.models.system_lesson import SystemLesson
from app.services import system_lessons

router = APIRouter(prefix="/system-lessons", tags=["How Syruss works"])


def _admin(auth: AuthContext) -> None:
    if not auth.is_platform_admin:
        raise HTTPException(status_code=403, detail="Only platform administrators can change how Syruss works.")


@router.get("")
async def list_lessons(auth: AuthContext = Depends(get_current_auth), db: AsyncSession = Depends(get_db_session)):
    admin = auth.is_platform_admin
    stmt = select(SystemLesson).order_by(SystemLesson.status, SystemLesson.importance.desc(), SystemLesson.created_at)
    if not admin:
        stmt = stmt.where(SystemLesson.status == "active")
    rows = (await db.execute(stmt)).scalars().all()
    return {"items": [system_lessons.lesson_dict(r, with_origin=admin) for r in rows], "can_manage": admin}


class LessonIn(BaseModel):
    content: str = Field(..., min_length=5, max_length=1000)
    importance: float = Field(0.7, ge=0, le=1)


@router.post("", status_code=201)
async def add_lesson(body: LessonIn, auth: AuthContext = Depends(get_current_auth), db: AsyncSession = Depends(get_db_session)):
    _admin(auth)
    lesson = SystemLesson(content=body.content.strip(), importance=body.importance, status="active", source="admin",
                          created_by=auth.user_id)
    db.add(lesson)
    await db.commit()
    return system_lessons.lesson_dict(lesson, with_origin=True)


class LessonPatch(BaseModel):
    status: Optional[Literal["active", "proposed", "retired"]] = None
    content: Optional[str] = Field(None, max_length=1000)
    importance: Optional[float] = Field(None, ge=0, le=1)


@router.patch("/{lesson_id}")
async def update_lesson(lesson_id: str, body: LessonPatch, auth: AuthContext = Depends(get_current_auth),
                        db: AsyncSession = Depends(get_db_session)):
    _admin(auth)
    lesson = await db.get(SystemLesson, lesson_id)
    if not lesson:
        raise HTTPException(status_code=404, detail="Lesson not found.")
    lesson = await system_lessons.review(db, lesson, status=body.status, content=body.content,
                                         importance=body.importance, reviewer=auth.username or auth.user_id)
    return system_lessons.lesson_dict(lesson, with_origin=True)
