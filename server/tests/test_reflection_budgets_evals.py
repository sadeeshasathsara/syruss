"""
Phase 4: the workspace graph's identity signals, agent budgets, reflection with memory
consolidation, live quality, and shadow evaluations against golden documents.
"""
import copy
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, update
from app.core.agent_token import mint_agent_token
from app.core.database import AsyncSessionLocal
from app.models.document import Document
from app.models.extraction import ExtractionResult
from app.models.memory import Memory
from app.models.notification import Notification
from app.models.outbox import AgentEventOutbox
from app.models.template import DocumentTemplate
from app.services import budget_service, scheduler
from app.services.agent_events import AgentEventRelay
from tests.conftest import TENANT, VALID_RECORD, agent, create_template, outbox_events, upload, ws

API = "/api/v1"
AGENT = "/internal/agent/v1"

ROSTER = {
    "document_type": "site_roster",
    "objects": [
        {"type": "Site", "multiple": False, "identity": {"id_field": "site_id", "label_field": "site_name"}, "fields": [
            {"name": "site_id", "type": "string", "required": True}, {"name": "site_name", "type": "string"},
            {"name": "date", "type": "date"}]},
        {"type": "Guard", "multiple": True, "identity": {"id_field": "employee_id", "label_field": "name"}, "fields": [
            {"name": "employee_id", "type": "string", "required": True}, {"name": "name", "type": "string", "required": True}]},
    ],
}


_day = iter(range(1, 10_000))


def roster(site: str, *guards, date: str = "") -> dict:
    """A day's roster; each one is another day unless `date` is given."""
    day = date or f"2026-{1 + next(_day) // 28 % 12:02d}-{1 + next(_day) % 28:02d}"
    data = {"Site": [{"site_id": site, "site_name": f"Site {site}", "date": day}],
            "Guard": [{"employee_id": e, "name": n} for e, n in guards]}
    evidence = [{"object": "Site", "index": 0, "field": "site_name", "value": f"Site {site}", "raw_text": f"Site {site}"}] + [
        {"object": "Guard", "index": i, "field": "name", "value": n, "raw_text": n} for i, (_, n) in enumerate(guards)]
    return {"data": data, "decision": "COMMIT", "confidence": 0.95, "evidence": evidence}


async def test_the_graph_catches_a_lookalike_of_a_regular(client):
    tpl = await create_template(client, schema=ROSTER)
    for _ in range(3):
        doc = await upload(client, tpl)
        res = await client.post(f"{AGENT}/documents/{doc}/record", headers=agent(tpl),
                                json=roster("S12", ("75599", "Kamal Perera"), ("61002", "Ruwan Silva")))
        assert res.status_code == 200, res.text

    related = (await client.get(f"{AGENT}/graph/related", params={"object": "Guard", "id": "75599"}, headers=agent(tpl))).json()
    assert related["appears_with"][0] == {"type": "Site", "id": "S12", "name": "Site S12", "times": 3}
    assert related["shares_contexts_with"][0]["id"] == "61002"

    # 75590 has never been on S12, and is one digit away from a regular there: a likely misread.
    doc = await upload(client, tpl)
    draft = roster("S12", ("75590", "Nuwan Jayasena"), ("61002", "Ruwan Silva"))
    check = (await client.post(f"{AGENT}/records/validate", headers=agent(tpl), json={"data": draft["data"]})).json()
    [signal] = [s for s in check["signals"] if s["code"] == "identity_lookalike"]
    assert signal["blocking"] and signal["path"] == "Guard[0].employee_id" and "75599" in signal["message"]

    res = await client.post(f"{AGENT}/documents/{doc}/record", headers=agent(tpl), json=draft)
    assert res.status_code == 422 and signal["id"] in str(res.json()["detail"]["errors"])
    # The agent looked again and stands by what it read: it says so, and the commit goes through.
    res = await client.post(f"{AGENT}/documents/{doc}/record", headers=agent(tpl), json={
        **draft, "acknowledged_signals": [{"id": signal["id"], "reason": "ID is clearly 75590 at 3x zoom; different name"}]})
    assert res.status_code == 200
    assert res.json()["validation"]["signals"][0]["acknowledged"] is True
    quality = (await client.get(f"{API}/quality", headers=ws(tpl))).json()
    assert quality["agent"]["committed"] == 4 and quality["agent"]["acknowledged_signals"] == 1

    # Sending a record back to review takes it out of the graph.
    await client.post(f"{AGENT}/documents/{doc}/record", headers=agent(tpl), json={**draft, "decision": "REVIEW", "review_reason": "check"})
    related = (await client.get(f"{AGENT}/graph/related", params={"object": "Guard", "id": "75590"}, headers=agent(tpl))).json()
    assert related["appears_with"] == []


