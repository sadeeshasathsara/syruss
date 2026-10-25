"""Proposing template changes (a person approves them)."""
from typing import List
from pydantic import BaseModel, Field
from agentic_framework.agent.tools.base import RunContext, ToolOutcome, dump, json_arg, tool


class ProposeTemplateArgs(BaseModel):
    observation: str = Field(..., description="What you noticed and why the change matters, in plain language")
    template_schema_json: str = Field(..., description="The COMPLETE proposed template schema as JSON")
    change_summary: str = Field(..., description="One-line summary of what changes")
    template_name: str = Field("", description="Optional new name for the template")
    evidence_document_ids: List[str] = Field(default_factory=list)
    evidence_json: str = Field("{}", description="What you checked, e.g. validation results on past documents")


@tool("propose_template_change", "Propose a new template version (needs human approval). The complete schema, "
      "not a diff.", ProposeTemplateArgs)
async def propose_template_change(ctx: RunContext, a: ProposeTemplateArgs) -> ToolOutcome:
    body = {
        "proposal_type": "TEMPLATE_CHANGE", "observation": a.observation,
        "proposed_data": {"schema_definition": json_arg(a.template_schema_json, "template_schema_json", {}),
                          "change_summary": a.change_summary, "name": a.template_name or None},
        "evidence_document_ids": a.evidence_document_ids, "evidence": json_arg(a.evidence_json, "evidence_json", {}),
        "channel_id": ctx.event.payload.get("channel_id"),
    }
    return ToolOutcome(dump(await ctx.api.post("/proposals", body)))
