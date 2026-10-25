"""
End-to-end contract between the core server and the agent service, without Kafka or an LLM:
humans act through /api/v1, the "agent" acts through /internal/agent/v1 with a scoped token.
"""
import asyncio
import copy
from app.services import conversation_service as conversation_module
from tests.conftest import SCHEMA, VALID_RECORD, agent, create_template, join, outbox_events, person, png_bytes, register, upload, ws

API = "/api/v1"
AGENT = "/internal/agent/v1"


async def test_template_scope_is_required_and_enforced(client):
    tpl_a = await create_template(client, "A")
    tpl_b = await create_template(client, "B")
    doc_a = await upload(client, tpl_a)

    assert (await client.get(f"{API}/documents")).status_code == 400
    assert (await client.get(f"{API}/documents/{doc_a}", headers=ws(tpl_b))).status_code == 404
    assert (await client.get(f"{API}/documents/{doc_a}", headers=ws(tpl_a))).status_code == 200
    # An agent woken in workspace B cannot see workspace A's documents.
    assert (await client.get(f"{AGENT}/documents/{doc_a}", headers=agent(tpl_b))).status_code == 404
    assert (await client.get(f"{AGENT}/workspace", headers={"Authorization": "Bearer forged"})).status_code == 401


async def test_document_processing_contract(client):
    tpl = await create_template(client)
    doc = await upload(client, tpl)

    res = await client.post(f"{API}/documents/{doc}/process", headers=ws(tpl))
    assert res.status_code == 202 and res.json()["status"] == "QUEUED"
    events = await outbox_events(tpl, "document.submitted")
    assert [e.payload["document_id"] for e in events] == [doc]

    overview = (await client.get(f"{AGENT}/workspace", headers=agent(tpl))).json()
    assert overview["template"]["active_version"]["version"] == "1.0.0"

    page = await client.get(f"{AGENT}/documents/{doc}/image", headers=agent(tpl))
    zoom = await client.get(f"{AGENT}/documents/{doc}/image", params={"region": "0,0,500,500"}, headers=agent(tpl))
    assert page.headers["content-type"] == "image/jpeg" and zoom.status_code == 200

    # The platform refuses a COMMIT that breaks the template, and says why.
    bad = copy.deepcopy(VALID_RECORD)
    bad["Guard"][0]["overtime"] = "OT"
    res = await client.post(f"{AGENT}/documents/{doc}/record", headers=agent(tpl),
                            json={"data": bad, "decision": "COMMIT", "confidence": 0.9})
    assert res.status_code == 422
    assert res.json()["detail"]["errors"][0]["code"] == "unknown_field"

    res = await client.post(f"{AGENT}/documents/{doc}/record", headers=agent(tpl),
                            json={"data": VALID_RECORD, "decision": "COMMIT", "confidence": 0.97,
                                  "evidence": [{"object": "Guard", "index": 0, "field": "name", "value": "Perera", "raw_text": "P.rera"}]})
    assert res.status_code == 200 and res.json()["status"] == "COMPLETED"
    ext = (await client.get(f"{API}/extractions/{doc}", headers=ws(tpl))).json()
    assert ext["extracted_data"] == VALID_RECORD and ext["validation"]["valid"]

    found = (await client.get(f"{AGENT}/records/search", params={"q": "75599"}, headers=agent(tpl))).json()
    assert [r["document_id"] for r in found] == [doc]


async def test_review_round_trip_wakes_agent_with_the_correction(client):
    tpl = await create_template(client)
    doc = await upload(client, tpl)
    draft = copy.deepcopy(VALID_RECORD)
    draft["Guard"][0]["name"] = "P?rera"
    res = await client.post(f"{AGENT}/documents/{doc}/record", headers=agent(tpl), json={
        "data": draft, "decision": "REVIEW", "confidence": 0.5, "review_reason": "Surname is smudged",
        "uncertain_fields": [{"field": "Guard[0].name", "value": "P?rera", "why": "smudged"}],
    })
    assert res.json()["status"] == "NEEDS_REVIEW"

    reviews = (await client.get(f"{API}/reviews", headers=ws(tpl))).json()
    assert len(reviews) == 1 and reviews[0]["uncertain_fields"][0]["why"] == "smudged"
    res = await client.post(f"{API}/reviews/{reviews[0]['id']}/decide", headers=ws(tpl),
                            json={"action": "CORRECT", "corrected_data": VALID_RECORD, "notes": "It is Perera"})
    assert res.status_code == 200 and res.json()["status"] == "CORRECTED"
    doc_state = (await client.get(f"{API}/documents/{doc}", headers=ws(tpl))).json()
    assert doc_state["status"] == "COMPLETED"

    [event] = await outbox_events(tpl, "review.completed")
    assert event.payload["corrected_data"] == VALID_RECORD and event.payload["notes"] == "It is Perera"