async def test_budgets_warn_then_pause_the_agent(client):
    tpl = await create_template(client)
    await client.get(f"{API}/settings", headers=ws(tpl))  # makes the caller the owner
    res = await client.patch(f"{API}/settings", headers=ws(tpl), json={"budget": {"monthly_tokens": 1000, "when_reached": "pause"}})
    assert res.status_code == 200, res.text

    def trace(tokens: int) -> dict:
        now = datetime.now(timezone.utc).isoformat()
        return {"trigger": "document.submitted", "model": "m", "status": "COMPLETED", "started_at": now, "completed_at": now,
                "usage": {"input_tokens": tokens, "output_tokens": 0}}

    await client.post(f"{AGENT}/traces", headers=agent(tpl), json=trace(850))
    budget = (await client.get(f"{API}/settings/budget", headers=ws(tpl))).json()
    assert budget["state"] == "warning" and not budget["paused"]
    await client.post(f"{AGENT}/traces", headers=agent(tpl), json=trace(200))
    budget = (await client.get(f"{API}/settings/budget", headers=ws(tpl))).json()
    assert budget["state"] == "reached" and budget["paused"]
    async with AsyncSessionLocal() as s:
        alerts = (await s.execute(select(Notification.title).where(
            Notification.template_id == tpl, Notification.type == "agent.budget"))).scalars().all()
    assert len(alerts) == 2  # once at 80%, once at 100%
    await client.post(f"{AGENT}/traces", headers=agent(tpl), json=trace(10))
    async with AsyncSessionLocal() as s:
        assert len((await s.execute(select(Notification).where(
            Notification.template_id == tpl, Notification.type == "agent.budget"))).scalars().all()) == 2

    # Chat gets an immediate notice; no event is queued for a reply that can't come.
    chat = (await client.post(f"{API}/conversations", headers=ws(tpl), json={"title": "t"})).json()
    await client.post(f"{API}/conversations/{chat['id']}/messages?wait=false", headers=ws(tpl), json={"content": "hi"})
    assert not [e for e in await outbox_events(tpl, "human.message") if e.payload["conversation_id"] == chat["id"]]
    messages = (await client.get(f"{API}/conversations/{chat['id']}", headers=ws(tpl))).json()["messages"]
    assert "I'm paused" in messages[-1]["content"]

    # Work events wait in the outbox, in order, while other workspaces' events flow.
    other = await create_template(client, name="Other")
    await upload(client, tpl)
    async with AsyncSessionLocal() as s:
        await s.execute(update(AgentEventOutbox).values(published_at=datetime.now(timezone.utc))
                        .where(AgentEventOutbox.template_id != tpl, AgentEventOutbox.template_id != other))
        s.add(AgentEventOutbox(tenant_id=TENANT, template_id=tpl, event_type="schedule.reflect", payload={}, actor={}))
        s.add(AgentEventOutbox(tenant_id=TENANT, template_id=other, event_type="schedule.reflect", payload={}, actor={}))
        await s.commit()
    sent = []

    class Producer:
        async def send_and_wait(self, topic, key, value):
            sent.append(key.decode())

    relay = AgentEventRelay()
    relay._producer = Producer()
    await relay.publish_pending()
    assert other in sent and tpl not in sent

    # Raising the budget lets them through.
    await client.patch(f"{API}/settings", headers=ws(tpl), json={"budget": {"monthly_tokens": 100000}})
    await relay.publish_pending()
    assert tpl in sent
    budget_service.forget(tpl)


