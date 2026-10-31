"""The workspace's known people and things, their other spellings, and the graph of what appears together."""
from typing import List
from pydantic import BaseModel, Field
from agentic_framework.agent.tools.base import RunContext, ToolOutcome, dump, json_arg, tool


class SearchEntitiesArgs(BaseModel):
    query: str = Field(..., description="Name, identifier, or the text as you read it from the page")
    entity_type: str = Field("", description="Optional entity type filter")
    limit: int = 5


@tool("search_entities", "Ranked candidate entities for what you read (fuzzy on names, identifiers and aliases). "
      "You decide which candidate, if any, the page refers to.", SearchEntitiesArgs)
async def search_entities(ctx: RunContext, a: SearchEntitiesArgs) -> ToolOutcome:
    return ToolOutcome(dump(await ctx.api.get("/entities/search", q=a.query, entity_type=a.entity_type or None, limit=a.limit)))


class CreateEntityArgs(BaseModel):
    entity_type: str = Field(..., description="An object type the active template gives an identity")
    name: str
    identifier_key: str = Field("", description="Defaults to that object's identity id_field")
    identifier_value: str = ""
    attributes_json: str = Field("{}", description="JSON object of attributes")
    source_document_id: str = Field("", description="A COMMITTED document that names it")
    source_question_id: str = Field("", description="Or: an answered question in which a person confirmed it")
    reason: str = Field(..., description="Why you are sure this is a real, new entity")


@tool("create_entity", "Register a new business entity from a committed record or a person's answer (audited).", CreateEntityArgs)
async def create_entity(ctx: RunContext, a: CreateEntityArgs) -> ToolOutcome:
    body = {
        "entity_type": a.entity_type, "name": a.name, "identifier_key": a.identifier_key or None,
        "identifier_value": a.identifier_value or None, "attributes": json_arg(a.attributes_json, "attributes_json", {}),
        "source_document_id": a.source_document_id or None, "source_question_id": a.source_question_id or None,
        "reason": a.reason,
    }
    result = await ctx.api.post("/entities", body)
    registered = (a.entity_type, (a.identifier_value or "").replace(" ", "").upper())
    ctx.unregistered = [t for t in ctx.unregistered if (t["type"], t["id"]) != registered]
    return ToolOutcome(dump(result))


class UpdateEntityArgs(BaseModel):
    entity_id: str
    attributes_json: str = Field(..., description="JSON object of attributes to set")
    reason: str


@tool("update_entity", "Set attributes on an entity (merged into existing ones; audited).", UpdateEntityArgs)
async def update_entity(ctx: RunContext, a: UpdateEntityArgs) -> ToolOutcome:
    body = {"attributes": json_arg(a.attributes_json, "attributes_json", {}), "reason": a.reason}
    return ToolOutcome(dump(await ctx.api.patch(f"/entities/{a.entity_id}", body)))


class AliasArgs(BaseModel):
    entity_id: str
    alias: str = Field(..., description="Another way this entity appears on documents")
    source: str = Field(..., description="How you know, e.g. 'review <id>' or 'answer to question <id>'")


@tool("add_entity_alias", "Remember another way an entity is written (nickname, abbreviation, a confirmed handwriting variant).", AliasArgs)
async def add_entity_alias(ctx: RunContext, a: AliasArgs) -> ToolOutcome:
    return ToolOutcome(dump(await ctx.api.post(f"/entities/{a.entity_id}/aliases", {"alias": a.alias, "source": a.source})))


class MergeArgs(BaseModel):
    keep_entity_id: str
    merge_entity_ids: List[str]
    observation: str = Field(..., description="Why these are the same entity")


@tool("propose_entity_merge", "Propose that duplicate entities are the same (needs human approval).", MergeArgs)
async def propose_entity_merge(ctx: RunContext, a: MergeArgs) -> ToolOutcome:
    body = {"proposal_type": "ENTITY_MERGE", "observation": a.observation,
            "proposed_data": {"keep_entity_id": a.keep_entity_id, "merge_entity_ids": a.merge_entity_ids}}
    return ToolOutcome(dump(await ctx.api.post("/proposals", body)))


class RelatedArgs(BaseModel):
    object_type: str = Field(..., description="An object type with an identity, e.g. the type of a person or a place")
    id: str = Field(..., description="Its ID as written")


@tool("related", "The workspace graph around one thing, from committed records: what it appears with, most often first "
      "(where someone usually works, who is usually on a site), and others of its kind that share those contexts. "
      "Use it to judge an unfamiliar or doubtful identity.", RelatedArgs)
async def related(ctx: RunContext, a: RelatedArgs) -> ToolOutcome:
    return ToolOutcome(dump(await ctx.api.get("/graph/related", object=a.object_type, id=a.id)))