async def test_agent_question_is_answered_and_wakes_the_agent(client):
    tpl = await create_template(client)
    doc = await upload(client, tpl)
    body = {"question": "What does 'OT' next to hours mean?", "context": "Seen on 8 of 10 rosters",
            "options": ["Overtime", "Off-time"], "evidence_document_ids": [doc], "related_document_id": doc}
    q = (await client.post(f"{AGENT}/questions", headers=agent(tpl), json=body)).json()
    again = (await client.post(f"{AGENT}/questions", headers=agent(tpl), json=body)).json()
    assert again["id"] == q["id"] and again["duplicate"]

    open_qs = (await client.get(f"{API}/questions", headers=ws(tpl))).json()
    assert [x["id"] for x in open_qs] == [q["id"]]
    res = await client.post(f"{API}/questions/{q['id']}/answer", headers=ws(tpl), json={"answer": "Overtime, billed at 1.5x"})
    assert res.json()["status"] == "ANSWERED"
    [event] = await outbox_events(tpl, "human.answered")
    assert event.payload["answer"] == "Overtime, billed at 1.5x"


async def test_template_change_needs_approval_and_activates_a_new_version(client):
    tpl = await create_template(client)
    invalid = {"proposal_type": "TEMPLATE_CHANGE", "observation": "x",
               "proposed_data": {"schema_definition": {"objects": [], "validation_rules": []}}}
    assert (await client.post(f"{AGENT}/proposals", headers=agent(tpl), json=invalid)).status_code == 422

    new_schema = copy.deepcopy(SCHEMA)
    new_schema["objects"][1]["fields"].append({"name": "overtime", "type": "boolean"})
    proposal = (await client.post(f"{AGENT}/proposals", headers=agent(tpl), json={
        "proposal_type": "TEMPLATE_CHANGE", "observation": "'OT' marks overtime on most rosters",
        "proposed_data": {"schema_definition": new_schema, "change_summary": "Capture overtime"},
        "evidence": {"checked_documents": 10, "would_change": 8},
    })).json()

    # Not applied until a human approves.
    assert len((await client.get(f"{API}/templates/{tpl}/versions")).json()) == 1
    res = await client.post(f"{API}/knowledge/proposals/{proposal['id']}/review", headers=ws(tpl), json={"decision": "APPROVE"})
    assert res.json()["status"] == "APPROVED"

    template = (await client.get(f"{API}/templates/{tpl}")).json()
    assert template["schema_definition"] == new_schema
    versions = (await client.get(f"{API}/templates/{tpl}/versions")).json()
    assert sorted(v["status"] for v in versions) == ["ACTIVE", "ARCHIVED"]
    [event] = await outbox_events(tpl, "proposal.decided")
    assert event.payload["decision"] == "APPROVED" and event.payload["version"] == "1.1.0"


async def test_template_discovery_lives_inside_a_draft_workspace(client):
    res = await client.post(f"{API}/templates/discover", data={"user_notes": "Daily guard rosters", "session_id": "tpl_test01"},
                            files=[("files", ("s1.png", png_bytes(), "image/png")), ("files", ("s2.png", png_bytes(), "image/png"))])
    assert res.status_code == 200, res.text
    tpl = res.json()["template_id"]
    [event] = await outbox_events(tpl, "template.samples_added")
    assert len(event.payload["sample_document_ids"]) == 2 and event.payload["channel_id"] == "tpl_test01"

    samples = (await client.get(f"{AGENT}/documents", params={"role": "SAMPLE"}, headers=agent(tpl))).json()
    assert len(samples) == 2
    # No records can be stored before the template has an active version.
    assert (await client.post(f"{API}/documents/{samples[0]['id']}/process", headers=ws(tpl))).status_code in (404, 409)

    await client.post(f"{AGENT}/proposals", headers=agent(tpl), json={
        "proposal_type": "TEMPLATE_CHANGE", "observation": "Rosters list guards per site and day",
        "proposed_data": {"schema_definition": SCHEMA}, "channel_id": "tpl_test01",
    })
    res = await client.post(f"{API}/templates", json={"id": tpl, "name": "Guard roster", "schema_definition": SCHEMA})
    assert res.status_code == 201 and res.json()["active_version_id"]
    proposals = (await client.get(f"{API}/knowledge/proposals", params={"status": "APPROVED"}, headers=ws(tpl))).json()
    assert len(proposals) == 1


