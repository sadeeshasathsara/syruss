"""
Custom screens: saved tables over the workspace's saved records, with calculated columns. Anyone who can see documents can open
one; building and changing them takes `screens.manage`. The numbers are computed here (services/screen_engine.py).
"""
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db_session
from app.core.permissions import Access, require
from app.core.scope import TemplateScope, get_template_scope
from app.models.audit import AuditLog
from app.models.template import DocumentTemplate
from app.services import scheduler, screen_engine, screen_service
from app.services.formula import functions_help
from app.services.screen_engine import AGGREGATES, FILTER_OPS, Filter, Sort

router = APIRouter(prefix="/screens", tags=["Custom screens"], dependencies=[Depends(require("documents.view"))])


class ScreenIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: str = Field("", max_length=2000)
    definition: Dict[str, Any]


class ScreenPatch(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=120)
    description: Optional[str] = Field(None, max_length=2000)
    definition: Optional[Dict[str, Any]] = None
    position: Optional[int] = Field(None, ge=0, le=1000)


class RunIn(BaseModel):
    offset: int = Field(0, ge=0)
    limit: int = Field(100, ge=1, le=screen_engine.MAX_PAGE)
    filters: List[Filter] = Field(default_factory=list, max_length=10, description="Extra filters for now, not saved")
    sort: Optional[List[Sort]] = Field(None, max_length=3, description="A different order for now, not saved")
    search: str = Field("", max_length=200, description="Words that must all appear somewhere in a row (for now, not saved)")


class PreviewIn(RunIn):
    definition: Dict[str, Any]


@router.get("")
async def list_screens(db: AsyncSession = Depends(get_db_session), scope: TemplateScope = Depends(get_template_scope)):
    return [screen_service.summary(s) for s in await screen_service.list_screens(db, scope.template_id)]


@router.get("/catalogue")
async def catalogue(scope: TemplateScope = Depends(get_template_scope)):
    """What a screen can be made from: the template's objects and fields, the formula functions, aggregates and filter operators."""
    return {**screen_engine.catalogue(scope.template.schema_definition), "functions": functions_help(),
            "aggregates": list(AGGREGATES), "filter_ops": list(FILTER_OPS)}


@router.get("/starters")
async def starters(scope: TemplateScope = Depends(get_template_scope)):
    """Ready-made screens made from the template (one per repeating object), to start from."""
    return screen_service.starters(scope.template.schema_definition)


@router.post("/preview")
async def preview(body: PreviewIn, db: AsyncSession = Depends(get_db_session), scope: TemplateScope = Depends(get_template_scope)):
    """Runs a definition that is not saved (the builder's live preview). Problems come back as a 422 with a sentence each."""
    parsed = screen_service.check(body.definition, scope.template.schema_definition)
    return await screen_engine.run_screen(db, template_id=scope.template_id, schema=scope.template.schema_definition, definition=parsed,
                                          offset=body.offset, limit=body.limit, extra_filters=body.filters, extra_sort=body.sort, search=body.search)


@router.post("/insights/run")
async def look_for_screens(db: AsyncSession = Depends(get_db_session), scope: TemplateScope = Depends(get_template_scope),
                           access: Access = Depends(require("screens.manage"))):
    """Asks Syruss to look through the saved records now and suggest screens worth having (they arrive under Suggested)."""
    template = await db.get(DocumentTemplate, scope.template_id)
    if not template or not template.active_version_id:
        raise HTTPException(status_code=409, detail="Publish the template first: there are no records to look through yet.")
    result = await scheduler.insights(db, template, requested_by={"id": scope.user_id, "name": scope.username or scope.user_id})
    if result is None:
        raise HTTPException(status_code=409, detail="Syruss is already getting ready to look. Try again in a moment.")
    await db.commit()
    scheduler.agent_event_relay.notify()
    return result


@router.post("", status_code=201)
async def create_screen(body: ScreenIn, db: AsyncSession = Depends(get_db_session), scope: TemplateScope = Depends(get_template_scope),
                        _access: Access = Depends(require("screens.manage"))):
    s = await screen_service.create(
        db, tenant_id=scope.tenant_id, template_id=scope.template_id, schema=scope.template.schema_definition, name=body.name,
        description=body.description, definition=body.definition, actor=scope.user_id, actor_kind="person")
    await db.commit()
    await screen_service.announce(s, "created")
    return screen_service.detail(s)


@router.get("/{slug}")
async def get_screen(slug: str, db: AsyncSession = Depends(get_db_session), scope: TemplateScope = Depends(get_template_scope)):
    return screen_service.detail(await screen_service.get(db, scope.template_id, slug))


