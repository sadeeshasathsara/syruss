"""
Entities (the workspace's known world) and the graph around them.
"""
import difflib
from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from app.models.document import DocumentStatus
from app.models.knowledge import Entity, EntityAlias
from app.models.question import AgentQuestion, AgentQuestionStatus
from app.services import graph_service, settings_service
from app.api.internal.agent_tools.scope import Workspace, get_workspace
from app.api.internal.agent_tools.common import audit, document_in, entity_dict


router = APIRouter()


def _similarity(query: str, candidate: str) -> float:
    a, b = query.lower().strip(), candidate.lower().strip()
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    tokens_a, tokens_b = set(a.split()), set(b.split())
    overlap = len(tokens_a & tokens_b) / max(len(tokens_a), len(tokens_b))
    contains = 0.9 if (a in b or b in a) and min(len(a), len(b)) >= 3 else 0.0
    return max(ratio, overlap, contains)


@router.get("/entities/search")
async def search_entities(
    q: str = Query(..., min_length=1, description="Name, identifier, or text as read from the page"),
    entity_type: Optional[str] = None,
    limit: int = Query(5, le=25),
    ws: Workspace = Depends(get_workspace),
):
    """
    Ranked candidates, not a single answer: the agent decides which candidate the ink
    refers to, using the scores together with context (what else is on the page, history).
    """
    stmt = select(Entity).where(Entity.template_id == ws.template_id)
    if entity_type:
        stmt = stmt.where(Entity.entity_type == entity_type)
    candidates = []
    for e in (await ws.db.execute(stmt)).scalars().all():
        fields = [("name", e.name), ("identifier", e.identifier_value or "")] + [("alias", a.alias) for a in e.aliases]
        best_field, best = max(((f, _similarity(q, v)) for f, v in fields if v), key=lambda x: x[1], default=("", 0.0))
        if best >= 0.4:
            candidates.append({**entity_dict(e), "score": round(best, 3), "matched_on": best_field})
    candidates.sort(key=lambda c: c["score"], reverse=True)
    return {"query": q, "candidates": candidates[:limit]}


class EntityCreateBody(BaseModel):
    entity_type: str
    name: str
    identifier_key: Optional[str] = None
    identifier_value: Optional[str] = None
    attributes: Dict[str, Any] = Field(default_factory=dict)
    source_document_id: Optional[str] = Field(None, description="A COMPLETED document that names this entity")
    source_question_id: Optional[str] = Field(None, description="An answered question in which a person confirmed it")
    reason: str = Field(..., description="Why the agent believes this is a new, real entity")


@router.post("/entities", status_code=201)
async def create_entity(body: EntityCreateBody, ws: Workspace = Depends(get_workspace)):
    approved = None
    if body.source_question_id:
        approved = await ws.db.get(AgentQuestion, body.source_question_id)
        if not approved or approved.template_id != ws.template_id or approved.status != AgentQuestionStatus.ANSWERED:
            raise HTTPException(status_code=409, detail="source_question_id must be a question a person answered.")
    if approved is None and not (await settings_service.get_settings(ws.db, ws.template_id)).agent.may_register_entities:
        raise HTTPException(status_code=403, detail=(
            "This workspace's settings don't let you register people and things on your own. Flag one question listing "
            "them and asking whether to register them; when a person agrees, register them with source_question_id."))
    # The agent registers only the kinds of things the template identifies.
    identified = {o["type"]: o["identity"] for o in (ws.template.schema_definition or {}).get("objects", [])
                  if isinstance(o, dict) and isinstance(o.get("identity"), dict)}
    if body.entity_type not in identified:
        raise HTTPException(status_code=422, detail=(
            f"'{body.entity_type}' is not something this template identifies. Entity types: {sorted(identified) or 'none'}. "
            "If it is a real, recurring thing, propose a template change that gives it an identity."))
    body.identifier_key = body.identifier_key or identified[body.entity_type].get("id_field")
    if body.identifier_value:
        existing = (await ws.db.execute(select(Entity).where(
            Entity.template_id == ws.template_id, Entity.entity_type == body.entity_type,
            Entity.identifier_value == body.identifier_value,
        ))).scalar_one_or_none()
        if existing:
            raise HTTPException(status_code=409, detail={"message": "Entity already exists", "entity": entity_dict(existing)})
    # Entities come from confirmed facts only, so the agent cannot create its own confirmation.
    if body.source_document_id:
        source = await document_in(ws, body.source_document_id)
        if source.status != DocumentStatus.COMPLETED:
            raise HTTPException(status_code=409, detail="Register entities from committed records: commit the document first.")
    elif approved is None:
        raise HTTPException(status_code=422, detail="Give source_document_id (a committed document) or source_question_id (an answered question).")
    entity = Entity(
        tenant_id=ws.tenant_id, template_id=ws.template_id, entity_type=body.entity_type, name=body.name,
        identifier_key=body.identifier_key, identifier_value=body.identifier_value, attributes=body.attributes,
        confidence=0.8, created_by="agent", source_document_id=body.source_document_id,
    )
    ws.db.add(entity)
    await ws.db.flush()
    audit(ws, "ENTITY_CREATED", "Entity", entity.id, {"name": body.name, "reason": body.reason})
    await ws.db.commit()
    await ws.db.refresh(entity)
    return entity_dict(entity)


