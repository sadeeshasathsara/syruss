from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db_session
from app.core.permissions import require
from app.core.scope import TemplateScope, get_template_scope
from app.models.extraction import ExtractionResult
from app.schemas.extraction import ExtractionResponse

router = APIRouter(prefix="/extractions", tags=["Extractions"], dependencies=[Depends(require("documents.view"))])


@router.get("/{document_id}", response_model=ExtractionResponse)
async def get_extraction(
    document_id: str,
    db: AsyncSession = Depends(get_db_session),
    scope: TemplateScope = Depends(get_template_scope),
):
    """The record the agent produced for a document, with per-field evidence and rule checks."""
    extraction = (await db.execute(select(ExtractionResult).where(
        ExtractionResult.document_id == document_id,
        ExtractionResult.template_id == scope.template_id,
    ))).scalar_one_or_none()
    if not extraction:
        raise HTTPException(status_code=404, detail="No record for this document yet.")
    return extraction
