"""Custom screens for the agent: its tools, the analyst that runs warmer, and which runs get which."""
import json
from agentic_framework import llm
from agentic_framework.agent import guides
from agentic_framework.agent.loop import WorkspaceAgent
from agentic_framework.agent.tools import ANALYST_TOOLS, CHAT_TOOLS, RunContext, execute, work_tools
from agentic_framework.config import settings
from agentic_framework.tests.conftest import call, make_event

WRITES = {"save_screen", "update_screen", "remove_screen"}


def test_the_analyst_and_a_chat_can_build_screens_but_document_work_only_looks():
    assert WRITES <= set(ANALYST_TOOLS) and {"preview_screen", "screen_catalogue", "read_guide", "reply_to_user"} <= set(ANALYST_TOOLS)
    # A chat builds for the person it is talking to (the platform checks that person's role on every call).
    assert WRITES <= set(CHAT_TOOLS) and {"list_screens", "run_screen", "preview_screen", "screen_catalogue", "read_guide"} <= set(CHAT_TOOLS)
    document = set(work_tools(make_event("document.submitted", {"document_id": "d1"})))
    assert {"list_screens", "run_screen"} <= document and not (WRITES & document)
    # A look nobody asked for may only suggest: it can try and save, never change or delete.
    insights = set(work_tools(make_event("schedule.insights", {})))
    assert {"preview_screen", "save_screen", "list_screens"} <= insights and not ({"update_screen", "remove_screen"} & insights)
    assert "submit_record" not in insights and "reply_to_user" not in insights


def test_the_analyst_is_told_what_screens_are_and_how_to_build_them(monkeypatch):
    agent = WorkspaceAgent(object(), mode="analyst")  # type: ignore[arg-type]
    assert agent.tools == ANALYST_TOOLS
    assert "ANALYST" in agent.charter and "# The screens guide" in agent.charter and "group_by" in agent.charter
    assert guides.read("screens").startswith("# Analytics screens") and guides.for_task("schedule.insights") == guides.read("screens")