async def test_admin_withdraws_a_proposal_and_the_agent_studies_the_samples_again(client):
    res = await client.post(f"{API}/templates/discover", data={"session_id": "tpl_test02"},
                            files=[("files", ("s1.png", png_bytes(), "image/png")), ("files", ("s2.png", png_bytes(), "image/png"))])
    tpl = res.json()["template_id"]
    await client.post(f"{AGENT}/proposals", headers=agent(tpl), json={
        "proposal_type": "TEMPLATE_CHANGE", "observation": "A first try", "proposed_data": {"schema_definition": SCHEMA}})
    await client.post(f"{AGENT}/questions", headers=agent(tpl), json={"question": "What is column 4?", "context": "Unlabelled"})

    chat = (await client.post(f"{API}/conversations", headers=ws(tpl), json={"title": "Design"})).json()
    res = await client.post(f"{API}/templates/{tpl}/proposal/withdraw", headers={**ws(tpl), "X-Conversation-ID": chat["id"]})
    assert res.json() == {"proposals": 1, "questions_closed": 1}
    assert (await client.post(f"{API}/templates/{tpl}/proposal/withdraw", headers=ws(tpl))).status_code == 409
    assert (await client.get(f"{API}/knowledge/proposals", headers=ws(tpl))).json() == []
    # The withdrawal is part of the chat, and does not wake the agent.
    thread = (await client.get(f"{API}/conversations/{chat['id']}", headers=ws(tpl))).json()
    assert [m["role"] for m in thread["messages"]] == ["system"]
    assert await outbox_events(tpl, "human.message") == []

    # Sending no new files asks the agent to study the existing samples again.
    res = await client.post(f"{API}/templates/discover", data={"template_id": tpl, "session_id": "tpl_test02"})
    assert res.status_code == 200, res.text
    first, again = await outbox_events(tpl, "template.samples_added")
    assert sorted(again.payload["sample_document_ids"]) == sorted(first.payload["sample_document_ids"])


async def test_chat_waits_for_the_agents_reply(client, monkeypatch):
    monkeypatch.setattr(conversation_module, "REPLY_TIMEOUT_SECONDS", 10)
    tpl = await create_template(client)
    conv = (await client.post(f"{API}/conversations", headers=ws(tpl), json={"title": "Q"})).json()

    async def fake_agent():
        for _ in range(100):
            events = await outbox_events(tpl, "human.message")
            if events:
                p = events[0].payload
                await client.post(f"{AGENT}/conversations/{p['conversation_id']}/messages", headers=agent(tpl),
                                  json={"content": "Perera worked 12h.", "reply_to": p["message_id"]})
                return
            await asyncio.sleep(0.05)

    task = asyncio.create_task(fake_agent())
    res = await client.post(f"{API}/conversations/{conv['id']}/messages", headers=ws(tpl), json={"content": "How long did Perera work?"})
    await task
    assert res.json()["content"] == "Perera worked 12h."
    full = (await client.get(f"{API}/conversations/{conv['id']}", headers=ws(tpl))).json()
    assert [m["role"] for m in full["messages"]] == ["user", "assistant"]


async def test_entity_search_ranks_candidates_and_uses_aliases(client):
    tpl = await create_template(client)
    for name, emp in (("Kamal Perera", "75599"), ("Nimal Silva", "75600"), ("Sunil Pereira", "75601")):
        res = await client.post(f"{API}/knowledge/entities", headers=ws(tpl),
                                json={"entity_type": "Guard", "name": name, "identifier_key": "employee_id", "identifier_value": emp})
        assert res.status_code == 201
    res = (await client.get(f"{AGENT}/entities/search", params={"q": "K. Perera"}, headers=agent(tpl))).json()
    assert res["candidates"][0]["name"] == "Kamal Perera"

    silva = (await client.get(f"{AGENT}/entities/search", params={"q": "75600"}, headers=agent(tpl))).json()["candidates"][0]
    await client.post(f"{AGENT}/entities/{silva['id']}/aliases", headers=agent(tpl), json={"alias": "Nimmi", "source": "review r1"})
    res = (await client.get(f"{AGENT}/entities/search", params={"q": "nimmi"}, headers=agent(tpl))).json()
    assert res["candidates"][0]["id"] == silva["id"] and res["candidates"][0]["matched_on"] == "alias"


