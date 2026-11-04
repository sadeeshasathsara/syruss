"""Rules for the agent: its tools (try first, then only suggest), which runs get them, and the guide a rules chat is given."""
import json
from agentic_framework.agent import guides
from agentic_framework.agent.briefing import _task_guide
from agentic_framework.agent.tools import CHAT_TOOLS, EVAL_TOOLS, RunContext, execute, work_tools
from agentic_framework.tests.conftest import make_event

RULE_TOOLS = {"list_rules", "check_rule", "suggest_rule"}


def test_chats_and_document_work_can_suggest_rules_but_evaluations_cannot():
    assert RULE_TOOLS <= set(CHAT_TOOLS)
    for event in ("document.submitted", "schedule.reflect", "template.samples_added"):
        assert RULE_TOOLS <= set(work_tools(make_event(event, {"document_id": "d1"}))), event
    assert not (RULE_TOOLS & set(EVAL_TOOLS))


def test_a_stated_constraint_in_chat_becomes_a_suggested_rule_not_only_a_note():
    from agentic_framework.agent.charter import ASSISTANT_CHARTER
    rules = ASSISTANT_CHARTER.split("# Rules", 1)[1].split("\n# ", 1)[0]
    assert "even as a remark" in rules and "suggest_rule" in rules and "Never say you made or noted a rule" in rules
    assert "suggest a rule" in ASSISTANT_CHARTER.split("# Doing work", 1)[1]


def test_what_the_agent_reads_names_no_kind_of_document():
    """The platform reads any kind of document: what the agent knows of a workspace comes from its template and records,
    never from examples of one business written into the charters, guides, briefings or tool descriptions."""
    import re
    from agentic_framework.agent import briefing, charter
    from agentic_framework.agent.tools import TOOLS
    texts = {
        "work charter": charter.CHARTER,
        "chat charter": charter.ASSISTANT_CHARTER,
        "analyst charter": charter.ANALYST_CHARTER,
        "briefings": open(briefing.__file__, encoding="utf-8").read(),
        **{f"{name} guide": guides.read(name) for name in guides.GUIDES},
        **{f"{name} tool": tool.description + json.dumps(tool.args.model_json_schema()) for name, tool in TOOLS.items()},
    }
    business = re.compile(r"\b(shifts?|guards?|rosters?|invoices?|suppliers?|employees?|night|overtime|salary|patients?|S12)\b", re.I)
    for where, text in texts.items():
        found = business.search(text)
        assert not found, (where, found and found.group(0))


def test_a_rule_ideas_run_only_looks_and_must_give_its_ideas(memory):
    from agentic_framework.agent.loop import open_obligations
    assert set(work_tools(make_event("rules.ideas", {"version_id": "v1"}))) == {"get_workspace", "query_records", "list_rules", "propose_rule_ideas"}
    ctx = RunContext(event=make_event("rules.ideas", {"version_id": "v1"}), api=None, memory=memory)  # type: ignore[arg-type]
    assert any("propose_rule_ideas" in o for o in open_obligations(ctx))
    ctx.ideas_given = True
    assert open_obligations(ctx) == []
    assert guides.FOR_TASK["rules.ideas"] == "rules"


def test_a_chat_from_the_new_rule_page_gets_the_rules_guide(memory):
    ctx = RunContext(event=make_event("human.message", {"conversation_id": "c1", "text": "a must be at least b", "context": {"mode": "rules"}}),
                     api=None, memory=memory)  # type: ignore[arg-type]
    attached = _task_guide(ctx)
    assert "# Guide for this task (rules)" in attached and "rules" in ctx.guides_read
    plain = RunContext(event=make_event("human.message", {"conversation_id": "c1", "text": "hi"}), api=None, memory=memory)  # type: ignore[arg-type]
    assert _task_guide(plain) == ""
    assert guides.read("rules").startswith("# Rules") and "rules" in guides.GUIDES


async def test_a_rule_is_suggested_only_after_it_was_tried(core, memory):
    sent = []
    core.add("GET", r"/rules", lambda r, m: (200, {"rules": [], "template_checks": []}))
    core.add("POST", r"/rules/check", lambda r, m: (sent.append(("check", json.loads(r.content)))
                                                     or (200, {"ok": "bad" not in json.loads(r.content)["check"], "problems": []})))
    core.add("POST", r"/rules/suggest", lambda r, m: (sent.append(("suggest", json.loads(r.content))) or (201, {"id": "r1", "status": "suggested"})))
    ctx = RunContext(event=make_event("document.submitted", {"document_id": "d1"}), api=core.api(), memory=memory)
    rule = {"scope": "Line", "check": "amount == quantity * price"}
    suggestion = {**rule, "name": "Line amounts", "rationale": "Holds on every record so far."}

    assert '"rules": []' in (await execute(ctx, "list_rules", {})).text
    assert (await execute(ctx, "suggest_rule", suggestion)).text.startswith("ERROR: try this exact rule") and sent == []
    # A rule that doesn't compile is not remembered as tried.
    await execute(ctx, "check_rule", {"scope": "Line", "check": "bad"})
    assert (await execute(ctx, "suggest_rule", {**suggestion, "check": "bad"})).text.startswith("ERROR")
    await execute(ctx, "check_rule", {**rule, "check": "amount  ==  quantity * price"})   # spacing does not matter
    made = await execute(ctx, "suggest_rule", suggestion)
    assert '"suggested"' in made.text
    kind, body = sent[-1]
    assert kind == "suggest" and body["check"] == rule["check"] and body["rationale"].startswith("Holds") and body["severity"] == "error"
