"""Data sources the admins add, and mapping them onto the template."""
from pydantic import BaseModel, Field
from agentic_framework.agent.tools.base import RunContext, ToolOutcome, dump, json_arg, tool


class SourceArgs(BaseModel):
    source_id: str


@tool("inspect_source", "A data source the admin added (a file or an API): the record sets in it with their "
      "columns and sample rows, its current mapping, and the template's identified objects it can map onto.", SourceArgs)
async def inspect_source(ctx: RunContext, a: SourceArgs) -> ToolOutcome:
    return ToolOutcome(dump(await ctx.api.get(f"/sources/{a.source_id}")))


class SourceMappingArgs(BaseModel):
    source_id: str
    mapping_json: str = Field(..., description='{"records_path": "<path to the list of records, \"\" for the top level>", '
                              '"objects": [{"entity_type": "<an identified template object>", "id_column": "<column>", '
                              '"name_column": "<column>", "attributes": {"<attribute>": "<column>"}, "alias_columns": ["<column>"]}]}')
    observation: str = Field(..., description="What the data is and why it maps this way, in plain language")
    dry_run: bool = Field(False, description="Only check the mapping and see what the import would do, without proposing it")


@tool("propose_source_mapping", "Map a data source onto the template's identified objects. With dry_run, the "
      "platform checks the mapping and shows what the import would do (new, already known, names that differ, rows "
      "without an id). Without it, the mapping goes to a person for approval; approving imports the records.", SourceMappingArgs)
async def propose_source_mapping(ctx: RunContext, a: SourceMappingArgs) -> ToolOutcome:
    body = {"mapping": json_arg(a.mapping_json, "mapping_json", {}), "observation": a.observation, "dry_run": a.dry_run}
    return ToolOutcome(dump(await ctx.api.post(f"/sources/{a.source_id}/mapping", body)))
