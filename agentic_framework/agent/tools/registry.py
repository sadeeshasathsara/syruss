"""Which tools each kind of run gets, their schemas for the model, and running a call safely."""
from typing import Any, Dict, List, Optional, Sequence
from agentic_framework.agent.tools.base import TOOLS, RunContext, ToolOutcome, dump
from agentic_framework.core_api import ToolRefused
from agentic_framework.events import AgentEvent

# What a chat may use: looking things up, noting lessons, and starting work for the person.
CHAT_TOOLS = (
    "get_workspace", "list_documents", "get_document", "view_document", "search_past_records", "search_entities",
    "review_history", "recall", "remember", "update_memory", "calculate", "process_documents", "reply_to_user",
    "related", "query_records", "read_guide",
    "list_screens", "run_screen", "screen_catalogue", "preview_screen", "save_screen", "update_screen", "remove_screen",
)

# What the analyst may use (custom screens, "what stands out?"): the chat's lookups, plus trying, saving and changing screens.
ANALYST_TOOLS = (
    "get_workspace", "list_documents", "get_document", "search_past_records", "query_records", "search_entities", "review_history",
    "recall", "remember", "update_memory", "calculate", "reply_to_user", "related", "read_guide",
    "list_screens", "screen_catalogue", "preview_screen", "run_screen", "save_screen", "update_screen", "remove_screen",
)

# What an evaluation may use: everything needed to read a document, nothing that changes the workspace.
EVAL_TOOLS = (
    "get_workspace", "get_document", "view_document", "search_past_records", "search_entities", "review_history",
    "recall", "related", "calculate", "validate_record", "submit_record", "mark_document_failed", "query_records",
    "read_guide",
)

# Every tool's description is resent with every model call, so each kind of work gets the tools it
# uses. Anything else (answers, reviews, decisions, replies in conversations it opened) can lead
# anywhere, so it gets them all.
WORK_CORE = (
    "get_workspace", "get_document", "view_document", "search_past_records", "query_records", "review_history",
    "search_entities", "related", "recall", "remember", "update_memory", "update_note", "calculate", "read_guide",
    "flag", "open_conversation", "propose_system_lesson", "list_screens", "run_screen",
)
TOOLSETS: Dict[str, Sequence[str]] = {
    "document.submitted": WORK_CORE + ("validate_record", "submit_record", "mark_document_failed", "create_entity",
                                       "update_entity", "add_entity_alias", "propose_entity_merge", "propose_template_change"),
    "template.samples_added": WORK_CORE + ("list_documents", "validate_record", "propose_template_change"),
    "knowledge.source_added": WORK_CORE + ("list_documents", "inspect_source", "propose_source_mapping", "propose_template_change"),
    "schedule.insights": ("get_workspace", "list_documents", "get_document", "query_records", "search_past_records", "review_history", "recall",
                          "calculate", "read_guide", "list_screens", "screen_catalogue", "preview_screen", "run_screen", "save_screen"),
    "schedule.reflect": WORK_CORE + ("list_documents", "validate_record", "propose_template_change", "propose_entity_merge",
                                     "update_entity", "add_entity_alias"),
}


def work_tools(event: AgentEvent) -> Sequence[str]:
    """The tools a work run gets, by what woke it."""
    chosen = TOOLSETS.get(event.type)
    if chosen is None:
        return tuple(name for name in TOOLS if name != "process_documents")  # process_documents is for chats
    return chosen


def tool_schemas(names: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
    return [t.schema() for name, t in TOOLS.items() if names is None or name in names]


async def execute(ctx: RunContext, name: str, raw_args: Optional[Dict[str, Any]], allowed: Optional[Sequence[str]] = None) -> ToolOutcome:
    """Runs a tool. Refusals and bad arguments are returned to the agent as text to reason about."""
    t = TOOLS.get(name)
    if t is None or (allowed is not None and name not in allowed):
        return ToolOutcome(f"ERROR: unknown tool '{name}'. Available: {', '.join(allowed or TOOLS)}")
    try:
        args = t.args.model_validate(raw_args or {})
    except Exception as exc:
        return ToolOutcome(f"ERROR: invalid arguments for {name}: {exc}")
    try:
        outcome = await t.handler(ctx, args)
    except ToolRefused as exc:
        return ToolOutcome(f"REFUSED by the platform (HTTP {exc.status_code}): {dump(exc.detail)}")
    except ValueError as exc:
        return ToolOutcome(f"ERROR: {exc}")
    ctx.touched_documents.update(outcome.touched_document_ids)
    return outcome
