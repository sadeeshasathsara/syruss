"""
Analytics screens (the Analytics tab): saved tables over the workspace's saved records. You can look at them, try new ones, save them and answer from them.
The platform computes every figure (you never add up rows yourself) and checks a screen before it is stored.
"""
import json
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from agentic_framework.agent.tools.base import RunContext, ToolOutcome, dump, json_arg, tool


def _canonical(definition: Any) -> str:
    return json.dumps(definition, sort_keys=True, ensure_ascii=False)


UNSEEN = ("ERROR: preview this exact definition with preview_screen first, look at its rows, totals and errors (check one figure "
          "by hand: filter to one person or group and compare), and only then save it. Nothing was saved.")
NOT_LISTED = "ERROR: call list_screens first: you must know what the workspace already has before you add or change a screen. Nothing was saved."
ASK_FIRST = ("ERROR: you don't yet know what this person wants to see or decide. Ask them first with reply_to_user: at most three short "
             "questions (what decision it helps with, how they want it cut up, which period), with concrete options from the records. "
             "Build after they answer. Nothing was saved.")


def _fields(definition: Any) -> set:
    return {c.get("field") for c in (definition or {}).get("columns", []) if isinstance(c, dict) and c.get("field")}


def similar_screen(definition: Any, name: str, known: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """An existing screen this one would mostly repeat: the same name, or the same rows with about half or more of the same fields."""
    mine = _fields(definition)
    obj = ((definition or {}).get("source") or {}).get("object")
    for s in known:
        if (s.get("name") or "").strip().lower() == name.strip().lower():
            return s
        theirs = _fields(s.get("definition"))
        if obj and obj == ((s.get("definition") or {}).get("source") or {}).get("object") and mine and theirs:
            if len(mine & theirs) / len(mine | theirs) >= 0.5:
                return s
    return None


class NoArgs(BaseModel):
    pass


@tool("list_screens", "The workspace's analytics screens with their definitions (people's, yours, and suggestions still waiting). "
      "Call it before you save or change one, so you build on what exists instead of repeating it.", NoArgs)
async def list_screens(ctx: RunContext, a: NoArgs) -> ToolOutcome:
    found = await ctx.api.get("/screens")
    ctx.screens_seen = found if isinstance(found, list) else []
    return ToolOutcome(dump(found))


@tool("screen_catalogue", "What a screen can be made of in this workspace: the template's objects and fields (with the name a "
      "formula uses), the formula functions, totals (aggregates) and filter operators.", NoArgs)
async def screen_catalogue(ctx: RunContext, a: NoArgs) -> ToolOutcome:
    return ToolOutcome(dump(await ctx.api.get("/screens/catalogue")))


DEFINITION_HELP = (
    'JSON: {"source": {"object": "Guard"}, "columns": [{"id": "name", "label": "Guard", "field": "name"}, '
    '{"id": "hours", "label": "Hours", "field": "hours", "format": {"type": "number", "decimals": 1}, "aggregate": "sum"}, '
    '{"id": "overtime", "label": "Overtime", "kind": "formula", "formula": "max(hours - 8, 0)", "aggregate": "sum"}], '
    '"filters": [{"field": "site", "op": "eq", "value": "S12"}], "sort": [{"column": "hours", "dir": "desc"}], '
    '"group_by": ["name"], "totals": true}. Column ids are lowercase letters, digits and _. Formulas use column ids and field '
    "variables from screen_catalogue. Read the screens guide for the rest."
)


class PreviewArgs(BaseModel):
    definition_json: str = Field(..., description="The screen to try. " + DEFINITION_HELP)
    limit: int = Field(25, ge=1, le=200)
    filters_json: str = Field("[]", description='Extra filters for this look only: [{"field", "op", "value"}]')


@tool("preview_screen", "Try a screen without saving it: you get its columns, rows, totals and which cells could not be worked out. "
      "This is how you analyse: build the table that would answer the question, look at it, adjust. Problems come back as sentences "
      "to fix.", PreviewArgs)
async def preview_screen(ctx: RunContext, a: PreviewArgs) -> ToolOutcome:
    definition = json_arg(a.definition_json, "definition_json", {})
    body = {"definition": definition, "limit": a.limit, "filters": json_arg(a.filters_json, "filters_json", [])}
    result = await ctx.api.post("/screens/preview", body)
    ctx.previewed_screens.add(_canonical(definition))
    return ToolOutcome(dump(result))


class RunArgs(BaseModel):
    slug: str = Field(..., description="The screen's slug, from list_screens")
    filters_json: str = Field("[]", description='Extra filters for this look only: [{"field": <column id>, "op", "value"}]')
    sort_json: str = Field("", description='Another order for this look: [{"column": <column id>, "dir": "asc"|"desc"}]')
    search: str = Field("", description="Words that must all appear somewhere in a row, any column (like the search box)")
    limit: int = Field(25, ge=1, le=200)
    offset: int = Field(0, ge=0)


@tool("run_screen", "Answer a question from a saved screen: its rows and totals now, with extra filters, a search or another order if you "
      "need them (the saved screen is not changed). Prefer this to adding rows up yourself.", RunArgs)
async def run_screen(ctx: RunContext, a: RunArgs) -> ToolOutcome:
    body = {"filters": json_arg(a.filters_json, "filters_json", []), "sort": json_arg(a.sort_json, "sort_json", None), "search": a.search,
            "limit": a.limit, "offset": a.offset}
    return ToolOutcome(dump(await ctx.api.post(f"/screens/{a.slug}/run", body)))


class SaveArgs(BaseModel):
    name: str = Field(..., description="A plain name a colleague would recognise, e.g. 'Overtime by guard'")
    description: str = Field("", description="One sentence: what the screen is for")
    definition_json: str = Field(..., description=DEFINITION_HELP)
    rationale: str = Field("", description="Why this screen is worth having, for the people who decide whether to keep it")
    goal: str = Field("", description="In a chat: what the person wants to see or decide, in a sentence, from what THEY said")
    goal_is_clear: bool = Field(False, description="In a chat: true only if the person has told you specifically enough what they want "
                                "to see or decide to build it. If their request is open ('an analytics page', 'a full table', 'to make "
                                "decisions'), it is false: ask them first")
    person_chose_new: bool = Field(False, description="true only if the person told you to make a new screen even though a similar one "
                                   "exists (you asked: change that one or make a new one?)")


@tool("save_screen", "Save an analytics screen. In a chat it is theirs at once and appears under Analytics; one you thought of yourself "
      "is only a suggestion until a person keeps it (so give a rationale). Before saving: know what they want (ask if not), look at "
      "list_screens (a similar screen means asking: change it or make a new one?), and preview this exact definition.", SaveArgs)
async def save_screen(ctx: RunContext, a: SaveArgs) -> ToolOutcome:
    definition = json_arg(a.definition_json, "definition_json", {})
    if ctx.screens_seen is None:
        return ToolOutcome(NOT_LISTED)
    chatting = ctx.event.type == "human.message"
    if chatting and not (a.goal_is_clear and a.goal.strip()):
        return ToolOutcome(ASK_FIRST)
    twin = similar_screen(definition, a.name, ctx.screens_seen)
    if twin and not (chatting and a.person_chose_new):
        who = "Ask the person with reply_to_user: change" if chatting else "Do not repeat it; suggest something different instead of"
        return ToolOutcome(f"ERROR: '{twin.get('name')}' ({twin.get('slug')}) already covers much of this. {who} that one (update_screen), "
                           "or make a new one? When they say a new one, save again with person_chose_new true. Nothing was saved.")
    if _canonical(definition) not in ctx.previewed_screens:
        return ToolOutcome(UNSEEN)
    body = {"name": a.name, "description": a.description, "definition": definition, "rationale": a.rationale or None}
    return ToolOutcome(dump(await ctx.api.post("/screens", body)))


class UpdateArgs(BaseModel):
    slug: str
    name: str = ""
    description: str = ""
    definition_json: str = Field("", description="The whole new definition (not a patch). Start from list_screens' definition")


@tool("update_screen", "Change a saved screen, only when the person asks you to in their chat. Send the complete new definition.", UpdateArgs)
async def update_screen(ctx: RunContext, a: UpdateArgs) -> ToolOutcome:
    if ctx.screens_seen is None:
        return ToolOutcome(NOT_LISTED)
    definition = json_arg(a.definition_json, "definition_json", None)
    if definition is not None and _canonical(definition) not in ctx.previewed_screens:
        return ToolOutcome(UNSEEN)
    body = {"name": a.name or None, "description": a.description or None, "definition": definition}
    return ToolOutcome(dump(await ctx.api.patch(f"/screens/{a.slug}", body)))


class RemoveArgs(BaseModel):
    slug: str


@tool("remove_screen", "Delete a screen, only when the person asks you to in their chat. The records are not touched.", RemoveArgs)
async def remove_screen(ctx: RunContext, a: RemoveArgs) -> ToolOutcome:
    await ctx.api.delete(f"/screens/{a.slug}")
    return ToolOutcome(dump({"removed": a.slug}))