async def test_reflection_consolidates_memory_and_wakes_the_agent(client):
    tpl = await create_template(client)
    await client.get(f"{API}/settings", headers=ws(tpl))
    for text in ("Night shifts start at 19:00.", "night shifts start at 1900"):
        await client.post(f"{AGENT}/memories", headers=agent(tpl), json={"kind": "lesson", "content": text, "importance": 0.6})
    old = (await client.post(f"{AGENT}/memories", headers=agent(tpl), json={
        "kind": "observation", "content": "A blue stamp on page two", "importance": 0.3})).json()
    async with AsyncSessionLocal() as s:
        await s.execute(update(Memory).where(Memory.id == old["id"]).values(created_at=datetime.now(timezone.utc) - timedelta(days=40)))
        await s.commit()

    res = await client.post(f"{API}/settings/reflection/run", headers=ws(tpl))
    assert res.status_code == 200, res.text
    assert res.json()["result"]["merged"] == 1 and res.json()["result"]["retired"] == 1
    [event] = await outbox_events(tpl, "schedule.reflect")
    assert event.actor["origin"] == "user" and event.payload["consolidated"]["merged"] == 1
    async with AsyncSessionLocal() as s:
        active = (await s.execute(select(Memory.content).where(Memory.template_id == tpl, Memory.archived.is_(False)))).scalars().all()
    assert len(active) == 1 and "19:00" in active[0].replace("1900", "19:00")

    state = (await client.get(f"{API}/settings/reflection", headers=ws(tpl))).json()
    assert state["cadence"] == "weekly" and state["reflect"]["last_status"] == "woke_agent"
    assert state["reflect"]["next_run_at"] is not None

    # On schedule: not due yet; then due, but nothing happened since, so the agent isn't woken.
    async with AsyncSessionLocal() as s:
        template = await s.get(DocumentTemplate, tpl)
        assert await scheduler.reflect(s, template) is None
        later = datetime.now(timezone.utc) + timedelta(days=8)
        result = await scheduler.reflect(s, template, now=later)
        await s.commit()
    assert result["status"] == "nothing_new"
    assert len(await outbox_events(tpl, "schedule.reflect")) == 1


async def test_shadow_evaluation_scores_without_changing_anything(client):
    tpl = await create_template(client)
    await client.get(f"{API}/settings", headers=ws(tpl))
    gold = await upload(client, tpl, "gold.png")
    res = await client.post(f"{AGENT}/documents/{gold}/record", headers=agent(tpl),
                            json={"data": VALID_RECORD, "decision": "COMMIT", "confidence": 0.95})
    assert res.status_code == 200
    unread = await upload(client, tpl, "unread.png")
    assert (await client.post(f"{API}/quality/golden", headers=ws(tpl), json={"document_id": unread})).status_code == 409
    assert (await client.post(f"{API}/quality/golden", headers=ws(tpl), json={"document_id": gold})).status_code == 201
    [golden] = (await client.get(f"{API}/quality/golden", headers=ws(tpl))).json()
    assert golden["filename"] == "gold.png" and golden["can_run"]

    run = (await client.post(f"{API}/quality/evals", headers=ws(tpl))).json()
    assert run["status"] == "RUNNING" and run["item_count"] == 1
    assert (await client.post(f"{API}/quality/evals", headers=ws(tpl))).status_code == 409
    [event] = await outbox_events(tpl, "eval.item")
    as_eval = {"Authorization": f"Bearer {mint_agent_token(TENANT, tpl, event.id)}"}

    # The answer is hidden; nothing that changes the workspace is allowed.
    doc = (await client.get(f"{AGENT}/documents/{gold}", headers=as_eval)).json()
    assert doc["record"] is None and doc["status"] == "QUEUED"
    assert (await client.get(f"{AGENT}/records/search", params={"q": "Perera"}, headers=as_eval)).json() == []
    res = await client.post(f"{AGENT}/memories", headers=as_eval, json={"kind": "lesson", "content": "x"})
    assert res.status_code == 403

    wrong = copy.deepcopy(VALID_RECORD)
    wrong["Guard"][0]["name"] = "Pereira"
    res = await client.post(f"{AGENT}/documents/{gold}/record", headers=as_eval,
                            json={"data": wrong, "decision": "COMMIT", "confidence": 0.95})
    assert res.status_code == 200 and res.json()["status"] == "EVALUATED"
    async with AsyncSessionLocal() as s:  # the real record is untouched
        stored = (await s.execute(select(ExtractionResult).where(ExtractionResult.document_id == gold))).scalar_one()
        assert stored.extracted_data["Guard"][0]["name"] == "Perera"
        assert (await s.get(Document, gold)).status.value == "COMPLETED"

    report = (await client.get(f"{API}/quality/evals/{run['id']}", headers=ws(tpl))).json()
    assert report["status"] == "COMPLETED"
    m = report["metrics"]
    assert m["answered"] == 1 and m["exact_match_rate"] == 0 and m["confidently_wrong"] == 1
    assert m["most_missed_fields"] == [{"field": "Guard.name", "documents": 1}]
    assert report["items"][0]["scores"]["wrong_fields"] == ["Guard[0].name"]
    async with AsyncSessionLocal() as s:
        note = (await s.execute(select(Notification).where(
            Notification.template_id == tpl, Notification.type == "agent.evaluation"))).scalars().first()
        assert note and "1 would have been committed wrong" in note.body


