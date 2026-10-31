"""Proposing template changes (a person approves them)."""
from typing import List
from pydantic import BaseModel, Field
from agentic_framework.agent.tools.base import RunContext, ToolOutcome, dump, json_arg, tool

# A compact inline example embedded in the field description so the model always sees
# the correct shape without needing a separate read_guide call.
_SCHEMA_EXAMPLE = (
    '{"document_type":"invoice","objects":['
    '{"type":"Supplier","identity":{"id_field":"tax_id","label_field":"name"},"multiple":false,'
    '"fields":[{"name":"tax_id","type":"string","required":true},{"name":"name","type":"string","required":true}]},'
    '{"type":"Invoice","multiple":false,'
    '"fields":[{"name":"number","type":"string","required":true},{"name":"date","type":"date"},'
    '{"name":"supplier","type":"string","resolves_to":"Supplier"},{"name":"total","type":"number"}]},'
    '{"type":"Line","multiple":true,'
    '"fields":[{"name":"description","type":"string"},{"name":"quantity","type":"number"},'
    '{"name":"amount","type":"number"}]}],'
    '"validation_rules":[{"rule_type":"sum_match","object":"Line","field":"amount",'
    '"total_object":"Invoice","total_field":"total","description":"Lines must sum to invoice total"}]}'
)


class ProposeTemplateArgs(BaseModel):
    observation: str = Field(..., description="What you noticed and why the change matters, in plain language")
    template_schema_json: str = Field(
        ...,
        description=(
            "The COMPLETE proposed template schema as a JSON string. "
            "Rules: (1) Recurring real-world things (people, places, organisations, products) get an "
            "\"identity\":{\"id_field\":\"...\",\"label_field\":\"...\"} block — these become tracked entities. "
            "(2) Facts/events/transactions have no identity; use \"resolves_to\":\"<ObjectType>\" on fields "
            "that hold the identifying value of a related entity. "
            "(3) Every resolves_to target must have an identity block. "
            f"Minimal example: {_SCHEMA_EXAMPLE}"
        ),
    )
    change_summary: str = Field(..., description="One-line summary of what changes")
    template_name: str = Field("", description="Optional new name for the template")
    evidence_document_ids: List[str] = Field(default_factory=list)
    evidence_json: str = Field("{}", description="What you checked, e.g. validation results on past documents")


@tool(
    "propose_template_change",
    "Propose a new template version (needs human approval). The complete schema, not a diff. "
    "Always separate recurring things (entities with identity) from facts (no identity, "
    "use resolves_to to link back to entities).",
    ProposeTemplateArgs,
)
async def propose_template_change(ctx: RunContext, a: ProposeTemplateArgs) -> ToolOutcome:
    body = {
        "proposal_type": "TEMPLATE_CHANGE", "observation": a.observation,
        "proposed_data": {"schema_definition": json_arg(a.template_schema_json, "template_schema_json", {}),
                          "change_summary": a.change_summary, "name": a.template_name or None},
        "evidence_document_ids": a.evidence_document_ids, "evidence": json_arg(a.evidence_json, "evidence_json", {}),
        "channel_id": ctx.event.payload.get("channel_id"),
    }
    return ToolOutcome(dump(await ctx.api.post("/proposals", body)))
