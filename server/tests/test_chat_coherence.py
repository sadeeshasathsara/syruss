"""
Chats that make sense: a reply goes only where a person wrote, a thread is answered before it is resolved, a chat keeps
the mode it started with, page work stays out of chats, and what people read names documents, not ids.
"""
import copy
from sqlalchemy import update
from app.core.agent_token import mint_agent_token
from app.core.database import AsyncSessionLocal
from app.models.document import Document
from app.models.outbox import AgentEventOutbox
from tests.conftest import TENANT, VALID_RECORD, agent, create_template, outbox_events, upload, ws

API = "/api/v1"
AGENT = "/internal/agent/v1"


def token_for(tpl: str, event_id: str) -> dict:
    return {"Authorization": f"Bearer {mint_agent_token(TENANT, tpl, event_id)}"}


async def document_run(tpl: str, document_id: str) -> str:
    async with AsyncSessionLocal() as s:
        row = AgentEventOutbox(tenant_id=TENANT, template_id=tpl, event_type="document.submitted",
                               payload={"document_id": document_id}, actor={})
        s.add(row)
        await s.commit()
        return row.id


async def new_chat(client, tpl: str) -> str:
    return (await client.post(f"{API}/conversations", headers=ws(tpl), json={})).json()["id"]


async def say(client, tpl: str, chat: str, text: str, context: dict | None = None):
    await client.post(f"{API}/conversations/{chat}/messages?wait=false", headers=ws(tpl), json={"content": text, "context": context or {}})
    return (await outbox_events(tpl, "human.message"))[-1]


async def test_a_reply_goes_only_where_a_person_wrote(client):
    tpl = await create_template(client)
    chat, other = await new_chat(client, tpl), await new_chat(client, tpl)
    event = await say(client, tpl, chat, "hello")
    assert (await client.post(f"{AGENT}/conversations/{chat}/messages", headers=token_for(tpl, event.id),
                              json={"content": "Hi.", "reply_to": event.payload["message_id"]})).status_code == 201
    assert (await client.post(f"{AGENT}/conversations/{other}/messages", headers=token_for(tpl, event.id),
                              json={"content": "Hi."})).status_code == 403
    # Reading a document writes in nobody's chat, whatever chat was open when it was uploaded.
    run = await document_run(tpl, await upload(client, tpl))
    res = await client.post(f"{AGENT}/conversations/{chat}/messages", headers=token_for(tpl, run), json={"content": "Read it."})
    assert res.status_code == 403 and "only reply where a person wrote" in res.json()["detail"]
    messages = (await client.get(f"{API}/conversations/{chat}", headers=ws(tpl))).json()["messages"]
    assert [m["content"] for m in messages] == ["hello", "Hi."]


async def test_a_thread_is_answered_before_it_is_resolved(client):
    tpl = await create_template(client)
    await client.get(f"{API}/settings", headers=ws(tpl))   # the owner, who can settle what Syruss raises
    opened = (await client.post(f"{AGENT}/conversations", headers=agent(tpl), json={
        "title": "Unlabelled column", "message": "Some pages have a column without a heading. What is it?"})).json()
    event = await say(client, tpl, opened["id"], "It is the quantity.")
    as_run = token_for(tpl, event.id)
    early = await client.post(f"{AGENT}/conversations/{opened['id']}/resolve", headers=as_run, json={"resolution": "It is the quantity."})
    assert early.status_code == 409 and "Reply to them" in early.json()["detail"]
    await client.post(f"{AGENT}/conversations/{opened['id']}/messages", headers=as_run, json={"content": "Thanks, I'll read it as the quantity."})
    assert (await client.post(f"{AGENT}/conversations/{opened['id']}/resolve", headers=as_run,
                              json={"resolution": "It is the quantity."})).status_code == 200
    thread = (await client.get(f"{API}/conversations/{opened['id']}", headers=ws(tpl))).json()["messages"]
    assert [m["role"] for m in thread] == ["assistant", "user", "assistant", "system"]   # the answer, then "Resolved"


async def test_a_chat_keeps_the_mode_it_started_with_and_each_message_its_own_page(client):
    tpl = await create_template(client)
    doc = await upload(client, tpl)
    chat = await new_chat(client, tpl)
    first = await say(client, tpl, chat, "a must exceed b", {"mode": "rules", "current_page": "/org/w/ws/template/rules/new"})
    then = await say(client, tpl, chat, "and on this one?", {"mode": "screens", "current_page": f"/org/w/ws/documents/{doc}"})
    conv = (await client.get(f"{API}/conversations/{chat}", headers=ws(tpl))).json()
    assert conv["mode"] == "rules" and conv["context_metadata"] == {}
    assert first.payload["conversation_mode"] == then.payload["conversation_mode"] == "rules"
    assert first.payload["page"] == "Template > Rules > New rule" and then.payload["page"] == "the document roster.png"
    assert conv["messages"][1]["agent_trace"]["context"]["current_page"].endswith(doc)