async def test_an_evaluation_closes_unanswered_documents(client):
    tpl = await create_template(client)
    await client.get(f"{API}/settings", headers=ws(tpl))
    docs = [await upload(client, tpl, f"g{i}.png") for i in range(2)]
    for i, d in enumerate(docs):
        record = copy.deepcopy(VALID_RECORD)
        record["Roster"][0]["date"] = f"2026-07-2{i}"  # two different days, not the same roster twice
        res = await client.post(f"{AGENT}/documents/{d}/record", headers=agent(tpl), json={"data": record, "decision": "COMMIT", "confidence": 0.95})
        assert res.status_code == 200, res.text
        await client.post(f"{API}/quality/golden", headers=ws(tpl), json={"document_id": d})
    run = (await client.post(f"{API}/quality/evals", headers=ws(tpl))).json()
    first, second = await outbox_events(tpl, "eval.item")
    token = lambda e: {"Authorization": f"Bearer {mint_agent_token(TENANT, tpl, e.id)}"}
    await client.post(f"{AGENT}/documents/{first.payload['document_id']}/status", headers=token(first),
                      json={"status": "FAILED", "message": "unreadable"})
    assert (await client.get(f"{API}/quality/evals/{run['id']}", headers=ws(tpl))).json()["status"] == "RUNNING"
    await client.post(f"{AGENT}/evals/outcome", headers=token(second), json={"note": "ran out of steps"})
    report = (await client.get(f"{API}/quality/evals/{run['id']}", headers=ws(tpl))).json()
    assert report["status"] == "COMPLETED"
    assert report["metrics"]["failed"] == 1 and report["metrics"]["no_answer"] == 1 and report["metrics"]["answered"] == 0
    async with AsyncSessionLocal() as s:  # failing in an evaluation never fails the real document
        assert {(await s.get(Document, d)).status.value for d in docs} == {"COMPLETED"}


async def test_the_graph_runs_on_apache_age_where_installed(client):
    """Same answers from Cypher as from SQL, kept in step as records are committed."""
    import pytest
    from sqlalchemy import text
    from app.services import graph_service
    async with AsyncSessionLocal() as s:
        available = (await s.execute(text("SELECT 1 FROM pg_available_extensions WHERE name = 'age'"))).scalar()
        preloaded = "age" in (await s.execute(text("SELECT current_setting('shared_preload_libraries')"))).scalar()
        if not (available and preloaded):
            pytest.skip("Apache AGE isn't installed in this database")
        await s.execute(text("CREATE EXTENSION IF NOT EXISTS age"))
        await s.commit()
    graph_service._age_available = None

    tpl = await create_template(client, schema=ROSTER)
    for _ in range(3):
        doc = await upload(client, tpl)
        await client.post(f"{AGENT}/documents/{doc}/record", headers=agent(tpl),
                          json=roster("S12", ("75599", "Kamal O'Brien $$ x"), ("61002", "Ruwan Silva")))
    async with AsyncSessionLocal() as s:
        sql_answer = await graph_service.appears_with(s, tpl, "Guard:75599")
        state = await scheduler.sync_graph(s, await s.get(DocumentTemplate, tpl))
        await s.commit()
    assert state["age_synced"] is True and state["edges"] == 2  # each guard with the site
    async with AsyncSessionLocal() as s:
        assert await graph_service.backend(s, tpl) == "age"
        cypher_answer = await graph_service.appears_with(s, tpl, "Guard:75599")
        assert [(n.key, n.label, n.count) for n in cypher_answer] == [(n.key, n.label, n.count) for n in sql_answer]
        assert cypher_answer[0].label == "Site S12" and cypher_answer[0].count == 3
        assert [p.key for p in await graph_service.peers(s, tpl, "Guard:75599")] == ["Guard:61002"]

    # A new commit updates the graph in place; the lookalike check now reads it through Cypher.
    doc = await upload(client, tpl)
    await client.post(f"{AGENT}/documents/{doc}/record", headers=agent(tpl), json=roster("S12", ("75599", "Kamal O'Brien $$ x")))
    async with AsyncSessionLocal() as s:
        [site] = await graph_service.appears_with(s, tpl, "Guard:75599", of_type="Site")
        assert site.count == 4
    draft = roster("S12", ("75590", "Nuwan Jayasena"))
    check = (await client.post(f"{AGENT}/records/validate", headers=agent(tpl), json={"data": draft["data"]})).json()
    assert [s["code"] for s in check["signals"]] == ["identity_lookalike"]
    related = (await client.get(f"{AGENT}/graph/related", params={"object": "Guard", "id": "75599"}, headers=agent(tpl))).json()
    assert related["graph"] == "age"
