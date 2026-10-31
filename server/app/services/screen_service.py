"""
Saving and finding custom screens. Shared by the people's API (api/v1/screens.py) and the agent's (api/internal), so a
screen means the same thing whoever made it: it is checked against the template before it is stored, never after.
"""
import re
from typing import Any, Dict, List, Optional
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.audit import AuditLog
from app.models.screen import WorkspaceScreen
from app.services import screen_engine
from app.services.formula import FUNCTIONS
from app.services.screen_engine import ScreenDefinition, slug as _slug, validate_definition
from app.services.ui_events import publish_ui_event

MAX_SCREENS = 50
RESERVED = {"new", "suggested", "catalogue", "starters", "preview", "edit"}


def screen_slug(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower().replace("'", "").replace("’", "")).strip("-")[:60] or "screen"
    return f"{base}-screen" if base in RESERVED else base


async def unique_slug(db: AsyncSession, template_id: str, name: str) -> str:
    base = screen_slug(name)
    taken = set((await db.execute(select(WorkspaceScreen.slug).where(
        WorkspaceScreen.template_id == template_id, WorkspaceScreen.slug.like(f"{base}%")))).scalars().all())
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def check(definition: Dict[str, Any], schema: Optional[Dict[str, Any]]) -> ScreenDefinition:
    """The definition as a model, or a 422 that says exactly what to fix (one sentence per problem)."""
    try:
        parsed = ScreenDefinition.model_validate(definition)
    except Exception as exc:  # pydantic's message names the field; keep it short
        errors = getattr(exc, "errors", lambda: [])()
        problems = [f"{'.'.join(str(p) for p in e.get('loc', ()))}: {e.get('msg', '')}" for e in errors][:8] or [str(exc)]
        raise HTTPException(status_code=422, detail={"message": "This screen can't be saved yet.", "problems": problems})
    problems = validate_definition(parsed, schema)
    if problems:
        raise HTTPException(status_code=422, detail={"message": "This screen can't be saved yet.", "problems": problems})
    return parsed


def summary(s: WorkspaceScreen) -> Dict[str, Any]:
    d = s.definition or {}
    return {
        "id": s.id, "slug": s.slug, "name": s.name, "description": s.description, "status": s.status, "position": s.position,
        "created_by": s.created_by, "created_by_kind": s.created_by_kind, "rationale": s.rationale,
        "object": (d.get("source") or {}).get("object"), "columns": len(d.get("columns") or []),
        "created_at": s.created_at, "updated_at": s.updated_at,
    }


def detail(s: WorkspaceScreen) -> Dict[str, Any]:
    return {**summary(s), "definition": s.definition}


async def list_screens(db: AsyncSession, template_id: str) -> List[WorkspaceScreen]:
    return list((await db.execute(select(WorkspaceScreen).where(WorkspaceScreen.template_id == template_id)
                                  .order_by(WorkspaceScreen.position, WorkspaceScreen.created_at))).scalars().all())


async def get(db: AsyncSession, template_id: str, slug: str) -> WorkspaceScreen:
    found = (await db.execute(select(WorkspaceScreen).where(
        WorkspaceScreen.template_id == template_id, WorkspaceScreen.slug == slug))).scalar_one_or_none()
    if found is None:
        raise HTTPException(status_code=404, detail="There is no such screen in this workspace.")
    return found


async def create(db: AsyncSession, *, tenant_id: str, template_id: str, schema: Optional[Dict[str, Any]], name: str, description: str,
                 definition: Dict[str, Any], actor: str, actor_kind: str, status: str = "active", rationale: Optional[str] = None) -> WorkspaceScreen:
    name = name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Give the screen a name.")
    count = (await db.execute(select(func.count()).select_from(WorkspaceScreen).where(WorkspaceScreen.template_id == template_id))).scalar_one()
    if count >= MAX_SCREENS:
        raise HTTPException(status_code=409, detail=f"A workspace can have {MAX_SCREENS} screens. Delete one you no longer use.")
    parsed = check(definition, schema)
    last = (await db.execute(select(func.max(WorkspaceScreen.position)).where(WorkspaceScreen.template_id == template_id))).scalar_one()
    s = WorkspaceScreen(
        tenant_id=tenant_id, template_id=template_id, slug=await unique_slug(db, template_id, name), name=name[:120],
        description=(description or "").strip()[:2000], definition=parsed.model_dump(exclude_none=True), position=(last or 0) + 1,
        status=status, created_by=actor, created_by_kind=actor_kind, rationale=(rationale or None),
    )
    db.add(s)
    await db.flush()
    db.add(AuditLog(tenant_id=tenant_id, template_id=template_id, event_type="SCREEN_SUGGESTED" if status == "suggested" else "SCREEN_CREATED",
                    entity_type="Screen", entity_id=s.id, actor=actor, details={"name": s.name, "slug": s.slug, "by": actor_kind}))
    return s


async def announce(s: WorkspaceScreen, what: str) -> None:
    """Tells open workspaces so their sidebar and screens refresh."""
    await publish_ui_event(f"template:{s.template_id}", "screen_changed", {"screen": s.slug, "name": s.name, "what": what, "status": s.status,
                                                                         "by": s.created_by_kind}, tenant_id=s.tenant_id, template_id=s.template_id)


def starters(schema: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One ready-made screen per repeating object of the template: every row, every field, and the document's own details first."""
    from app.services.rule_engine import normalize_schema
    out: List[Dict[str, Any]] = []
    objects = normalize_schema(schema)
    for obj, spec in objects.items():
        if not spec["multiple"]:
            continue
        var = screen_engine.variables_for(schema, obj)
        columns: List[Dict[str, Any]] = []
        used = set()

        def add(label: str, field: str, ftype: str) -> None:
            cid = _slug(label)[:36]
            if cid in FUNCTIONS:
                cid += "_col"
            while cid in used:
                cid += "_"
            used.add(cid)
            fmt = {"type": "number"} if ftype in ("number", "integer") else {"type": "date"} if ftype == "date" else {"type": "time"} if ftype == "time" else {"type": "text"}
            columns.append({"id": cid, "label": label.replace("_", " ").capitalize(), "kind": "field", "field": field, "format": fmt})

        for doc_var, doc_field in list(var.document.items())[:2]:
            add(doc_field.split(".", 1)[1], doc_field, var.types.get(doc_var, "string"))
        for own_var, field in var.own.items():
            add(field, field, var.types.get(own_var, "string"))
        out.append({"name": f"All {obj.lower()} rows" if not obj.endswith("s") else f"All {obj.lower()}", "description": f"Every {obj.lower()} row from every saved document.",
                    "definition": {"source": {"object": obj}, "columns": columns[:screen_engine.MAX_COLUMNS]}})
    return out
