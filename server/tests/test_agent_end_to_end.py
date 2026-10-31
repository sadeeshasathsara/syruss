"""
The real agent worker against the real core API and database: only the model is scripted. Covers
the two ways Syruss involves people: a flag (here, whether to register someone new when it may not
register on its own) and a conversation it opens, resumes from its checkpoint, and resolves.
"""
import copy
import json
import os
import sys
from pathlib import Path

import httpx
import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # the agent package lives next to server/
os.environ.setdefault("AGENT_MEMORY_DB", "agent_memory_test")
os.environ["EMBEDDINGS_ENABLED"] = "false"

import agentic_framework.worker as worker_module  # noqa: E402
from agentic_framework.agent.loop import WorkspaceAgent  # noqa: E402
from agentic_framework.core_api import CoreAPI  # noqa: E402
from agentic_framework.events import AgentEvent  # noqa: E402
from agentic_framework.memory import MemoryStore  # noqa: E402
from agentic_framework.tests.conftest import ScriptedModel, call  # noqa: E402
from app.core.database import AsyncSessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models.conversation import Conversation  # noqa: E402
from app.models.knowledge import Entity  # noqa: E402
from app.services.agent_events import build_envelope  # noqa: E402
from tests.conftest import SCHEMA, VALID_RECORD, agent, create_template, outbox_events, upload, ws  # noqa: E402

API = "/api/v1"
IDENTIFIED = copy.deepcopy(SCHEMA)
IDENTIFIED["objects"][1]["identity"] = {"id_field": "employee_id", "label_field": "name"}


@pytest.fixture
async def memory():
    store = MemoryStore()
    await store.init()
    yield store
    await store.close()


async def run_agent(memory, model, row) -> str:
    """Hands one outbox event to the real worker, which talks to this API in-process."""
    def core(token: str) -> CoreAPI:
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test/internal/agent/v1",
                                   headers={"Authorization": f"Bearer {token}"}, timeout=30)
        return CoreAPI(token, client=client)
    original = worker_module.CoreAPI
    worker_module.CoreAPI = core
    try:
        return await worker_module.AgentWorker(WorkspaceAgent(model), memory, producer=None).handle(AgentEvent(**build_envelope(row)))
    finally:
        worker_module.CoreAPI = original


def record(employee_id: str, name: str, day: str) -> dict:
    data = copy.deepcopy(VALID_RECORD)
    data["Roster"][0]["date"] = day
    data["Guard"][0].update(employee_id=employee_id, name=name)
    return data


async def test_a_commit_naming_someone_new_is_refused_until_a_person_approves(client, memory):
    tpl = await create_template(client, schema=IDENTIFIED)
    await client.get(f"{API}/settings", headers=ws(tpl))
    await client.patch(f"{API}/settings", headers=ws(tpl), json={"agent": {"may_register_entities": False}})
    doc = await upload(client, tpl)
    await client.post(f"{API}/documents/{doc}/process", headers=ws(tpl))
    [event] = await outbox_events(tpl, "document.submitted")

    commit = call("submit_record", {
        "document_id": doc, "data_json": json.dumps(record("75765", "Sagara Ruwan Priya", "2026-07-21")),
        "decision": "COMMIT", "confidence": 0.95, "unrecorded_content": "none",
        "evidence_json": '[{"object": "Guard", "index": 0, "field": "name", "value": "Sagara Ruwan Priya", "raw_text": "Sagara Ruwan Priya"}]'})
    model = ScriptedModel(script=[
        AIMessage(content="", tool_calls=[commit]),
        # The platform refuses and says why; Syruss asks, parking the document on the question.
        AIMessage(content="", tool_calls=[call("flag", {
            "question": "Should I register Guard 75765 (Sagara Ruwan Priya)?", "context": "New on the 2026-07-21 roster.",
            "answer_type": "single", "options": ["Yes, register", "No"], "waiting_document_ids": [doc]})]),
        AIMessage(content="Asked whether to register them."),
    ])
    assert await run_agent(memory, model, event) == "COMPLETED"
    refusal = next(m.content for m in model.seen[1] if getattr(m, "name", "") == "submit_record")
    assert "REFUSED" in refusal and "unregistered_identity" in refusal and "75765" in refusal
    assert (await client.get(f"{API}/documents/{doc}", headers=ws(tpl))).json()["status"] == "AWAITING_INPUT"

    [flag] = (await client.get(f"{API}/questions", headers=ws(tpl))).json()
    assert "75765" in flag["question"] and flag["options"] == ["Yes, register", "No"]
    # While that's open, another roster naming them is refused with the same question to park on.
    second = await upload(client, tpl)
    res = await client.post(f"/internal/agent/v1/documents/{second}/record", headers=agent(tpl), json={
        "data": record("75765", "Sagara Ruwan Priya", "2026-07-22"), "decision": "COMMIT", "confidence": 0.95,
        "evidence": [{"object": "Guard", "index": 0, "field": "name", "value": "Sagara Ruwan Priya", "raw_text": "Sagara Ruwan Priya"}]})
    assert res.status_code == 422 and "You already asked" in res.json()["detail"]["errors"][0]["message"]

    answer = (await client.post(f"{API}/questions/{flag['id']}/answer", headers=ws(tpl), json={"choices": ["Yes, register"]})).json()
    assert answer["conversation_id"] == flag["conversation_id"] and answer["conversation_status"] == "open"
    # Answered but not resolved: still in Review, as pending.
    [pending] = (await client.get(f"{API}/questions", params={"status": "ACTIVE"}, headers=ws(tpl))).json()
    assert pending["status"] == "ANSWERED" and pending["conversation_status"] == "open"
    [answered] = await outbox_events(tpl, "human.answered")
    assert answered.conversation_id == flag["conversation_id"]

    model = ScriptedModel(script=[
        AIMessage(content="", tool_calls=[call("create_entity", {
            "entity_type": "Guard", "name": "Sagara Ruwan Priya", "identifier_key": "employee_id", "identifier_value": "75765",
            "source_question_id": flag["id"], "reason": "The admin said yes"})]),
        AIMessage(content="", tool_calls=[commit]),
        AIMessage(content="", tool_calls=[call("reply_to_user", {"content": "Registered Sagara Ruwan Priya and saved the roster."})]),
        AIMessage(content="", tool_calls=[call("resolve_conversation", {"conversation_id": flag["conversation_id"], "resolution": "Registered and saved."})]),
        AIMessage(content="Done."),
    ])
    assert await run_agent(memory, model, answered) == "COMPLETED"
    assert (await client.get(f"{API}/documents/{doc}", headers=ws(tpl))).json()["status"] == "COMPLETED"
    # It picked up with its own earlier steps on this flag (saved on the flag's thread).
    assert any("Asked whether to register them." in str(m.content) for m in model.seen[0])
    # The whole exchange is in the flag's thread, which Syruss resolved: no longer pending in Review.
    thread = (await client.get(f"{API}/conversations/{flag['conversation_id']}", headers=ws(tpl))).json()
    assert thread["kind"] == "flag" and thread["status"] == "resolved"
    assert [m["role"] for m in thread["messages"]] == ["assistant", "user", "assistant", "system"]
    assert thread["messages"][1]["content"] == "Yes, register"
    assert (await client.get(f"{API}/questions", params={"status": "ACTIVE"}, headers=ws(tpl))).json() == []
    async with AsyncSessionLocal() as s:
        [entity] = (await s.execute(select(Entity).where(Entity.template_id == tpl))).scalars().all()
    assert (entity.name, entity.identifier_value) == ("Sagara Ruwan Priya", "75765")


