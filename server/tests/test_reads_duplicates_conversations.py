"""
Records first (page reads are capped), duplicate documents, flags with answer types, and the
conversations Syruss opens (with their checkpoints), plus paged conversation lists.
"""
import copy
import json
from app.core.agent_token import mint_agent_token
from app.core.database import AsyncSessionLocal
from app.models.outbox import AgentEventOutbox
from tests.conftest import TENANT, VALID_RECORD, agent, create_template, join, outbox_events, person, png_bytes, register, upload, ws

API = "/api/v1"
AGENT = "/internal/agent/v1"
REVIEWER = person("usr_flags_reviewer", "Ravi")


def token_for(tpl: str, event_id: str) -> dict:
    return {"Authorization": f"Bearer {mint_agent_token(TENANT, tpl, event_id)}"}


async def work_event(tpl: str, document_id: str) -> str:
    """A run reading one document (as if it had just been submitted)."""
    async with AsyncSessionLocal() as s:
        row = AgentEventOutbox(tenant_id=TENANT, template_id=tpl, event_type="document.submitted",
                               payload={"document_id": document_id}, actor={})
        s.add(row)
        await s.commit()
        return row.id


async def commit(client, tpl: str, doc: str, date: str, name: str = "Perera") -> int:
    record = copy.deepcopy(VALID_RECORD)
    record["Roster"][0]["date"] = date
    record["Guard"][0]["name"] = name
    res = await client.post(f"{AGENT}/documents/{doc}/record", headers=agent(tpl),
                            json={"data": record, "decision": "COMMIT", "confidence": 0.95})
    return res.status_code


async def test_page_reads_are_capped_records_come_first(client):
    tpl = await create_template(client)
    await client.get(f"{API}/settings", headers=ws(tpl))
    read = [await upload(client, tpl) for _ in range(5)]
    for i, d in enumerate(read):
        assert await commit(client, tpl, d, f"2026-08-0{i + 1}") == 200
    new = await upload(client, tpl)

    # Answering a chat message: at most 3 pages; zooming into one already opened is free.
    chat = (await client.post(f"{API}/conversations", headers=ws(tpl), json={"title": "t"})).json()
    await client.post(f"{API}/conversations/{chat['id']}/messages?wait=false", headers=ws(tpl), json={"content": "what's on these?"})
    [event] = [e for e in await outbox_events(tpl, "human.message") if e.payload["conversation_id"] == chat["id"]]
    as_chat = token_for(tpl, event.id)
    budget = (await client.get(f"{AGENT}/page-budget", headers=as_chat)).json()
    assert budget["this_message"] == {"limit": 3, "used": 0, "left": 3} and budget["person_today"]["left"] == 20
    for d in read[:3]:
        res = await client.get(f"{AGENT}/documents/{d}/image", headers=as_chat)
        assert res.status_code == 200
    # Each page tells the agent what is left.
    assert json.loads(res.headers["X-Page-Budget"])["this_message"]["left"] == 0
    assert (await client.get(f"{AGENT}/documents/{read[0]}/image", params={"region": "0,0,500,500"}, headers=as_chat)).status_code == 200
    refused = await client.get(f"{AGENT}/documents/{read[3]}/image", headers=as_chat)
    assert refused.status_code == 429 and "answer from get_document" in refused.json()["detail"]

    # Per person per day.
    await client.patch(f"{API}/settings", headers=ws(tpl), json={"agent": {"pages_per_person_per_day": 4}})
    await client.post(f"{API}/conversations/{chat['id']}/messages?wait=false", headers=ws(tpl), json={"content": "and this one?"})
    second = [e for e in await outbox_events(tpl, "human.message") if e.payload["conversation_id"] == chat["id"]][-1]
    assert (await client.get(f"{AGENT}/documents/{read[3]}/image", headers=token_for(tpl, second.id))).status_code == 200
    refused = await client.get(f"{AGENT}/documents/{read[4]}/image", headers=token_for(tpl, second.id))
    assert refused.status_code == 429 and "Today's limit" in refused.json()["detail"]

    # While reading a new document: its own page is free; already-read pages are capped at 2 per run.
    as_work = token_for(tpl, await work_event(tpl, new))
    assert (await client.get(f"{AGENT}/documents/{new}/image", headers=as_work)).status_code == 200
    for d in read[:2]:
        assert (await client.get(f"{AGENT}/documents/{d}/image", headers=as_work)).status_code == 200
    refused = await client.get(f"{AGENT}/documents/{read[2]}/image", headers=as_work)
    assert refused.status_code == 429 and "its record is in the database" in refused.json()["detail"]
    # Its record is what the agent should use instead.
    doc = (await client.get(f"{AGENT}/documents/{read[2]}", headers=as_work)).json()
    assert doc["record"]["data"]["Guard"][0]["name"] == "Perera"