@router.patch("/{slug}")
async def update_screen(slug: str, body: ScreenPatch, db: AsyncSession = Depends(get_db_session), scope: TemplateScope = Depends(get_template_scope),
                        _access: Access = Depends(require("screens.manage"))):
    s = await screen_service.get(db, scope.template_id, slug)
    if body.name is not None:
        s.name = body.name.strip()[:120] or s.name
    if body.description is not None:
        s.description = body.description.strip()
    if body.position is not None:
        s.position = body.position
    if body.definition is not None:
        s.definition = screen_service.check(body.definition, scope.template.schema_definition).model_dump(exclude_none=True)
    db.add(AuditLog(tenant_id=scope.tenant_id, template_id=scope.template_id, event_type="SCREEN_UPDATED", entity_type="Screen",
                    entity_id=s.id, actor=scope.user_id, details={"name": s.name}))
    await db.commit()
    await screen_service.announce(s, "updated")
    return screen_service.detail(s)


@router.delete("/{slug}", status_code=204)
async def delete_screen(slug: str, db: AsyncSession = Depends(get_db_session), scope: TemplateScope = Depends(get_template_scope),
                        _access: Access = Depends(require("screens.manage"))):
    s = await screen_service.get(db, scope.template_id, slug)
    db.add(AuditLog(tenant_id=scope.tenant_id, template_id=scope.template_id, event_type="SCREEN_DELETED", entity_type="Screen",
                    entity_id=s.id, actor=scope.user_id, details={"name": s.name}))
    await screen_service.announce(s, "deleted")
    await db.delete(s)
    await db.commit()
    return Response(status_code=204)


@router.post("/{slug}/run")
async def run_screen(slug: str, body: RunIn, db: AsyncSession = Depends(get_db_session), scope: TemplateScope = Depends(get_template_scope)):
    """The screen's rows now. Filters and sort in the request are for this look only; the saved screen is unchanged."""
    s = await screen_service.get(db, scope.template_id, slug)
    result = await screen_engine.run_screen(
        db, template_id=scope.template_id, schema=scope.template.schema_definition,
        definition=screen_engine.ScreenDefinition.model_validate(s.definition), offset=body.offset, limit=body.limit,
        extra_filters=body.filters, extra_sort=body.sort, search=body.search)
    return {"screen": screen_service.summary(s), **result}


@router.get("/{slug}/export.csv")
async def export_screen(slug: str, db: AsyncSession = Depends(get_db_session), scope: TemplateScope = Depends(get_template_scope)):
    s = await screen_service.get(db, scope.template_id, slug)
    result = await screen_engine.run_screen(
        db, template_id=scope.template_id, schema=scope.template.schema_definition,
        definition=screen_engine.ScreenDefinition.model_validate(s.definition), limit=100000, page_cap=100000)
    name = "".join(c if c.isalnum() or c in "-_" else "-" for c in s.slug)
    return Response(content="﻿" + screen_engine.to_csv(result), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})


@router.post("/{slug}/accept")
async def accept_screen(slug: str, db: AsyncSession = Depends(get_db_session), scope: TemplateScope = Depends(get_template_scope),
                        _access: Access = Depends(require("screens.manage"))):
    """Keeps a screen Syruss suggested: it joins the others in the sidebar."""
    s = await screen_service.get(db, scope.template_id, slug)
    s.status = "active"
    db.add(AuditLog(tenant_id=scope.tenant_id, template_id=scope.template_id, event_type="SCREEN_ACCEPTED", entity_type="Screen",
                    entity_id=s.id, actor=scope.user_id, details={"name": s.name}))
    await db.commit()
    await screen_service.announce(s, "accepted")
    return screen_service.detail(s)


@router.post("/{slug}/dismiss", status_code=204)
async def dismiss_screen(slug: str, db: AsyncSession = Depends(get_db_session), scope: TemplateScope = Depends(get_template_scope),
                         _access: Access = Depends(require("screens.manage"))):
    """Turns down a suggestion; it is removed."""
    s = await screen_service.get(db, scope.template_id, slug)
    db.add(AuditLog(tenant_id=scope.tenant_id, template_id=scope.template_id, event_type="SCREEN_DISMISSED", entity_type="Screen",
                    entity_id=s.id, actor=scope.user_id, details={"name": s.name}))
    await screen_service.announce(s, "dismissed")
    await db.delete(s)
    await db.commit()
    return Response(status_code=204)