async def test_a_conversation_is_opened_resumed_from_its_checkpoint_and_resolved(client, memory):
    tpl = await create_template(client, schema=IDENTIFIED)
    await client.get(f"{API}/settings", headers=ws(tpl))
    doc = await upload(client, tpl, "night-roster.png")
    await client.post(f"{API}/documents/{doc}/process", headers=ws(tpl))
    [event] = await outbox_events(tpl, "document.submitted")

    opener = ScriptedModel(script=[
        AIMessage(content="", tool_calls=[call("open_conversation", {
            "title": "OT column on night rosters", "message": "This roster has an **OT** column the template can't hold. What is it?",
            "audience": "admin", "waiting_document_ids": [doc], "resume_note": "add overtime, then commit"})]),
        AIMessage(content="Parked night-roster.png until the admin explains OT."),
    ])
    assert await run_agent(memory, opener, event) == "COMPLETED"
    inbox = (await client.get(f"{API}/conversations", params={"kind": "agent"}, headers=ws(tpl))).json()
    [conv] = inbox["items"]
    assert (conv["title"], conv["status"]) == ("OT column on night rosters", "open")
    async with AsyncSessionLocal() as s:
        saved = await s.get(Conversation, conv["id"])
        assert "Parked night-roster.png" in json.dumps(saved.agent_state)   # its checkpoint, in Postgres
    assert (await client.get(f"{API}/documents/{doc}", headers=ws(tpl))).json()["status"] == "AWAITING_INPUT"

    # The admin replies in the conversation: Syruss continues from where it left off.
    await client.post(f"{API}/conversations/{conv['id']}/messages?wait=false", headers=ws(tpl), json={"content": "OT is overtime hours."})
    [reply] = await outbox_events(tpl, "human.message")
    resumer = ScriptedModel(script=[
        AIMessage(content="", tool_calls=[call("reply_to_user", {"content": "Thanks: I'll capture overtime."})]),
        AIMessage(content="", tool_calls=[call("resolve_conversation", {"conversation_id": conv["id"], "resolution": "OT is overtime."})]),
        AIMessage(content="Done."),
    ])
    assert await run_agent(memory, resumer, reply) == "COMPLETED"
    assert any("Parked night-roster.png" in str(m.content) for m in resumer.seen[0])

    thread = (await client.get(f"{API}/conversations/{conv['id']}", headers=ws(tpl))).json()
    assert thread["status"] == "resolved"
    assert [m["role"] for m in thread["messages"]] == ["assistant", "user", "assistant", "system"]
    async with AsyncSessionLocal() as s:
        assert (await s.get(Conversation, conv["id"])).agent_state is None
