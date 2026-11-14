import json
from langchain_core.messages import AIMessage
from agentic_framework.agent.loop import WorkspaceAgent
from agentic_framework.worker import AgentWorker
from agentic_framework.tests.conftest import ScriptedModel, call, make_event


def with_conversations(core):
    """Fake core routes for conversations the agent opens, with the note it keeps there of where it left off."""
    state = {}

    def open_conversation(request, m):
        body = json.loads(request.content)
        return 201, {"id": "conv1", "title": body["title"], "status": "open"}

    def put_state(request, m):
        state[m["id"]] = json.loads(request.content)
        return 200, {"saved": True}

    core.add("POST", r"/conversations", open_conversation)
    core.add("GET", r"/conversations/(?P<id>[^/]+)/state", lambda r, m: (200, {"id": m["id"], "state": state.get(m["id"])}))
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
    # A short note of where it left off is kept on the conversation, not a replay of the whole run.
    assert saved["conv1"] == {"note": "Parked doc1 until the admin explains OT.", "document_ids": ["doc1"]}

    # The admin replies: the agent continues from that note and the thread, with its full tools.
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
    assert len(first_prompt) == 2   # the charter and the briefing: no earlier run replayed
    assert "Where you left off last time: Parked doc1 until the admin explains OT." in str(first_prompt[-1].content)
    assert "replied in the conversation you opened" in str(first_prompt[-1].content)
    calls = [(m, p) for m, p, _ in core.requests]
    assert ("POST", "/conversations/conv1/resolve") in calls
    # Resolved in this run: its state isn't saved again.
    assert [p for m, p in calls if m == "PUT"].count("/conversations/conv1/state") == 1


def test_only_a_run_someone_wrote_to_can_reply():
    from agentic_framework.agent.tools import CHAT_TOOLS, work_tools
    assert "reply_to_user" in CHAT_TOOLS
    for woke in (make_event("document.submitted", {"document_id": "d1"}),
                 make_event("template.samples_added", {"sample_document_ids": []}),
                 make_event("proposal.decided", {"proposal_id": "p1"}),
                 make_event("review.completed", {"review_id": "r1", "document_id": "d1", "conversation_id": "c1"})):
        assert "reply_to_user" not in work_tools(woke), woke.type
    assert "reply_to_user" in work_tools(make_event("human.answered", {"question_id": "q1", "conversation_id": "t1"}))
    assert "reply_to_user" in work_tools(make_event("human.message", {"conversation_id": "t1", "conversation_kind": "flag"}))


async def test_a_reply_goes_only_to_the_conversation_that_woke_the_run(core, memory):
    from agentic_framework.agent.tools import RunContext, execute
    work = RunContext(event=make_event("document.submitted", {"document_id": "d1", "channel_id": "c1"}), api=core.api(), memory=memory)
    assert (await execute(work, "reply_to_user", {"content": "Done."})).text.startswith("ERROR: Nobody wrote to you")
    chat = RunContext(event=make_event("human.message", {"conversation_id": "c1", "message_id": "m1", "text": "hi"}),
                      api=core.api(), memory=memory)
    # The conversation can't be chosen: a model that names another one still answers where it was asked.
    await execute(chat, "reply_to_user", {"content": "Hello.", "conversation_id": "someone-else"})
    assert [p for m, p, _ in core.requests if m == "POST"] == ["/conversations/c1/messages"]


async def test_what_a_chat_run_did_travels_with_its_reply_and_is_read_back_later(core, memory):
    from agentic_framework.agent.briefing import _transcript
    from agentic_framework.agent.tools import RunContext, execute
    core.add("POST", r"/memories", lambda r, m: (201, {"id": "mem1"}))
    ctx = RunContext(event=make_event("human.message", {"conversation_id": "c1", "message_id": "m1", "text": "x"}),
                     api=core.api(), memory=memory)
    await execute(ctx, "remember", {"kind": "lesson", "content": "A blank in a column repeats the row above"})
    await execute(ctx, "reply_to_user", {"content": "Noted."})
    [(_, _, body)] = [r for r in core.requests if r[1] == "/conversations/c1/messages"]
    assert body["actions"] == ["remembered (lesson): A blank in a column repeats the row above"]
    history = [{"id": "a", "role": "user", "content": "x"},
               {"id": "b", "role": "assistant", "content": "Noted.", "actions": body["actions"]},
               {"id": "c", "role": "system", "content": "Asha stopped Syruss."}]
    read = _transcript(history, skip="none")
    assert "You: Noted.\n  (before that reply you remembered (lesson)" in read and "Note: Asha stopped Syruss." in read


async def test_a_chat_cut_short_says_so_in_words_not_with_the_runtimes_note(core, memory, monkeypatch):
    from agentic_framework.config import settings
    monkeypatch.setattr(settings, "MAX_STEPS", 2)
    looping = ScriptedModel(script=[AIMessage(content="", tool_calls=[call("list_documents", {})])] * 3)
    await _handle(core, memory, looping, make_event("human.message", {"conversation_id": "c1", "message_id": "m1",
                                                                      "text": "everything?"}, event_id="e-long"))
    [(_, _, body)] = [r for r in core.requests if r[0] == "POST" and r[1] == "/conversations/c1/messages"]
    assert "Stopped" not in body["content"] and "narrow it down" in body["content"]


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
