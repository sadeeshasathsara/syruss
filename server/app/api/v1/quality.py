"""
How well the agent reads: live quality from the workspace's own decisions and reviews, golden
documents (records people verified), and shadow evaluations against them. Every member can see
quality; people who can publish the template curate golden documents and start evaluations.
"""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db_session
from app.core.permissions import Access, get_access, require
from app.models.evaluation import EvalRun, EvalRunItem
from app.models.template import DocumentTemplate
from app.models.template_enterprise import GoldenDatasetItem
from app.services import eval_service, quality_service
from app.services.agent_events import agent_event_relay

router = APIRouter(prefix="/quality", tags=["Quality"], dependencies=[Depends(require("documents.view"))])


@router.get("")
async def live_quality(days: int = Query(30, ge=1, le=365), access: Access = Depends(get_access),
                       db: AsyncSession = Depends(get_db_session)):
    return await quality_service.live(db, access.scope.template_id, days)


class GoldenIn(BaseModel):
    document_id: str
    name: Optional[str] = Field(None, max_length=255)
    complexity_tag: str = Field("standard", max_length=64)


@router.get("/golden")
async def golden_documents(access: Access = Depends(get_access), db: AsyncSession = Depends(get_db_session)):
    return await eval_service.golden_list(db, access.scope.template_id)


@router.post("/golden", status_code=201)
async def add_golden_document(body: GoldenIn, access: Access = Depends(require("template.publish")),
                              db: AsyncSession = Depends(get_db_session)):
    template = await db.get(DocumentTemplate, access.scope.template_id)
    item = await eval_service.add_golden(db, template=template, document_id=body.document_id, user_id=access.auth.user_id,
                                         name=body.name, complexity_tag=body.complexity_tag)
    await db.commit()
    return {"id": item.id, "name": item.name, "document_id": item.document_id}


@router.delete("/golden/{item_id}", status_code=204)
async def remove_golden_document(item_id: str, access: Access = Depends(require("template.publish")),
                                 db: AsyncSession = Depends(get_db_session)):
    item = await db.get(GoldenDatasetItem, item_id)
    if not item or item.template_id != access.scope.template_id:
        raise HTTPException(status_code=404, detail="Golden document not found.")
    await db.delete(item)
    await db.commit()


async def _run(db: AsyncSession, access: Access, run_id: str) -> EvalRun:
    run = await db.get(EvalRun, run_id)
    if not run or run.template_id != access.scope.template_id:
        raise HTTPException(status_code=404, detail="Evaluation not found.")
    return run


@router.get("/evals")
async def evaluations(access: Access = Depends(get_access), db: AsyncSession = Depends(get_db_session)):
    runs = (await db.execute(select(EvalRun).where(EvalRun.template_id == access.scope.template_id)
                             .order_by(EvalRun.created_at.desc()).limit(50))).scalars().all()
    return [eval_service.run_dict(r) for r in runs]


@router.get("/evals/{run_id}")
async def evaluation(run_id: str, access: Access = Depends(get_access), db: AsyncSession = Depends(get_db_session)):
    run = await _run(db, access, run_id)
    items = (await db.execute(select(EvalRunItem).where(EvalRunItem.run_id == run.id).order_by(EvalRunItem.name))).scalars().all()
    return eval_service.run_dict(run, list(items))


@router.post("/evals", status_code=201)
async def start_evaluation(access: Access = Depends(require("template.publish")), db: AsyncSession = Depends(get_db_session)):
    """The agent re-reads every golden document without changing anything; scores arrive as it goes."""
    template = await db.get(DocumentTemplate, access.scope.template_id)
    run = await eval_service.start(db, template=template, user_id=access.auth.user_id,
                                   user_name=access.auth.username or access.auth.user_id)
    await db.commit()
    agent_event_relay.notify()
    return eval_service.run_dict(run)


@router.post("/evals/{run_id}/cancel")
async def cancel_evaluation(run_id: str, access: Access = Depends(require("template.publish")),
                            db: AsyncSession = Depends(get_db_session)):
    run = await _run(db, access, run_id)
    await eval_service.cancel(db, run)
    await db.commit()
    return eval_service.run_dict(run)