async def test_what_syruss_writes_names_documents_by_their_names(client):
    tpl = await create_template(client)
    doc = await upload(client, tpl)
    async with AsyncSessionLocal() as s:
        await s.execute(update(Document).where(Document.id == doc).values(identified_key="North_2026-07-22"))
        await s.commit()
    chat = await new_chat(client, tpl)
    event = await say(client, tpl, chat, "what did you read?")
    await client.post(f"{AGENT}/conversations/{chat}/messages", headers=token_for(tpl, event.id),
                      json={"content": f"I read `{doc}` ('roster.png'); see documents/{doc}."})
    reply = (await client.get(f"{API}/conversations/{chat}", headers=ws(tpl))).json()["messages"][-1]["content"]
    assert reply == f"I read North_2026-07-22 (North_2026-07-22); see documents/{doc}."   # a path keeps its id


async def test_a_correction_opens_its_own_chat_about_that_document(client):
    tpl = await create_template(client)
    doc = await upload(client, tpl)
    assert (await client.post(f"{AGENT}/documents/{doc}/record", headers=agent(tpl),
                              json={"data": VALID_RECORD, "decision": "COMMIT", "confidence": 0.95})).status_code == 200
    open_chat = await new_chat(client, tpl)
    corrected = copy.deepcopy(VALID_RECORD)
    obj = next(k for k, v in corrected.items() if isinstance(v, list) and v and isinstance(v[0], dict))
    field = next(k for k, v in corrected[obj][0].items() if isinstance(v, str))
    corrected[obj][0][field] = "Corrected value"
    res = (await client.post(f"{API}/documents/{doc}/corrections", headers={**ws(tpl), "X-Conversation-ID": open_chat},
                             json={"data": corrected, "reasons": [{"object": obj, "reason": "misread"}]})).json()
    assert res["conversation_id"] != open_chat
    conv = (await client.get(f"{API}/conversations/{res['conversation_id']}", headers=ws(tpl))).json()
    assert conv["title"].startswith("Correction: ") and conv["messages"][0]["content"].startswith("I corrected the saved record")
    assert (await client.get(f"{API}/conversations/{open_chat}", headers=ws(tpl))).json()["messages"] == []


async def test_a_chat_is_named_by_its_topic_unless_someone_named_it(client):
    tpl = await create_template(client)
    chat = await new_chat(client, tpl)
    event = await say(client, tpl, chat, "hi there")
    as_run = token_for(tpl, event.id)
    assert (await client.put(f"{AGENT}/conversations/{chat}/title", headers=as_run, json={"title": "Greeting", "was": "hi there"})).json()["renamed"]
    await client.patch(f"{API}/conversations/{chat}", headers=ws(tpl), json={"title": "My own name"})
    assert not (await client.put(f"{AGENT}/conversations/{chat}/title", headers=as_run, json={"title": "Other", "was": "Greeting"})).json()["renamed"]
    assert (await client.get(f"{API}/conversations/{chat}", headers=ws(tpl))).json()["title"] == "My own name"


async def test_threads_syruss_started_are_listed_apart_open_first(client):
    tpl = await create_template(client)
    await client.get(f"{API}/settings", headers=ws(tpl))
    flag = (await client.post(f"{AGENT}/questions", headers=agent(tpl), json={"question": "Is this a total?", "context": "One page shows a figure at the bottom."})).json()
    issue = (await client.post(f"{AGENT}/conversations", headers=agent(tpl), json={"title": "Two layouts", "message": "Pages come in two layouts."})).json()
    await client.post(f"{AGENT}/conversations/{issue['id']}/resolve", headers=agent(tpl), json={"resolution": "Both are read."})
    await new_chat(client, tpl)
    listed = (await client.get(f"{API}/conversations", params={"kind": "syruss"}, headers=ws(tpl))).json()
    assert [c["id"] for c in listed["items"]] == [flag["conversation_id"], issue["id"]] and listed["open"] == 1
    assert {c["kind"] for c in (await client.get(f"{API}/conversations", headers=ws(tpl))).json()["items"]} == {"chat"}
