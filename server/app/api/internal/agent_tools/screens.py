"""
Custom screens.
"""
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from app.services import workspace_events
from app.models.screen import WorkspaceScreen
from app.services import formula, screen_engine, screen_service
from app.api.internal.agent_tools.scope import Workspace, get_workspace
from app.api.internal.agent_tools.common import audit, on_a_persons_request


router = APIRouter()


MAX_PENDING_SUGGESTIONS = 5


class ScreenBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: str = Field("", max_length=2000)
    definition: Dict[str, Any] = Field(..., description="The screen: source, columns, filters, sort, group_by, totals")
    rationale: Optional[str] = Field(None, max_length=1000, description="Why this screen is worth having (shown with a suggestion)")


class ScreenChange(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=120)
    description: Optional[str] = Field(None, max_length=2000)
    definition: Optional[Dict[str, Any]] = None


class ScreenRunBody(BaseModel):
    definition: Optional[Dict[str, Any]] = Field(None, description="Only for /screens/preview: the unsaved screen to try")
    offset: int = Field(0, ge=0)
    limit: int = Field(25, ge=1, le=200)
    filters: List[screen_engine.Filter] = Field(default_factory=list, max_length=10)
    sort: Optional[List[screen_engine.Sort]] = Field(None, max_length=3)
    search: str = Field("", max_length=200, description="Words that must all appear somewhere in a row")


@router.get("/screens")
async def agent_list_screens(ws: Workspace = Depends(get_workspace)):
    """The workspace's screens with their definitions (suggested ones too), so you can build on them rather than repeat them."""
    return [screen_service.detail(s) for s in await screen_service.list_screens(ws.db, ws.template_id)]


@router.get("/screens/catalogue")
async def agent_screen_catalogue(ws: Workspace = Depends(get_workspace)):
    return {**screen_engine.catalogue(ws.template.schema_definition), "functions": formula.functions_help(),
            "aggregates": list(screen_engine.AGGREGATES), "filter_ops": list(screen_engine.FILTER_OPS)}


@router.post("/screens/preview")
async def agent_preview_screen(body: ScreenRunBody, ws: Workspace = Depends(get_workspace)):
    """Tries a screen without saving it: what ad-hoc analysis is. Problems come back as sentences to fix."""
    if body.definition is None:
        raise HTTPException(status_code=422, detail="Send the definition to try.")
    parsed = screen_service.check(body.definition, ws.template.schema_definition)
    return await screen_engine.run_screen(ws.db, template_id=ws.template_id, schema=ws.template.schema_definition, definition=parsed, hidden=ws.hidden,
                                          offset=body.offset, limit=body.limit, extra_filters=body.filters, extra_sort=body.sort)


@router.post("/screens/{slug}/run")
async def agent_run_screen(slug: str, body: ScreenRunBody, ws: Workspace = Depends(get_workspace)):
    """A saved screen's rows now, with extra filters or another order for this look only: how you answer from a screen."""
    s = await screen_service.get(ws.db, ws.template_id, slug)
    result = await screen_engine.run_screen(
        ws.db, template_id=ws.template_id, schema=ws.template.schema_definition, definition=screen_engine.ScreenDefinition.model_validate(s.definition),
        hidden=ws.hidden, offset=body.offset, limit=body.limit, extra_filters=body.filters, extra_sort=body.sort, search=body.search)
    return {"screen": screen_service.summary(s), **result}


@router.post("/screens", status_code=201)
async def agent_save_screen(body: ScreenBody, ws: Workspace = Depends(get_workspace)):
    """
    Saves a screen. For a person who asked for it in their chat it is theirs at once (it appears in the sidebar). One you thought of
    yourself is only a suggestion: it waits, with your reason, until a person keeps it.
    """
    actor = await on_a_persons_request(ws)
    if actor is None:
        pending = (await ws.db.execute(select(func.count()).select_from(WorkspaceScreen).where(
            WorkspaceScreen.template_id == ws.template_id, WorkspaceScreen.status == "suggested"))).scalar_one()
        if pending >= MAX_PENDING_SUGGESTIONS:
            raise HTTPException(status_code=409, detail=f"{pending} suggestions are already waiting for a person. Wait for them to be kept or dismissed.")
    s = await screen_service.create(
        ws.db, tenant_id=ws.tenant_id, template_id=ws.template_id, schema=ws.template.schema_definition, name=body.name, description=body.description,
        definition=body.definition, actor="agent", actor_kind="agent", status="active" if actor else "suggested", rationale=body.rationale)
    if actor is None:
        await workspace_events.emit(
            ws.db, tenant_id=ws.tenant_id, template_id=ws.template_id, type="agent.screen", title="Syruss suggests a screen", body=s.name,
            path="screens/suggested", data={"screen": s.slug})
    await ws.db.commit()
    await screen_service.announce(s, "suggested" if actor is None else "created")
    await workspace_events.dispatch(ws.db)
    return screen_service.detail(s)


@router.patch("/screens/{slug}")
async def agent_change_screen(slug: str, body: ScreenChange, ws: Workspace = Depends(get_workspace)):
    if await on_a_persons_request(ws) is None:
        raise HTTPException(status_code=403, detail="You change a screen only when a person asks you to, in their chat. Suggest a new one instead.")
    s = await screen_service.get(ws.db, ws.template_id, slug)
    if body.name is not None:
        s.name = body.name.strip()[:120] or s.name
    if body.description is not None:
        s.description = body.description.strip()
    if body.definition is not None:
        s.definition = screen_service.check(body.definition, ws.template.schema_definition).model_dump(exclude_none=True)
    audit(ws, "SCREEN_UPDATED", "Screen", s.id, {"name": s.name})
    await ws.db.commit()
    await screen_service.announce(s, "updated")
    return screen_service.detail(s)


@router.delete("/screens/{slug}", status_code=204)
async def agent_remove_screen(slug: str, ws: Workspace = Depends(get_workspace)):
    if await on_a_persons_request(ws) is None:
        raise HTTPException(status_code=403, detail="You remove a screen only when a person asks you to, in their chat.")
    s = await screen_service.get(ws.db, ws.template_id, slug)
    audit(ws, "SCREEN_DELETED", "Screen", s.id, {"name": s.name})
    await screen_service.announce(s, "deleted")
    await ws.db.delete(s)
    await ws.db.commit()
    return Response(status_code=204)
