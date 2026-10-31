"""
The extraction log, for everyone in the workspace to read. There is nothing here that changes it: entries are written
by the system as documents move, and read a page at a time (newest first) with live additions on the workspace channel.
"""
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db_session
from app.core.scope import TemplateScope, get_template_scope
from app.services import extraction_log

router = APIRouter(prefix="/extraction-log", tags=["Extraction log"])


@router.get("")
async def list_log(
    limit: int = Query(50, ge=1, le=extraction_log.PAGE_MAX),
    before: Optional[int] = Query(None, ge=1, description="The seq of the oldest entry you already have"),
    level: Optional[str] = Query(None, pattern="^(info|success|warning|error|problems)$"),
    document_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db_session),
    scope: TemplateScope = Depends(get_template_scope),
):
    """Any member of the workspace may read it, whatever their role."""
    return await extraction_log.page(db, scope.template_id, limit=limit, before=before, level=level, document_id=document_id)
