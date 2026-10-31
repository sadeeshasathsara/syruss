import json
from langchain_core.messages import AIMessage, HumanMessage
from agentic_framework.agent.loop import WorkspaceAgent, _prune_images
from agentic_framework.agent.tools import RunContext, execute, safe_calculate
from agentic_framework.telemetry import Telemetry
from agentic_framework.worker import AgentWorker
from agentic_framework.tests.conftest import ScriptedModel, call, make_event

RECORD = {"Guard": [{"employee_id": "75599", "name": "Perera"}]}


def telemetry():
    return Telemetry(None, tenant_id="t1", template_id="tpl1", channel_id="doc1", run_id="r1")


async def test_agent_decides_steps_and_sees_requested_images(core, memory):
    model = ScriptedModel(script=[
        AIMessage(content="Let me look at the page.", tool_calls=[call("view_document", {"document_id": "doc1"})]),
        AIMessage(content="Zooming into the smudged name.", tool_calls=[call("view_document", {"document_id": "doc1", "region": [100, 0, 300, 500]})]),
        AIMessage(content="Clear now.", tool_calls=[call("submit_record", {
            "document_id": "doc1", "data_json": json.dumps(RECORD), "decision": "COMMIT", "confidence": 0.95,
            "unrecorded_content": "Signed OIC block: service no 58155, Nimal Samarasekara"})]),
        AIMessage(content="Committed doc1."),
    ])
    ctx = RunContext(event=make_event("document.submitted", {"document_id": "doc1"}), api=core.api(), memory=memory)
    result = await WorkspaceAgent(model).run(ctx, telemetry())

    assert result.status == "COMPLETED" and result.final_thought == "Committed doc1."
    assert [s["tool_calls"][0]["tool_name"] for s in result.steps[:3]] == ["view_document", "view_document", "submit_record"]
    assert ("GET", "/documents/doc1/image") in [(m, p) for m, p, _ in core.requests]
    # The image the agent asked for is in the next prompt it sees.
    second_prompt = model.seen[1]
    assert isinstance(second_prompt[-1], HumanMessage)
    assert any(part.get("type") == "image_url" for part in second_prompt[-1].content)
    assert core.doc_status["doc1"] == "COMPLETED"
    # What the template could not hold is remembered (in the workspace's memory) as an observation.
    [obs] = core.memories
    assert obs["kind"] == "observation" and "Samarasekara" in obs["content"] and obs["document_ids"] == ["doc1"]


async def test_stale_document_events_are_skipped(core, memory):
    core.doc_status["doc5"] = "COMPLETED"
    worker = AgentWorker(WorkspaceAgent(ScriptedModel(script=[])), memory, producer=None)
    import agentic_framework.worker as worker_module
    api = core.api()
    original = worker_module.CoreAPI
    worker_module.CoreAPI = lambda token: api
    try:
        assert await worker.handle(make_event("document.submitted", {"document_id": "doc5"}, event_id="e-stale")) == "SKIPPED"
    finally:
        worker_module.CoreAPI = original
    assert core.doc_status["doc5"] == "COMPLETED"


async def test_rate_limits_wait_instead_of_restarting(core, memory, monkeypatch):
    import agentic_framework.agent.loop as loop_module

    class Flaky(ScriptedModel):
        failures: int = 1

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            if self.failures:
                self.failures -= 1
                raise RuntimeError("429 RESOURCE_EXHAUSTED. Please retry in 0.01s.")
            return super()._generate(messages, stop, run_manager, **kwargs)

    sleeps = []
    async def fake_sleep(seconds):
        sleeps.append(seconds)
    monkeypatch.setattr(loop_module.asyncio, "sleep", fake_sleep)
    ctx = RunContext(event=make_event("schedule.reflect", {}), api=core.api(), memory=memory)
    result = await WorkspaceAgent(Flaky(script=[AIMessage(content="All quiet.")])).run(ctx, telemetry())
    assert result.final_thought == "All quiet." and sleeps and sleeps[0] < 2


async def test_refusals_come_back_as_feedback_not_crashes(core, memory):
    core.add("POST", r"/documents/(?P<id>[^/]+)/record", lambda r, m: (422, {"detail": {"errors": [{"code": "unknown_field"}]}}))
    ctx = RunContext(event=make_event("document.submitted", {"document_id": "doc1"}), api=core.api(), memory=memory)
    outcome = await execute(ctx, "submit_record", {"document_id": "doc1", "data_json": "{}", "decision": "COMMIT", "confidence": 1, "unrecorded_content": "none"})
    assert outcome.text.startswith("REFUSED") and "unknown_field" in outcome.text
    assert (await execute(ctx, "submit_record", {"document_id": "doc1"})).text.startswith("ERROR: invalid arguments")
    assert (await execute(ctx, "submit_record", {"document_id": "doc1", "data_json": "{oops", "decision": "COMMIT", "confidence": 1, "unrecorded_content": "none"})).text.startswith("ERROR")
    assert (await execute(ctx, "no_such_tool", {})).text.startswith("ERROR: unknown tool")


async def test_parking_work_on_a_question_and_resuming(core, memory):
    ctx = RunContext(event=make_event("document.submitted", {"document_id": "doc1"}), api=core.api(), memory=memory)
    await execute(ctx, "flag", {"answer_type": "text", "question": "What does OT mean?", "context": "seen on 8 rosters",
                                     "waiting_document_ids": ["doc1"], "resume_note": "add overtime and commit"})
    assert core.doc_status["doc1"] == "AWAITING_INPUT"
    [item] = await memory.work_items_for_question("t1", "tpl1", "q1")
    assert item["data"]["document_ids"] == ["doc1"]

    await execute(ctx, "submit_record", {"document_id": "doc1", "data_json": json.dumps(RECORD), "decision": "COMMIT", "confidence": 0.9, "unrecorded_content": "none"})
    assert await memory.work_items_for_question("t1", "tpl1", "q1") == []