class EntityUpdateBody(BaseModel):
    attributes: Dict[str, Any] = Field(..., description="Attributes to set (merged into existing)")
    reason: str


@router.patch("/entities/{entity_id}")
async def update_entity(entity_id: str, body: EntityUpdateBody, ws: Workspace = Depends(get_workspace)):
    entity = await ws.db.get(Entity, entity_id)
    if not entity or entity.template_id != ws.template_id:
        raise HTTPException(status_code=404, detail="Entity not found in this workspace.")
    entity.attributes = {**(entity.attributes or {}), **body.attributes}
    audit(ws, "ENTITY_UPDATED", "Entity", entity.id, {"attributes": body.attributes, "reason": body.reason})
    await ws.db.commit()
    await ws.db.refresh(entity)
    return entity_dict(entity)


class AliasBody(BaseModel):
    alias: str
    source: str = Field(..., description="Where this was learned, e.g. 'review r_123' or 'answer to question q_9'")


@router.post("/entities/{entity_id}/aliases", status_code=201)
async def add_alias(entity_id: str, body: AliasBody, ws: Workspace = Depends(get_workspace)):
    entity = await ws.db.get(Entity, entity_id)
    if not entity or entity.template_id != ws.template_id:
        raise HTTPException(status_code=404, detail="Entity not found in this workspace.")
    if not (await settings_service.get_settings(ws.db, ws.template_id)).agent.may_learn_aliases:
        raise HTTPException(status_code=403, detail="This workspace's settings don't let you add spellings on your own; ask a person.")
    alias = body.alias.strip()
    if alias.lower() not in {a.alias.lower() for a in entity.aliases} and alias.lower() != entity.name.lower():
        ws.db.add(EntityAlias(entity_id=entity.id, template_id=ws.template_id, alias=alias, source=body.source))
        audit(ws, "ENTITY_ALIAS_ADDED", "Entity", entity.id, {"alias": alias, "source": body.source})
        await ws.db.commit()
        await ws.db.refresh(entity)
    return entity_dict(entity)


@router.get("/graph/related")
async def related(
    object_type: str = Query(..., alias="object", description="The object type, as the template names it"),
    identifier: str = Query(..., alias="id", description="Its ID as written"),
    ws: Workspace = Depends(get_workspace),
):
    """The workspace graph around one thing: what it appears with in committed records, most often
    first, and the things of its kind that share those contexts."""
    key = graph_service.thing_key(object_type, identifier)
    if not key:
        raise HTTPException(status_code=400, detail="Give the thing's ID.")
    neighbours = await graph_service.appears_with(ws.db, ws.template_id, key, limit=30)
    alike = await graph_service.peers(ws.db, ws.template_id, key, limit=10)
    return {
        "thing": {"key": key, "type": object_type, "id": key.split(":", 1)[1]},
        "appears_with": [{"type": n.type, "id": n.key.split(":", 1)[1], "name": n.label, "times": n.count} for n in neighbours],
        "shares_contexts_with": [{"id": n.key.split(":", 1)[1], "name": n.label, "contexts_shared": n.count} for n in alike],
        "graph": await graph_service.backend(ws.db, ws.template_id),
        "note": None if neighbours else "Not seen in any committed record yet.",
    }
