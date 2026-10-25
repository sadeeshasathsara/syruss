"""Saved records: querying them, and validating and submitting the record of a document."""
from typing import List
from pydantic import BaseModel, Field
from agentic_framework.agent.tools.base import RunContext, ToolOutcome, dump, json_arg, tool
from agentic_framework.agent.tools.memory import save_memory


class QueryRecordsArgs(BaseModel):
    object_type: str = Field(..., description="The template object whose rows you want (e.g. the people on a roster)")
    where_json: str = Field("[]", description='JSON list of conditions: {"field", "op", "value"}. field is a field of the '
                            'rows, or "Object.field" of a one-per-document object (e.g. the roster date). op: eq, ne, '
                            'contains, in (value is a list), gte, lte, empty, not_empty. Case-insensitive.')
    distinct: List[str] = Field(default_factory=list, description="Group by these fields: unique combinations with counts "
                                "(e.g. the employee number and name, for a list of people)")
    fields: List[str] = Field(default_factory=list, description="Only these fields in the returned rows (default: all)")
    limit: int = 100
    include_pending_review: bool = False


@tool("query_records", "Answer list, count and filter questions across every saved record in one call: rows of one "
      "template object matching conditions, optionally grouped into unique combinations. Use this, not get_document one "
      "by one and never page images, for questions like 'who worked the day shift' or 'which documents mention S12'. If "
      "nothing matches it shows the values the documents actually use, so you can adjust the filter.", QueryRecordsArgs)
async def query_records(ctx: RunContext, a: QueryRecordsArgs) -> ToolOutcome:
    body = {"object": a.object_type, "where": json_arg(a.where_json, "where_json", []), "distinct": a.distinct,
            "fields": a.fields, "limit": a.limit, "include_pending_review": a.include_pending_review}
    return ToolOutcome(dump(await ctx.api.post("/records/query", body)))


class SearchRecordsArgs(BaseModel):
    text: str = Field(..., description="A value to look for, e.g. a name, an ID, a site number")
    limit: int = 10


@tool("search_past_records", "Find committed records in this workspace containing a value — how a name or ID "
      "appeared before, who usually appears with whom, what is normal.", SearchRecordsArgs)
async def search_past_records(ctx: RunContext, a: SearchRecordsArgs) -> ToolOutcome:
    return ToolOutcome(dump(await ctx.api.get("/records/search", q=a.text, limit=a.limit)))


class LimitArgs(BaseModel):
    limit: int = 10


@tool("review_history", "Recent human review decisions here: what people approved or corrected, and their notes.", LimitArgs)
async def review_history(ctx: RunContext, a: LimitArgs) -> ToolOutcome:
    return ToolOutcome(dump(await ctx.api.get("/reviews/history", limit=a.limit)))


class ValidateArgs(BaseModel):
    data_json: str = Field(..., description='Draft record JSON: {"ObjectType": [{...}]}')
    proposed_schema_json: str = Field("", description="Optional: validate against this proposed schema instead of the active one")
    document_id: str = Field("", description="The document the draft is for, if any")


@tool("validate_record", "Check a draft record against the active template (or a proposed schema) without storing it. "
      "Against the active template it also returns identity signals from the workspace graph.", ValidateArgs)
async def validate_record(ctx: RunContext, a: ValidateArgs) -> ToolOutcome:
    body = {"data": json_arg(a.data_json, "data_json", {}), "schema_definition": json_arg(a.proposed_schema_json, "proposed_schema_json", None),
            "document_id": a.document_id or None}
    return ToolOutcome(dump(await ctx.api.post("/records/validate", body)))


class SubmitArgs(BaseModel):
    document_id: str
    data_json: str = Field(..., description='Record JSON: {"ObjectType": [{...}]}')
    decision: str = Field(..., description="COMMIT or REVIEW")
    confidence: float = Field(..., description="Honest probability (0-1) that the whole record is correct")
    evidence_json: str = Field("[]", description='JSON list: {"object", "index", "field", "value", "raw_text", '
                               '"confidence", "entity_id", "note"}. Required for identity names: raw_text exactly as written')
    notes: str = Field("", description="What you did and why, for the audit trail")
    review_reason: str = Field("", description="Required for REVIEW: what exactly a person must check")
    unrecorded_content: str = Field(..., description="Everything on the page the template could not hold (blocks, "
                                    "annotations, marks, extra people), described with what it says; or 'none'")
    uncertain_fields_json: str = Field("[]", description='For REVIEW: JSON list of {"field", "value", "why"}')
    acknowledged_signals_json: str = Field("[]", description='JSON list of {"id", "reason"}: identity signals you looked '
                                           "into on the page and stand by, with what you saw")


@tool("submit_record", "Store the document's record and its outcome: COMMIT (complete and certain, must pass the "
      "template) or REVIEW (a person checks the uncertain fields).", SubmitArgs)
async def submit_record(ctx: RunContext, a: SubmitArgs) -> ToolOutcome:
    body = {
        "data": json_arg(a.data_json, "data_json", {}), "decision": a.decision.upper(), "confidence": a.confidence,
        "evidence": json_arg(a.evidence_json, "evidence_json", []), "notes": a.notes or None,
        "review_reason": a.review_reason or None,
        "uncertain_fields": json_arg(a.uncertain_fields_json, "uncertain_fields_json", []),
        "acknowledged_signals": json_arg(a.acknowledged_signals_json, "acknowledged_signals_json", []),
    }
    result = await ctx.api.post(f"/documents/{a.document_id}/record", body)
    if result.get("new_identities"):
        ctx.unregistered = list(result["new_identities"])
        ctx.registration = result.get("registration", "")
    if ctx.evaluating:  # scored, not stored: nothing to remember or close
        ctx.resolved_documents.add(a.document_id)
        return ToolOutcome(dump(result), touched_document_ids=[a.document_id])
    if a.unrecorded_content.strip().lower() not in ("", "none", "n/a", "nothing"):
        await save_memory(ctx, "workspace", "observation",
                          f"Not captured by the template on document {a.document_id}: {a.unrecorded_content}",
                          importance=0.4, document_ids=[a.document_id], data={"tags": ["unrecorded"]})
    ctx.resolved_documents.add(a.document_id)
    ctx.parked_documents.discard(a.document_id)
    for item in await ctx.memory.work_items_for_document(ctx.tenant_id, ctx.template_id, a.document_id):
        await ctx.memory.update(ctx.tenant_id, ctx.template_id, item["id"], status="done",
                                data={"outcome": f"record submitted ({a.decision.upper()})"})
    return ToolOutcome(dump(result), touched_document_ids=[a.document_id])