async def test_a_document_already_recorded_is_caught_with_evidence(client):
    tpl = await create_template(client)
    first = await upload(client, tpl)
    assert await commit(client, tpl, first, "2026-09-01") == 200

    # The same roster again (another scan): same site and day, same people.
    again = await upload(client, tpl)
    record = copy.deepcopy(VALID_RECORD)
    record["Roster"][0]["date"] = "2026-09-01"
    res = await client.post(f"{AGENT}/documents/{again}/record", headers=agent(tpl),
                            json={"data": record, "decision": "COMMIT", "confidence": 0.95})
    assert res.status_code == 422
    [error] = [e for e in res.json()["detail"]["errors"] if e["code"] == "possible_duplicate"]
    assert error["evidence"]["document_id"] == first and error["evidence"]["record"]["Guard"][0]["name"] == "Perera"
    # The agent can flag it for people to decide.
    res = await client.post(f"{AGENT}/documents/{again}/record", headers=agent(tpl), json={
        "data": record, "decision": "REVIEW", "confidence": 0.9, "review_reason": f"Looks like a copy of {first}"})
    assert res.json()["status"] == "NEEDS_REVIEW"
    assert any(s["code"] == "possible_duplicate" for s in res.json()["validation"]["signals"])

    # The next day's roster with the same team is not a duplicate.
    next_day = await upload(client, tpl)
    assert await commit(client, tpl, next_day, "2026-09-02") == 200

    # The identical file uploaded twice is caught even before comparing records.
    image = png_bytes(color=(10, 20, 30))
    ids = []
    for _ in range(2):
        res = await client.post(f"{API}/documents/upload", headers=ws(tpl), data={"process": "false"},
                                files={"file": ("same.png", image, "image/png")})
        ids.append(res.json()["id"])
    assert await commit(client, tpl, ids[0], "2026-09-10") == 200
    check = (await client.post(f"{AGENT}/records/validate", headers=agent(tpl),
                               json={"data": {**VALID_RECORD, "Roster": [{"site_id": "S99", "date": "2026-01-01"}]},
                                     "document_id": ids[1]})).json()
    [signal] = [s for s in check["signals"] if s["code"] == "duplicate_file"]
    # Sure it's different (it isn't, here): acknowledging with a reason lets it through.
    record = copy.deepcopy(VALID_RECORD)
    record["Roster"][0].update(site_id="S99", date="2026-01-01")
    res = await client.post(f"{AGENT}/documents/{ids[1]}/record", headers=agent(tpl), json={
        "data": record, "decision": "COMMIT", "confidence": 0.95,
        "acknowledged_signals": [{"id": signal["id"], "reason": "Second copy is the signed version, kept on purpose"}]})
    assert res.status_code == 200