async def test_agent_cannot_commit_an_identity_that_contradicts_known_records(client):
    schema = copy.deepcopy(SCHEMA)
    schema["objects"][1]["identity"] = {"id_field": "employee_id", "label_field": "name"}
    tpl = await create_template(client, schema=schema)
    first, second, third = await upload(client, tpl), await upload(client, tpl), await upload(client, tpl)

    def record(emp, name, raw=None, entity_id=None):
        r = copy.deepcopy(VALID_RECORD)
        r["Guard"][0].update(employee_id=emp, name=name)
        ev = {"object": "Guard", "index": 0, "field": "name", "value": name, "raw_text": raw or name, "entity_id": entity_id}
        return {"data": r, "decision": "COMMIT", "confidence": 0.95, "evidence": [ev]}

    assert (await client.post(f"{AGENT}/documents/{first}/record", headers=agent(tpl), json=record("55363", "W.A.A. Wijesinghe"))).status_code == 200
    # Same person written slightly differently: fine.
    assert (await client.post(f"{AGENT}/documents/{second}/record", headers=agent(tpl), json=record("55363", "Wijesinghe WAA"))).status_code == 200

    # A known name borrowed for a different ID is refused, with the reason.
    res = await client.post(f"{AGENT}/documents/{third}/record", headers=agent(tpl), json=record("76399", "W.A.A. Wijesinghe"))
    assert res.status_code == 422
    assert "already known with employee_id 55363" in res.json()["detail"]["errors"][0]["message"]
    # A known ID under a different name is refused too.
    res = await client.post(f"{AGENT}/documents/{third}/record", headers=agent(tpl), json=record("55363", "S. Perera"))
    assert "is already known as" in res.json()["detail"]["errors"][0]["message"]
    # Sending it to a person is always possible.
    res = await client.post(f"{AGENT}/documents/{third}/record", headers=agent(tpl), json={
        **record("76399", "W.A.A. Wijesinghe"), "decision": "REVIEW", "review_reason": "Name matches a guard with another ID"})
    assert res.json()["status"] == "NEEDS_REVIEW"


def test_identity_fields_must_exist():
    from app.services.rule_engine import check_rule_definitions
    schema = copy.deepcopy(SCHEMA)
    schema["objects"][1]["identity"] = {"id_field": "badge", "label_field": "name"}
    assert any("identity.id_field" in p for p in check_rule_definitions(schema))



