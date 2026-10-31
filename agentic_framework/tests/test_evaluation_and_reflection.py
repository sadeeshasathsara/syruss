import json
from langchain_core.messages import AIMessage
from agentic_framework.agent.briefing import build_briefing
from agentic_framework.agent.loop import WorkspaceAgent
from agentic_framework.agent.tools import EVAL_TOOLS, RunContext
from agentic_framework.worker import AgentWorker
from agentic_framework.tests.conftest import ScriptedModel, call, make_event

RECORD = {"Guard": [{"employee_id": "75599", "name": "Perera"}]}


async def _handle(core, memory, model, event):
    import agentic_framework.worker as worker_module
    worker = AgentWorker(WorkspaceAgent(model), memory, producer=None)
    api = core.api()
    original = worker_module.CoreAPI
    worker_module.CoreAPI = lambda token: api
    try:
        return await worker.handle(event)
    finally:
        worker_module.CoreAPI = original


async def test_evaluations_read_with_tools_that_cannot_change_the_workspace(core, memory):
    model = ScriptedModel(script=[
        AIMessage(content="Reading it.", tool_calls=[call("view_document", {"document_id": "gold1"})]),
        AIMessage(content="Trying to note something.", tool_calls=[call("remember", {"content": "x", "kind": "lesson"})]),
        AIMessage(content="Submitting.", tool_calls=[call("submit_record", {
            "document_id": "gold1", "data_json": json.dumps(RECORD), "decision": "COMMIT", "confidence": 0.95,
            "unrecorded_content": "a margin note"})]),
        AIMessage(content="Done."),
    ])
    event = make_event("eval.item", {"eval_run_id": "run1", "item_id": "i1", "document_id": "gold1", "name": "gold.jpg"}, event_id="e-eval")
    assert await _handle(core, memory, model, event) == "COMPLETED"

    assert "remember" not in EVAL_TOOLS
    calls = [(m, p) for m, p, _ in core.requests]
    assert ("POST", "/documents/gold1/record") in calls
    # Nothing written: no memory upkeep, no observation from unrecorded content, no document status changes.
    assert not core.memories
    assert ("POST", "/memories/import") not in calls and ("POST", "/documents/gold1/status") not in calls
    # It answered, so the run doesn't close the item as unanswered.
    assert ("POST", "/evals/outcome") not in calls
    # The tool the agent wasn't given was refused as unknown, not run.
    assert any("unknown tool 'remember'" in str(m.content) for m in model.seen[2] if hasattr(m, "content"))


async def test_an_evaluation_without_a_record_is_closed_as_unanswered(core, memory):
    model = ScriptedModel(script=[AIMessage(content="I can't tell.")] * 5)
    event = make_event("eval.item", {"eval_run_id": "run1", "item_id": "i2", "document_id": "gold2", "name": "gold2.jpg"}, event_id="e-eval2")
    await _handle(core, memory, model, event)
    outcome = [b for m, p, b in core.requests if (m, p) == ("POST", "/evals/outcome")]
    assert len(outcome) == 1 and "without a record" in outcome[0]["note"]


async def test_reflection_briefing_shows_corrections_quality_and_upkeep(core, memory):
    core.add("GET", r"/reviews/history", lambda r, m: (200, [{
        "review_id": "rv1", "document_id": "doc9", "status": "CORRECTED", "reason": "smudged", "notes": "It's Perera",
        "original_data": {"Guard": [{"name": "Pereira"}]}, "corrected_data": {"Guard": [{"name": "Perera"}]},
        "decided_at": "2026-10-20T10:00:00+00:00",
    }]))
    core.add("GET", r"/quality", lambda r, m: (200, {"agent": {"straight_through_rate": 0.7}, "reviews": {},
                                                     "fields": [{"field": "Guard.name", "corrections": 4}], "calibration": []}))
    payload = {"since": "2026-10-17T00:00:00+00:00", "activity": {"review.completed": 3},
               "consolidated": {"retired": 2, "merged": 1, "merged_items": [{"kept": "a", "merged": "a."}]}}
    ctx = RunContext(event=make_event("schedule.reflect", payload), api=core.api(), memory=memory)
    briefing = await build_briefing(ctx)
    assert "'Pereira' -> 'Perera'" in briefing
    assert "Guard.name" in briefing and "straight_through_rate" in briefing
    assert "retired 2" in briefing and "merged 1" in briefing