async def test_flags_take_one_or_many_choices_and_always_allow_other(client):
    tpl = await create_template(client)
    await client.get(f"{API}/settings", headers=ws(tpl))
    single = (await client.post(f"{AGENT}/questions", headers=agent(tpl), json={
        "question": "What does OT mean?", "context": "seen on 8 rosters", "answer_type": "single",
        "options": ["Overtime", "Off-time", "Other"]})).json()
    assert single["options"] == ["Overtime", "Off-time"] and single["answer_type"] == "single"
    assert (await client.post(f"{AGENT}/questions", headers=agent(tpl), json={
        "question": "Pick?", "context": "x", "answer_type": "multiple", "options": ["one"]})).status_code == 422

    answer = lambda q, body: client.post(f"{API}/questions/{q}/answer", headers=ws(tpl), json=body)
    assert (await answer(single["id"], {"choices": ["Overtime", "Off-time"]})).status_code == 422
    assert (await answer(single["id"], {"choices": ["Nope"]})).status_code == 422
    res = await answer(single["id"], {"other": "On training"})
    assert res.status_code == 200 and res.json()["answer"] == "Other: On training"
    [event] = await outbox_events(tpl, "human.answered")
    assert event.payload["other"] == "On training" and event.payload["answer_type"] == "single"

    many = (await client.post(f"{AGENT}/questions", headers=agent(tpl), json={
        "question": "Which columns are hours?", "context": "x", "answer_type": "multiple", "options": ["A", "B", "C"]})).json()
    res = await answer(many["id"], {"choices": ["A", "C"], "other": "and the margin total"})
    assert res.json()["answer"] == "A; C; Other: and the margin total" and res.json()["answer_choices"] == ["A", "C"]

    text = (await client.post(f"{AGENT}/questions", headers=agent(tpl), json={
        "question": "Who signs off rosters?", "context": "x", "answer_type": "text"})).json()
    assert (await answer(text["id"], {})).status_code == 422
    assert (await answer(text["id"], {"answer": "The site supervisor"})).json()["answer"] == "The site supervisor"


async def test_a_flag_carries_the_rows_it_is_about_as_a_table(client):
    tpl = await create_template(client)
    await client.get(f"{API}/settings", headers=ws(tpl))
    table = {"title": "Employees on this sheet", "columns": ["No.", "Name"], "rows": [["74982", "PIYATISSA R."], ["75162", "JAYAWARDANA", "extra"], ["76921"]]}
    asked = (await client.post(f"{AGENT}/questions", headers=agent(tpl), json={
        "question": "Register these employees?", "context": "new sheet", "answer_type": "text", "data_table": table})).json()
    # Rows are made as wide as the columns: short ones padded, long ones cut.
    assert asked["data_table"]["rows"] == [["74982", "PIYATISSA R."], ["75162", "JAYAWARDANA"], ["76921", ""]]
    [listed] = (await client.get(f"{API}/questions", headers=ws(tpl))).json()
    assert listed["data_table"]["columns"] == ["No.", "Name"] and listed["data_table"]["title"] == "Employees on this sheet"
    # No table is fine, and a table with no columns is refused.
    plain = (await client.post(f"{AGENT}/questions", headers=agent(tpl), json={"question": "Plain?", "context": "x", "answer_type": "text"})).json()
    assert plain["data_table"] is None
    assert (await client.post(f"{AGENT}/questions", headers=agent(tpl), json={
        "question": "Empty?", "context": "x", "answer_type": "text", "data_table": {"columns": [], "rows": [["a"]]}})).status_code == 422


