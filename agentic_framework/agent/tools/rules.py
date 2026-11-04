"""
Rules: checks in the rule language that run on every record after a document is read.
You can list them, try one on the saved records, and suggest one; a person adds it. The platform compiles every rule
against the template and runs it, so a rule says the same thing whoever wrote it.
"""
from typing import List, Optional
from pydantic import BaseModel, Field
from agentic_framework.agent.tools.base import NoArgs, RunContext, ToolOutcome, dump, tool

UNCHECKED = ("ERROR: try this exact rule with check_rule first and look at what it says about the saved records. "
             "Nothing was suggested.")


def _key(scope: str, check: str, when: str) -> tuple:
    return ((scope or "document").strip(), " ".join((check or "").split()), " ".join((when or "").split()))


@tool("list_rules", "The workspace's rules (active, suggested, disabled, broken) and the checks that come with the template "
      "version. Look before you suggest one, so you don't repeat a rule that exists.", NoArgs)
async def list_rules(ctx: RunContext, _: NoArgs) -> ToolOutcome:
    return ToolOutcome(dump(await ctx.api.get("/rules")))


class RuleArgs(BaseModel):
    scope: str = Field("document", description='"document" for a rule about the whole record, or an object type to check each of its rows')
    check: str = Field(..., description="A yes/no expression in the rule language over this template's objects and fields (see the rules guide)")
    when: str = Field("", description="Optional: check only rows (or records) where this is true")
    severity: str = Field("error", description="error: a failing record can't be committed; warning: it is only reported")


@tool("check_rule", "Compile a rule against the template and try it on the committed records, without saving it. You get the "
      "problem (what, where, how to fix) when it doesn't compile, else how many records pass, fail or don't apply, and the "
      "first documents it fails on. Read the rules guide for the language.", RuleArgs)
async def check_rule(ctx: RunContext, a: RuleArgs) -> ToolOutcome:
    result = await ctx.api.post("/rules/check", a.model_dump())
    if isinstance(result, dict) and result.get("ok"):
        ctx.checked_rules.add(_key(a.scope, a.check, a.when))
    return ToolOutcome(dump(result))


class SuggestArgs(RuleArgs):
    name: str = Field(..., description="A short name people recognise, in their words")
    description: str = Field("", description="What the rule checks, in a sentence")
    message: str = Field("", description="What people are told when it fails: what is wrong, in their words")
    rationale: str = Field(..., description="Why it is worth having: what you saw, on how many documents, and what it did on check_rule")
    source_document_id: Optional[str] = Field(None, description="The document that made you think of it, if any")


@tool("suggest_rule", "Suggest a rule for this workspace. It does nothing until a person adds it (they see it in the chat and "
      "under Template, Rules), so suggest one when a person asks for it, or when you saw a relation that holds on the "
      "documents and would catch misreads. Try it with check_rule first.", SuggestArgs)
async def suggest_rule(ctx: RunContext, a: SuggestArgs) -> ToolOutcome:
    if _key(a.scope, a.check, a.when) not in ctx.checked_rules:
        return ToolOutcome(UNCHECKED)
    result = await ctx.api.post("/rules/suggest", a.model_dump())
    return ToolOutcome(dump(result))


class IdeasArgs(BaseModel):
    ideas: List[str] = Field(..., description="Up to five rule ideas, each one plain-English sentence specific to this template")


@tool("propose_rule_ideas", "Give your ideas for rules worth having in this workspace. They are shown on the new-rule page as "
      "starting points a person can pick; nothing is created.", IdeasArgs)
async def propose_rule_ideas(ctx: RunContext, a: IdeasArgs) -> ToolOutcome:
    result = await ctx.api.post("/rules/ideas", {"ideas": [i for i in a.ideas if i.strip()][:6]})
    ctx.ideas_given = True
    return ToolOutcome(dump(result))