async def test_agent_cannot_commit_a_name_that_is_not_on_the_page(client):
    schema = copy.deepcopy(SCHEMA)
    schema["objects"][1]["identity"] = {"id_field": "employee_id", "label_field": "name"}
    tpl = await create_template(client, schema=schema)
    known_doc, smudged_doc, foreign_doc = await upload(client, tpl), await upload(client, tpl), await upload(client, tpl)

    def body(emp, name, raw, entity_id=None, evidence=True):
        r = copy.deepcopy(VALID_RECORD)
        r["Guard"][0].update(employee_id=emp, name=name)
        ev = [{"object": "Guard", "index": 0, "field": "name", "value": name, "raw_text": raw, "entity_id": entity_id}] if evidence else []
        return {"data": r, "decision": "COMMIT", "confidence": 0.9, "evidence": ev}

    # Identity names need evidence of what is written.
    res = await client.post(f"{AGENT}/documents/{known_doc}/record", headers=agent(tpl), json=body("75599", "Kamal Perera", "", evidence=False))
    assert res.json()["detail"]["errors"][0]["code"] == "identity_evidence_missing"
    assert (await client.post(f"{AGENT}/documents/{known_doc}/record", headers=agent(tpl), json=body("75599", "Kamal Perera", "Kamal Perera"))).status_code == 200

    # Entities come from committed records only, never from the document being decided.
    res = await client.post(f"{AGENT}/entities", headers=agent(tpl), json={
        "entity_type": "Guard", "name": "Invented", "identifier_value": "1", "source_document_id": smudged_doc, "reason": "x"})
    assert res.status_code == 409
    # Only kinds of things the template identifies can be registered.
    res = await client.post(f"{AGENT}/entities", headers=agent(tpl), json={
        "entity_type": "Roster", "name": "S12", "identifier_value": "S12", "source_document_id": known_doc, "reason": "x"})
    assert res.status_code == 422
    res = await client.post(f"{AGENT}/entities", headers=agent(tpl), json={
        "entity_type": "Guard", "name": "Kamal Perera", "identifier_key": "employee_id", "identifier_value": "75599",
        "source_document_id": known_doc, "reason": "Committed record"})
    entity_id = res.json()["id"]

    # A smudged name next to a known ID is recognized through the known entity.
    res = await client.post(f"{AGENT}/documents/{smudged_doc}/record", headers=agent(tpl),
                            json=body("75599", "Kamal Perera", "K. P.r.ra", entity_id=entity_id))
    assert res.status_code == 200, res.text

    # An unreadable name with an unknown ID cannot be "recognized" as anyone.
    res = await client.post(f"{AGENT}/documents/{foreign_doc}/record", headers=agent(tpl),
                            json=body("76399", "Kamal Perera", "SSP සරත්", entity_id=entity_id))
    assert res.status_code == 422
    assert {e["code"] for e in res.json()["detail"]["errors"]} >= {"identity_unsupported"}
    # Recording what is written is fine.
    res = await client.post(f"{AGENT}/documents/{foreign_doc}/record", headers=agent(tpl),
                            json=body("76399", "SSP සරත්", "SSP සරත්"))
    assert res.status_code == 200, res.text


async def test_workspaces_have_unique_slugs_and_a_draft_state(client):
    a = (await client.post(f"{API}/workspaces", json={"name": "Supplier invoices"})).json()
    b = (await client.post(f"{API}/workspaces", json={"name": "Supplier invoices"})).json()
    assert (a["slug"], b["slug"]) == ("supplier-invoices", "supplier-invoices-2")
    assert a["status"] == "draft"
    got = (await client.get(f"{API}/workspaces/{a['slug']}")).json()
    assert got["id"] == a["id"]
    assert (await client.get(f"{API}/workspaces/no-such-workspace")).status_code == 404
    # A draft cannot take documents yet.
    res = await client.post(f"{API}/documents/upload", headers=ws(a["id"]), files={"file": ("x.png", png_bytes(), "image/png")})
    assert res.status_code == 409


async def test_upload_hands_the_document_to_the_agent_and_chat_can_be_async(client):
    tpl = await create_template(client, "Async")
    res = await client.post(f"{API}/documents/upload", headers=ws(tpl), files={"file": ("r.png", png_bytes(), "image/png")})
    assert res.json()["status"] == "QUEUED"
    assert len(await outbox_events(tpl, "document.submitted")) == 1

    thread = (await client.post(f"{API}/conversations", headers=ws(tpl), json={"title": "Today"})).json()
    other = (await client.post(f"{API}/conversations", headers=ws(tpl), json={"title": "Another"})).json()
    listed = (await client.get(f"{API}/conversations", headers=ws(tpl))).json()
    assert {c["id"] for c in listed["items"]} >= {thread["id"], other["id"]} and "messages" not in listed["items"][0]
    # Chats are private: someone else in the workspace, even an admin, can't open yours; an outsider is refused at the door.
    colleague = person("usr_colleague", "Colleague")
    await join(client, tpl, colleague, "admin")
    assert (await client.get(f"{API}/conversations/{thread['id']}", headers={**ws(tpl), **colleague})).status_code == 404
    outsider = person("usr_stranger", "Stranger")
    await register(outsider)
    assert (await client.get(f"{API}/conversations/{thread['id']}", headers={**ws(tpl), **outsider})).status_code == 403
    msg = (await client.post(f"{API}/conversations/{thread['id']}/messages", params={"wait": "false"},
                             headers=ws(tpl), json={"content": "What changed today?"})).json()
    assert msg["role"] == "user"
    [event] = await outbox_events(tpl, "human.message")
    assert event.payload["conversation_id"] == thread["id"]