async def test_conversations_syruss_opens_keep_its_state_until_resolved(client):
    tpl = await create_template(client)
    await client.get(f"{API}/settings", headers=ws(tpl))  # the owner
    await join(client, tpl, REVIEWER, "reviewer")
    doc = await upload(client, tpl)

    opened = (await client.post(f"{AGENT}/conversations", headers=agent(tpl), json={
        "title": "Overtime column on night rosters", "message": "Three rosters have an OT column the template can't hold.",
        "audience": "admin", "document_ids": [doc]})).json()
    again = (await client.post(f"{AGENT}/conversations", headers=agent(tpl), json={
        "title": "overtime column on night rosters", "message": "again"})).json()
    assert again["already_open"] and again["id"] == opened["id"]
    checkpoint = [{"type": "human", "data": {"content": "briefing"}}, {"type": "ai", "data": {"content": "I asked."}}]
    assert (await client.put(f"{AGENT}/conversations/{opened['id']}/state", headers=agent(tpl), json={"messages": checkpoint})).json()["saved"]
    assert (await client.get(f"{AGENT}/workspace", headers=agent(tpl))).json()["open_conversations"][0]["id"] == opened["id"]

    # It shows in the inbox of people who can settle it (admins), not reviewers.
    inbox = (await client.get(f"{API}/conversations", params={"kind": "agent"}, headers=ws(tpl))).json()
    assert [(c["title"], c["status"]) for c in inbox["items"]] == [("Overtime column on night rosters", "open")] and inbox["open"] == 1
    assert (await client.get(f"{API}/conversations", params={"kind": "agent"}, headers={**ws(tpl), **REVIEWER})).json()["items"] == []
    assert (await client.get(f"{API}/conversations/{opened['id']}", headers={**ws(tpl), **REVIEWER})).status_code == 404
    # It isn't one of your chats, and can't be renamed or deleted like one.
    assert opened["id"] not in {c["id"] for c in (await client.get(f"{API}/conversations", headers=ws(tpl))).json()["items"]}
    assert (await client.delete(f"{API}/conversations/{opened['id']}", headers=ws(tpl))).status_code == 404

    # A reply wakes Syruss on that conversation, which resumes from its checkpoint.
    conv = (await client.get(f"{API}/conversations/{opened['id']}", headers=ws(tpl))).json()
    assert conv["kind"] == "agent" and conv["messages"][0]["role"] == "assistant"
    await client.post(f"{API}/conversations/{opened['id']}/messages?wait=false", headers=ws(tpl), json={"content": "OT is overtime; capture it."})
    [event] = await outbox_events(tpl, "human.message")
    assert event.payload["conversation_kind"] == "agent" and event.payload["conversation_title"].startswith("Overtime")
    state = (await client.get(f"{AGENT}/conversations/{opened['id']}/state", headers=token_for(tpl, event.id))).json()
    assert state["state"]["messages"] == checkpoint

    # Resolving it drops the checkpoint.
    res = await client.post(f"{AGENT}/conversations/{opened['id']}/resolve", headers=token_for(tpl, event.id),
                            json={"resolution": "Proposed adding overtime hours to the template."})
    assert res.json()["status"] == "resolved"
    assert (await client.get(f"{AGENT}/conversations/{opened['id']}/state", headers=agent(tpl))).json()["state"] is None
    assert not (await client.put(f"{AGENT}/conversations/{opened['id']}/state", headers=agent(tpl), json={"messages": checkpoint})).json()["saved"]
    inbox = (await client.get(f"{API}/conversations", params={"kind": "agent"}, headers=ws(tpl))).json()
    assert inbox["items"][0]["status"] == "resolved" and inbox["open"] == 0


async def test_chat_history_is_paged_titles_only(client):
    tpl = await create_template(client)
    for i in range(23):
        await client.post(f"{API}/conversations", headers=ws(tpl), json={"title": f"Chat {i}"})
    first = (await client.get(f"{API}/conversations", params={"limit": 20}, headers=ws(tpl))).json()
    assert len(first["items"]) == 20 and first["has_more"] and first["next_offset"] == 20
    assert set(first["items"][0]) == {"id", "title", "kind", "status", "audience", "updated_at", "created_at"}
    rest = (await client.get(f"{API}/conversations", params={"offset": 20}, headers=ws(tpl))).json()
    assert len(rest["items"]) == 3 and not rest["has_more"]
    assert {c["title"] for c in first["items"] + rest["items"]} == {f"Chat {i}" for i in range(23)}


