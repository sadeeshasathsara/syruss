from typing import List
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db_session
from app.core.scope import TemplateScope, get_template_scope
from app.schemas.review import ReviewDecisionRequest, ReviewTaskResponse
from app.services.review_service import review_service
from app.core.permissions import Access, require

router = APIRouter(prefix="/reviews", tags=["Human Review"], dependencies=[Depends(require("documents.view"))])


@router.get("", response_model=List[ReviewTaskResponse])
async def list_pending_reviews(
    limit: int = 50,
    offset: int = 0,
    decided: bool = False,
    db: AsyncSession = Depends(get_db_session),
    scope: TemplateScope = Depends(get_template_scope),
):
    """The review queue; `decided=true` lists what people already reviewed, newest first."""
    return await review_service.list_pending(db, scope, limit, offset, decided=decided)


@router.get("/{task_id}", response_model=ReviewTaskResponse)
async def get_review_task(
    task_id: str,
    db: AsyncSession = Depends(get_db_session),
    scope: TemplateScope = Depends(get_template_scope),
):
    return await review_service.get(db, scope, task_id)


@router.post("/{task_id}/decide", response_model=ReviewTaskResponse)
async def decide_review(
    task_id: str,
    decision: ReviewDecisionRequest,
    db: AsyncSession = Depends(get_db_session),
    scope: TemplateScope = Depends(get_template_scope),
    _access: Access = Depends(require("documents.review")),
):
    """APPROVE or CORRECT commits the record; REJECT fails the document. The agent learns from the outcome."""
    return await review_service.decide(db, scope, task_id, decision)