async def test_memory_tools(core, memory):
    ctx = RunContext(event=make_event("schedule.reflect", {}), api=core.api(), memory=memory)
    await execute(ctx, "remember", {"kind": "lesson", "content": "OT means overtime billed at 1.5x"})
    found = json.loads((await execute(ctx, "recall", {"query": "overtime billing"})).text)
    assert found[0]["content"].startswith("OT means overtime") and found[0]["scope"] == "workspace"
    # Work runs only search the workspace's memory; personal memory is for chats.
    [(_, _, search)] = [r for r in core.requests if r[1] == "/memories/search"]
    assert search["scopes"] == ["workspace"]


async def test_worker_never_leaves_a_document_hanging(core, memory):
    model = ScriptedModel(script=[AIMessage(content="I looked around but did nothing.")])
    worker = AgentWorker(WorkspaceAgent(model), memory, producer=None)
    worker_api = core.api()
    import agentic_framework.worker as worker_module
    original = worker_module.CoreAPI
    worker_module.CoreAPI = lambda token: worker_api
    try:
        status = await worker.handle(make_event("document.submitted", {"document_id": "doc9"}, event_id="e-hang"))
        assert status == "COMPLETED"
        assert core.doc_status["doc9"] == "FAILED"
        assert ("POST", "/traces") in [(m, p) for m, p, _ in core.requests]
        # Re-delivery of the same event is skipped.
        assert await worker.handle(make_event("document.submitted", {"document_id": "doc9"}, event_id="e-hang")) == "DUPLICATE"
    finally:
        worker_module.CoreAPI = original


async def test_worker_always_answers_a_chat_message(core, memory):
    # The model answers in plain text and ignores the reminder to use reply_to_user:
    # the runtime still delivers its answer.
    model = ScriptedModel(script=[AIMessage(content="Perera worked 12 hours on 22 July."), AIMessage(content=""), AIMessage(content="")])
    worker = AgentWorker(WorkspaceAgent(model), memory, producer=None)
    import agentic_framework.worker as worker_module
    api = core.api()
    original = worker_module.CoreAPI
    worker_module.CoreAPI = lambda token: api
    try:
        await worker.handle(make_event("human.message", {"conversation_id": "c1", "message_id": "m1", "text": "hours?"}, event_id="e-chat"))
    finally:
        worker_module.CoreAPI = original
    [(_, _, body)] = [r for r in core.requests if r[0] == "POST" and r[1] == "/conversations/c1/messages"]
    assert body["reply_to"] == "m1" and "12 hours" in body["content"]


def test_old_images_are_pruned_from_the_prompt():
    msgs = [HumanMessage(content=[{"type": "image_url", "image_url": {"url": f"data:,{i}"}}]) for i in range(6)]
    _prune_images(msgs, keep=2)
    kept = [m for m in msgs if m.content[0].get("type") == "image_url"]
    assert len(kept) == 2 and kept[-1] is msgs[-1]


def test_calculator():
    assert safe_calculate("(7*60 + 24*60 - 19*60) / 60") == 12
    assert safe_calculate("sum([8, 12, 12.5])") == 32.5


async def test_agent_is_reminded_of_unfinished_outcomes(core, memory):
    model = ScriptedModel(script=[
        AIMessage(content=""),  # stops early without an outcome
        AIMessage(content="", tool_calls=[call("submit_record", {
            "document_id": "doc7", "data_json": json.dumps(RECORD), "decision": "REVIEW", "confidence": 0.6,
            "review_reason": "Name is smudged", "unrecorded_content": "none"})]),
        AIMessage(content="Sent doc7 to review."),
    ])
    ctx = RunContext(event=make_event("document.submitted", {"document_id": "doc7"}), api=core.api(), memory=memory)
    result = await WorkspaceAgent(model).run(ctx, telemetry())
    assert core.doc_status["doc7"] == "NEEDS_REVIEW" and result.final_thought == "Sent doc7 to review."
    reminder = model.seen[1][-1]
    assert isinstance(reminder, HumanMessage) and "doc7 has no outcome" in reminder.content


async def test_hung_model_calls_time_out_and_retry(core, memory, monkeypatch):
    import asyncio as aio
    from agentic_framework.config import settings

    class Hangs(ScriptedModel):
        hangs: int = 1

        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
            if self.hangs:
                self.hangs -= 1
                await aio.sleep(5)
            return self._generate(messages, stop, run_manager, **kwargs)

    monkeypatch.setattr(settings, "MODEL_CALL_TIMEOUT_SECONDS", 0.2)
    ctx = RunContext(event=make_event("schedule.reflect", {}), api=core.api(), memory=memory)
    result = await WorkspaceAgent(Hangs(script=[AIMessage(content="Nothing to do.")])).run(ctx, telemetry())
    assert result.final_thought == "Nothing to do."


async def test_chat_mode_has_only_the_chat_tools(core, memory):
    chat = WorkspaceAgent(ScriptedModel(script=[]), mode="chat")
    assert "reply_to_user" in chat.tools and "process_documents" in chat.tools
    assert "submit_record" not in chat.tools and "propose_template_change" not in chat.tools
    ctx = RunContext(event=make_event("human.message", {"conversation_id": "c1", "message_id": "m1", "text": "hi"}),
                     api=core.api(), memory=memory)
    outcome = await execute(ctx, "submit_record", {}, chat.tools)
    assert outcome.text.startswith("ERROR: unknown tool 'submit_record'")