async def test_list_questions_are_answered_from_the_database_in_one_query(client):
    tpl = await create_template(client)
    shifts = {
        "2026-10-01": [("75599", "Perera", "07:00", "19:00", 12), ("61002", "Silva", "19:00", "07:00", 12)],
        "2026-10-02": [("75599", "Perera", "07:00", "19:00", 12), ("70001", "Fernando", "07:00", "19:00", 12)],
    }
    for day, guards in shifts.items():
        doc = await upload(client, tpl)
        record = {"Roster": [{"site_id": "S12", "date": day}],
                  "Guard": [{"employee_id": e, "name": n, "in_time": i, "out_time": o, "hours": h} for e, n, i, o, h in guards]}
        res = await client.post(f"{AGENT}/documents/{doc}/record", headers=agent(tpl),
                                json={"data": record, "decision": "COMMIT", "confidence": 0.95})
        assert res.status_code == 200, res.text

    query = lambda body: client.post(f"{AGENT}/records/query", headers=agent(tpl), json=body)
    # Who worked day shifts: unique people, with how often.
    day = (await query({"object": "Guard", "where": [{"field": "in_time", "op": "eq", "value": "07:00"}],
                        "distinct": ["employee_id", "name"]})).json()
    assert day["rows_matched"] == 3 and day["documents_matched"] == 2
    assert [(g["values"]["name"], g["rows"], g["documents"]) for g in day["distinct"]] == [("Perera", 2, 2), ("Fernando", 1, 1)]
    # Document-level fields filter too (the roster's date).
    later = (await query({"object": "Guard", "where": [{"field": "Roster.date", "op": "gte", "value": "2026-10-02"}],
                          "fields": ["name"]})).json()
    assert sorted(r["row"]["name"] for r in later["rows"]) == ["Fernando", "Perera"] and later["rows"][0]["document"]["Roster.date"] == "2026-10-02"
    # Nothing matched: the values the documents really use come back, so the filter can be fixed.
    none = (await query({"object": "Guard", "where": [{"field": "in_time", "op": "eq", "value": "day"}]})).json()
    assert none["rows_matched"] == 0 and set(none["values_seen"]["in_time"]) == {"07:00", "19:00"}
    # Mistakes come back with what exists.
    bad = await query({"object": "Guard", "where": [{"field": "shift", "op": "eq", "value": "D"}]})
    assert bad.status_code == 400 and "in_time" in bad.json()["detail"]


async def test_stop_cancels_the_reply_not_the_chat(client):
    tpl = await create_template(client)
    chat = (await client.post(f"{API}/conversations", headers=ws(tpl), json={"title": "t"})).json()
    await client.post(f"{API}/conversations/{chat['id']}/messages?wait=false", headers=ws(tpl), json={"content": "list everything"})
    [event] = await outbox_events(tpl, "human.message")
    run = token_for(tpl, event.id)
    assert (await client.get(f"{AGENT}/run/cancelled", headers=run)).json()["cancelled"] is False

    res = (await client.post(f"{API}/conversations/{chat['id']}/stop", headers=ws(tpl))).json()
    assert res["stopped"] == 1 and res["event_ids"] == [event.id]
    assert (await client.get(f"{AGENT}/run/cancelled", headers=run)).json()["cancelled"] is True
    messages = (await client.get(f"{API}/conversations/{chat['id']}", headers=ws(tpl))).json()["messages"]
    assert messages[-1]["role"] == "system" and messages[-1]["content"].endswith("stopped Syruss.")
    # Nothing left to stop; a finished reply isn't stopped either.
    assert (await client.post(f"{API}/conversations/{chat['id']}/stop", headers=ws(tpl))).json()["stopped"] == 0
    # Someone else's chat can't be stopped, even by another member of the workspace; an outsider is refused at the door.
    colleague = person("usr_other_stop", "Colleague")
    await join(client, tpl, colleague, "admin")
    assert (await client.post(f"{API}/conversations/{chat['id']}/stop", headers={**ws(tpl), **colleague})).status_code == 404
    outsider = person("usr_outsider_stop", "Outsider")
    await register(outsider)
    assert (await client.post(f"{API}/conversations/{chat['id']}/stop", headers={**ws(tpl), **outsider})).status_code == 403


