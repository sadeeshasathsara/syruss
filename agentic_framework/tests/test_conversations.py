import json
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from agentic_framework.agent import checkpoint
from agentic_framework.agent.loop import WorkspaceAgent
from agentic_framework.worker import AgentWorker
from agentic_framework.tests.conftest import ScriptedModel, call, make_event


def with_conversations(core):
    """Fake core routes for conversations the agent opens, with their saved state."""
    state = {}

    def open_conversation(request, m):
        body = json.loads(request.content)
        return 201, {"id": "conv1", "title": body["title"], "status": "open"}

    def put_state(request, m):
        state[m["id"]] = json.loads(request.content)["messages"]
        return 200, {"saved": True}

    core.add("POST", r"/conversations", open_conversation)
    core.add("GET", r"/conversations/(?P<id>[^/]+)/state", lambda r, m: (200, {"id": m["id"], "state": {"messages": state.get(m["id"])}}))
    core.add("PUT", r"/conversations/(?P<id>[^/]+)/state", put_state)
    core.add("POST", r"/conversations/(?P<id>[^/]+)/resolve", lambda r, m: (200, {"id": m["id"], "status": "resolved"}))
    return state


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


def test_checkpoints_drop_images_and_keep_tool_calls_paired():
    messages = [HumanMessage(content="briefing")]
    for i in range(40):
        messages += [AIMessage(content="", tool_calls=[call("view_document", {"document_id": f"d{i}"})]),
                     ToolMessage(content="x" * 5000, tool_call_id=f"call_view_document_{i}", name="view_document"),
                     HumanMessage(content=[{"type": "text", "text": "Requested image(s):"},
                                           {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}}])]
    saved = checkpoint.dump(messages)
    assert "base64" not in json.dumps(saved)
    restored = checkpoint.load(saved)
    assert isinstance(restored[0], HumanMessage) and "left out" in restored[0].content
    assert isinstance(restored[1], AIMessage) and len(restored) <= checkpoint.MAX_MESSAGES + 1
    assert all(len(m.content) <= checkpoint.MAX_TOOL_TEXT + 10 for m in restored if isinstance(m, ToolMessage))
    assert isinstance(restored[-1], AIMessage)  # the next person's message follows a model turn


async def test_a_conversation_resumes_from_where_the_agent_left_off(core, memory):
    saved = with_conversations(core)
    # While reading a document, the agent opens a conversation and parks the document.
    opener = ScriptedModel(script=[
        AIMessage(content="This column is new; I'll raise it.", tool_calls=[call("open_conversation", {
            "title": "OT column", "message": "Rosters have an OT column. What is it?", "waiting_document_ids": ["doc1"]})]),
        AIMessage(content="Parked doc1 until the admin explains OT."),
    ])
    await _handle(core, memory, opener, make_event("document.submitted", {"document_id": "doc1"}, event_id="e-open"))
    assert core.doc_status["doc1"] == "AWAITING_INPUT"
    assert "OT column" in json.dumps(saved["conv1"])  # its working state, saved on the conversation

    # The admin replies: the agent continues from that state, with its full tools.
    resumer = ScriptedModel(script=[
        AIMessage(content="", tool_calls=[call("reply_to_user", {"content": "Thanks, I'll capture overtime."})]),
        AIMessage(content="", tool_calls=[call("resolve_conversation", {"conversation_id": "conv1", "resolution": "OT is overtime."})]),
        AIMessage(content="Done."),
    ])
    reply = make_event("human.message", {"conversation_id": "conv1", "message_id": "m9", "text": "OT is overtime",
                                         "conversation_kind": "agent", "conversation_title": "OT column",
                                         "sender": {"name": "Asha", "role": "Admin"}}, event_id="e-reply")
    assert await _handle(core, memory, resumer, reply) == "COMPLETED"
    first_prompt = resumer.seen[0]
    assert any("Parked doc1 until the admin explains OT." in str(m.content) for m in first_prompt)
    assert "replied in the conversation you opened" in str(first_prompt[-1].content)
    calls = [(m, p) for m, p, _ in core.requests]
    assert ("POST", "/conversations/conv1/resolve") in calls
    # Resolved in this run: its state isn't saved again.
    assert [p for m, p in calls if m == "PUT"].count("/conversations/conv1/state") == 1


async def test_the_agent_sees_its_page_budget_when_it_wakes_and_after_each_page(core, memory):
    from agentic_framework.agent.briefing import build_chat_briefing
    from agentic_framework.agent.tools import RunContext, execute
    core.page_budget = {"kind": "chat", "left": 2, "this_message": {"limit": 3, "used": 1, "left": 2},
                        "person_today": {"limit": 20, "used": 7, "left": 13}}
    event = make_event("human.message", {"conversation_id": "c1", "message_id": "m1", "text": "who worked at S12?"})
    ctx = RunContext(event=event, api=core.api(), memory=memory)
    briefing = await build_chat_briefing(ctx)
    assert "2 of 3 pages left for this message, 13 of 20 left today" in briefing
    assert "search_past_records and get_document" in briefing
    outcome = await execute(ctx, "view_document", {"document_id": "doc1"})
    assert "2 of 3 pages left for this message" in outcome.text and outcome.images


async def test_list_questions_use_one_record_query(core, memory):
    from agentic_framework.agent.tools import CHAT_TOOLS, RunContext, execute
    core.add("POST", r"/records/query", lambda r, m: (200, {"rows_matched": 3, "distinct": [{"values": {"name": "Perera"}, "rows": 2}]}))
    ctx = RunContext(event=make_event("human.message", {"conversation_id": "c1", "message_id": "m1", "text": "day shift?"}),
                     api=core.api(), memory=memory)
    out = await execute(ctx, "query_records", {"object_type": "Guard", "distinct": ["employee_id", "name"],
                                               "where_json": json.dumps([{"field": "in_time", "op": "eq", "value": "07:00"}])}, CHAT_TOOLS)
    assert "Perera" in out.text
    [(_, _, body)] = [r for r in core.requests if r[1] == "/records/query"]
    assert body["object"] == "Guard" and body["where"][0]["value"] == "07:00" and body["distinct"] == ["employee_id", "name"]


async def test_stop_ends_a_chat_reply_while_the_model_is_still_thinking(core, memory, monkeypatch):
    import asyncio
    import time
    import agentic_framework.agent.loop as loop_module

    class SlowModel(ScriptedModel):
        async def ainvoke(self, messages, *args, **kwargs):
            await asyncio.sleep(30)

    monkeypatch.setattr(loop_module, "STOP_POLL_SECONDS", 0.1)
    event = make_event("human.message", {"conversation_id": "c1", "message_id": "m1", "text": "list everything"}, event_id="e-stop")

    async def press_stop():
        await asyncio.sleep(0.3)
        core.cancelled = True

    started = time.monotonic()
    stopper = asyncio.create_task(press_stop())
    status = await _handle(core, memory, SlowModel(script=[]), event)
    await stopper
    assert status == "CANCELLED" and time.monotonic() - started < 5
    # No "I couldn't finish" reply after a person stopped it.
    assert not [p for m, p, _ in core.requests if (m, p) == ("POST", "/conversations/c1/messages")]

    # Stopped before it even started: it doesn't run at all.
    core.requests.clear()
    assert await _handle(core, memory, SlowModel(script=[]), make_event("human.message", {
        "conversation_id": "c1", "message_id": "m2", "text": "again"}, event_id="e-stop-2")) == "CANCELLED"
    assert ("GET", "/workspace") not in [(m, p) for m, p, _ in core.requests]