def test_analytics_runs_warmer_than_the_work_that_must_be_exact(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDER", "gemini")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(settings, "LLM_TEMPERATURE", 0.1)
    monkeypatch.setattr(settings, "ANALYTICS_TEMPERATURE", 0.8)
    assert llm.get_chat_model("work").temperature == 0.1
    assert llm.get_chat_model("chat").temperature == 0.1
    assert llm.get_chat_model("analytics").temperature == 0.8
    monkeypatch.setattr(settings, "CHAT_MODEL", "cheap-model")
    monkeypatch.setattr(settings, "ANALYTICS_MODEL", "")
    assert llm.model_name("analyst") == "cheap-model" and llm.model_name("work") == settings.GEMINI_MODEL
    monkeypatch.setattr(settings, "ANALYTICS_MODEL", "analyst-model")
    assert llm.model_name("analytics") == "analyst-model" and llm.model_name("analyst") == "analyst-model"


async def test_the_tools_pass_what_the_agent_wrote_to_the_platform_and_return_what_it_says(core, memory):
    sent = []
    core.add("GET", r"/screens", lambda r, m: (200, [{"slug": "hours", "name": "Hours", "definition": {"columns": []}}]))
    core.add("GET", r"/screens/catalogue", lambda r, m: (200, {"objects": [], "functions": []}))
    core.add("POST", r"/screens/preview", lambda r, m: (sent.append(json.loads(r.content)) or (200, {"rows": [], "totals": {"hours": 24}, "errors": []})))
    core.add("POST", r"/screens/(?P<slug>[^/]+)/run", lambda r, m: (sent.append((m["slug"], json.loads(r.content))) or (200, {"rows": [{"cells": {"name": "Silva"}}]})))
    core.add("POST", r"/screens", lambda r, m: (sent.append(json.loads(r.content)) or (201, {"slug": "hours", "status": "active"})))
    core.add("DELETE", r"/screens/(?P<slug>[^/]+)", lambda r, m: (204, b""))
    core.add("PATCH", r"/screens/(?P<slug>[^/]+)", lambda r, m: (200, {"slug": m["slug"], "name": json.loads(r.content)["name"]}))
    ctx = RunContext(event=make_event("human.message", {"conversation_id": "c1", "text": "x"}), api=core.api(), memory=memory)
    definition = {"source": {"object": "Guard"}, "columns": [{"id": "name", "label": "Guard", "field": "name"}]}

    # Nothing is saved or changed before the agent has seen what exists.
    assert (await execute(ctx, "save_screen", {"name": "Guards", "definition_json": json.dumps(definition), "goal": "x", "goal_is_clear": True})).text.startswith("ERROR: call list_screens first")
    assert (await execute(ctx, "update_screen", {"slug": "hours", "name": "Renamed"})).text.startswith("ERROR: call list_screens first")
    assert "Hours" in (await execute(ctx, "list_screens", {})).text
    tried = await execute(ctx, "preview_screen", {"definition_json": json.dumps(definition), "limit": 5, "filters_json": '[{"field":"name","op":"eq","value":"Silva"}]'})
    assert '"hours": 24' in tried.text and sent[0]["definition"] == definition and sent[0]["limit"] == 5 and sent[0]["filters"][0]["value"] == "Silva"
    ran = await execute(ctx, "run_screen", {"slug": "hours", "sort_json": '[{"column":"name","dir":"desc"}]'})
    assert "Silva" in ran.text and sent[1][0] == "hours" and sent[1][1]["sort"] == [{"column": "name", "dir": "desc"}]
    # Not before it has been looked at: a definition that was never previewed is not saved (spacing does not matter).
    clear = {"goal": "see each guard's name", "goal_is_clear": True}
    unseen = await execute(ctx, "save_screen", {"name": "Guards", "definition_json": json.dumps({**definition, "totals": True}), **clear})
    assert unseen.text.startswith("ERROR: preview this exact definition") and len(sent) == 2
    assert (await execute(ctx, "update_screen", {"slug": "hours", "definition_json": json.dumps({**definition, "totals": True})})).text.startswith("ERROR: preview")
    # In a chat it asks first when it does not yet know what the person wants.
    vague = await execute(ctx, "save_screen", {"name": "Guards", "definition_json": json.dumps(definition), "goal": "", "goal_is_clear": False})
    assert vague.text.startswith("ERROR: you don't yet know what this person wants") and len(sent) == 2
    saved = await execute(ctx, "save_screen", {"name": "Guards", "definition_json": json.dumps(definition, indent=2), "rationale": "worth a look", **clear})
    assert '"slug": "hours"' in saved.text and sent[2]["name"] == "Guards" and sent[2]["rationale"] == "worth a look"
    assert "removed" in (await execute(ctx, "remove_screen", {"slug": "hours"})).text
    # A malformed definition comes back as a sentence to fix, not a crash.
    assert "not valid JSON" in (await execute(ctx, "save_screen", {"name": "Bad", "definition_json": "{oops", **clear})).text
    assert '"slug"' in (await execute(ctx, "update_screen", {"slug": "hours", "name": "Renamed"})).text   # a rename has no definition to preview


async def test_it_asks_whether_to_change_a_similar_screen_or_make_a_new_one(core, memory):
    existing = {"slug": "shifts-by-type", "name": "Shifts by Type", "definition": {"source": {"object": "Guard"}, "columns": [
        {"id": "kind", "label": "Type", "field": "shift_type"}, {"id": "n", "label": "Shifts", "field": "employee_id", "aggregate": "count"}]}}
    sent = []
    core.add("GET", r"/screens", lambda r, m: (200, [existing]))
    core.add("POST", r"/screens/preview", lambda r, m: (200, {"rows": [], "errors": []}))
    core.add("POST", r"/screens", lambda r, m: (sent.append(json.loads(r.content)) or (201, {"slug": "by-rank", "status": "active"})))
    wider = {"source": {"object": "Guard"}, "columns": [{"id": "kind", "label": "Type", "field": "shift_type"},
                                                          {"id": "rank", "label": "Rank", "field": "rank"}, {"id": "n", "label": "Shifts", "field": "employee_id"}]}
    chat = RunContext(event=make_event("human.message", {"conversation_id": "c1", "text": "x"}), api=core.api(), memory=memory)
    await execute(chat, "list_screens", {})
    await execute(chat, "preview_screen", {"definition_json": json.dumps(wider)})
    args = {"name": "Shifts by Rank and Type", "definition_json": json.dumps(wider), "goal": "staffing by rank", "goal_is_clear": True}
    asked = await execute(chat, "save_screen", args)
    assert "already covers much of this" in asked.text and "Shifts by Type" in asked.text and sent == []
    # Once the person said "a new one" it is saved; an unrelated screen needed no question.
    assert '"slug": "by-rank"' in (await execute(chat, "save_screen", {**args, "person_chose_new": True})).text
    other = {"source": {"object": "Guard"}, "columns": [{"id": "who", "label": "Guard", "field": "name"}, {"id": "h", "label": "Hours", "field": "hours"}]}
    await execute(chat, "preview_screen", {"definition_json": json.dumps(other)})
    assert '"slug"' in (await execute(chat, "save_screen", {"name": "Hours", "definition_json": json.dumps(other), "goal": "hours", "goal_is_clear": True})).text
    # On its own it never repeats a screen, and says nothing of "the person".
    alone = RunContext(event=make_event("schedule.insights", {}), api=core.api(), memory=memory)
    await execute(alone, "list_screens", {})
    await execute(alone, "preview_screen", {"definition_json": json.dumps(wider)})
    assert "Do not repeat it" in (await execute(alone, "save_screen", {"name": "Another", "definition_json": json.dumps(wider), "person_chose_new": True})).text


def test_chats_about_analytics_go_to_the_analyst():
    from agentic_framework.worker import wants_analyst

    def chat(text, page="/org/w/roasters/documents", context=None, kind="chat"):
        return make_event("human.message", {"text": text, "page": page, "context": context or {}, "conversation_kind": kind, "conversation_id": "c1"})

    assert wants_analyst(chat("hello", context={"mode": "screens"}))                    # asked from the analytics page's box
    assert wants_analyst(chat("what changed?", page="/org/w/roasters/analytics/hours"))  # written from an Analytics page
    assert wants_analyst(chat("I need a full analytics table"))                            # plainly about analytics
    assert wants_analyst(chat("can you create a screen for overtime"))
    assert not wants_analyst(chat("who worked the day shift at S12?"))
    assert not wants_analyst(chat("screen", kind="agent"))                                # only a person's private chat
    assert not wants_analyst(make_event("document.submitted", {"document_id": "d1", "text": "screen"}))