async def test_correcting_a_saved_record_tells_syruss_in_chat(client):
    tpl = await create_template(client)
    await client.get(f"{API}/settings", headers=ws(tpl))
    doc = await upload(client, tpl, "roster-22.png")
    record = copy.deepcopy(VALID_RECORD)
    record["Guard"][0]["name"] = "pereira"
    await client.post(f"{AGENT}/documents/{doc}/record", headers=agent(tpl), json={"data": record, "decision": "COMMIT", "confidence": 0.95})
    chat = (await client.post(f"{API}/conversations", headers=ws(tpl), json={"title": "open"})).json()

    fixed = copy.deepcopy(record)
    fixed["Guard"][0]["name"] = "Pereira"  # only the case: still a correction
    correct = lambda body, headers=None: client.post(f"{API}/documents/{doc}/corrections", headers={**ws(tpl), **(headers or {})}, json=body)
    assert (await correct({"data": record})).status_code == 400  # nothing changed
    res = await correct({"data": fixed, "reasons": [{"object": "Guard", "reason": "Names start with a capital"}]},
                        {"X-Conversation-ID": chat["id"]})
    assert res.status_code == 200, res.text
    assert res.json()["conversation_id"] == chat["id"]  # the chat that was open
    [event] = await outbox_events(tpl, "human.message")
    text = event.payload["text"]
    assert "roster-22.png" in text and "Names start with a capital" in text and "“pereira” → “Pereira”" in text
    assert event.payload["context"]["kind"] == "record_correction" and event.payload["context"]["document_id"] == doc
    saved = (await client.get(f"{API}/extractions/{doc}", headers=ws(tpl))).json()
    assert saved["extracted_data"]["Guard"][0]["name"] == "Pereira" and saved["overall_confidence"] == 1.0

    # Without an open chat, a new one is started for it.
    again = copy.deepcopy(fixed)
    again["Roster"][0]["site_id"] = "S13"
    res = (await correct({"data": again})).json()
    assert res["conversation_id"] != chat["id"]
    # A record that breaks the template's rules is refused with the reason.
    broken = copy.deepcopy(fixed)
    broken["Guard"][0]["hours"] = 3
    assert (await correct({"data": broken})).status_code == 422
    # Reviewers can correct; viewers can't.
    viewer = person("usr_viewer_corr", "Vee")
    await join(client, tpl, viewer, "viewer")
    assert (await correct({"data": record}, viewer)).status_code == 403


async def test_system_lessons_seed_propose_and_need_approval(client):
    from app.services import system_lessons
    async with AsyncSessionLocal() as s:
        await system_lessons.ensure_seeded(s)
        await system_lessons.ensure_seeded(s)  # idempotent
    tpl = await create_template(client)
    lessons = (await client.get(f"{AGENT}/system-lessons", headers=agent(tpl))).json()
    assert any(l["id"] == "sys_no_guessing" for l in lessons)

    lesson = "When the platform refuses a commit for unknown people, flag one question and park the document."
    proposed = (await client.post(f"{AGENT}/system-lessons", headers=agent(tpl), json={"content": lesson, "why": "refused twice"})).json()
    assert proposed["status"] == "proposed"
    again = (await client.post(f"{AGENT}/system-lessons", headers=agent(tpl), json={"content": lesson.upper()})).json()
    assert again["already_known"] and again["times_proposed"] == 2
    # Not in any briefing until an admin approves it.
    assert lesson not in [l["content"] for l in (await client.get(f"{AGENT}/system-lessons", headers=agent(tpl))).json()]

    member = person("usr_lessons_member", "Lena Member")
    await register(member)
    listed = (await client.get(f"{API}/system-lessons", headers=member)).json()
    assert not listed["can_manage"] and all(l["status"] == "active" for l in listed["items"])
    assert (await client.patch(f"{API}/system-lessons/{proposed['id']}", headers=member, json={"status": "active"})).status_code == 403
    assert (await client.patch(f"{API}/system-lessons/{proposed['id']}", json={"status": "active"})).json()["status"] == "active"
    assert lesson in [l["content"] for l in (await client.get(f"{AGENT}/system-lessons", headers=agent(tpl))).json()]


async def test_chat_history_lists_your_chats_and_syruss_conversations(client):
    tpl = await create_template(client)
    await client.get(f"{API}/settings", headers=ws(tpl))
    mine = (await client.post(f"{API}/conversations", headers=ws(tpl), json={"title": "My chat"})).json()
    opened = (await client.post(f"{AGENT}/conversations", headers=agent(tpl), json={
        "title": "Unclear shift codes", "message": "What do D and N mean?", "audience": "admin"})).json()
    history = (await client.get(f"{API}/conversations", params={"kind": "history"}, headers=ws(tpl))).json()
    assert {(c["id"], c["kind"]) for c in history["items"]} == {(mine["id"], "chat"), (opened["id"], "agent")}
    # Someone else sees Syruss's conversation (their role allows) but not your chat.
    other = person("usr_history_other", "Other")
    await join(client, tpl, other, "admin")
    theirs = (await client.get(f"{API}/conversations", params={"kind": "history"}, headers={**ws(tpl), **other})).json()
    assert [c["id"] for c in theirs["items"]] == [opened["id"]]
