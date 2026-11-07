"""
The platform's view of agent work: runs saved while they run and marked lost when they stop reporting, facts for all
staff and content only through an audited grant, one document's whole journey, the live view, and what staff may do.
"""
import uuid
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, update
from app.core.agent_token import mint_agent_token
from app.models.audit import AuditLog
from app.models.document import Document, DocumentStatus
from app.models.extraction_log import ExtractionLogEntry
from app.models.outbox import AgentEventOutbox
from app.models.platform import PlatformAuditEntry
from app.models.trace import AgentExecutionTrace as Run
from app.services import run_watch
from app.services.live import platform_feed
from tests.conftest import TENANT, AsyncSessionLocal, create_template, outbox_events, person, register, upload, ws

API = "/api/v1"
SYS = "/api/v1/sysadmin"
AGENT = "/internal/agent/v1"
ADMIN_APP = {"X-Client-ID": "syruss-admin"}

STEP = {"step_number": 1, "thought": "The total on the page is 41.20", "tool_calls": [
    {"tool_name": "view_page", "arguments": {"page": 1}, "observation": "secret page text", "status": "success", "duration_ms": 12.5}]}


async def queued_document(client):
    """A workspace, a document sent to the agent, and a token for the event that carries it."""
    tpl = await create_template(client)
    doc = await upload(client, tpl)
    event =AgentEventOutbox(tenant_id=TENANT, template_id=tpl, event_type="document.submitted", payload={"document_id": doc},
                             actor={"type": "user", "id": "usr_admin_default", "origin": "user", "source": "dashboard"})
    async with AsyncSessionLocal() as db:
        db.add(event)
        await db.commit()
        await db.refresh(event)
    token = {"Authorization": f"Bearer {mint_agent_token(TENANT, tpl, event.id)}"}
    return tpl, doc, event.id, token


async def start(client, token, doc=None, run_id=None):
    run_id = run_id or f"run_{uuid.uuid4().hex[:10]}"
    res = await client.post(f"{AGENT}/traces/start", headers=token, json={
        "run_id": run_id, "trigger": "document.submitted", "model": "m", "document_id": doc,
        "origin": {"type": "user", "id": "usr_admin_default", "origin": "user", "source": "dashboard"}})
    assert res.status_code == 201, res.text
    return run_id, res.json()["id"]


async def test_a_run_is_saved_when_it_starts_after_each_step_and_when_it_ends(client):
    tpl, doc, event_id, token = await queued_document(client)
    run_id, trace_id = await start(client, token, doc)
    running = (await client.get(f"{SYS}/runs/running", headers=ADMIN_APP)).json()
    mine = next(r for r in running["items"] if r["id"] == trace_id)
    assert mine["status"] == "RUNNING" and mine["queued_at"] and mine["event_id"] == event_id
    assert running["workspaces"][tpl]["name"]

    res = await client.patch(f"{AGENT}/traces/{run_id}", headers=token, json={"steps": [STEP], "usage": {"input_tokens": 50}, "model_calls": 1, "cost_usd": 0.001})
    assert res.json()["status"] == "RUNNING"
    async with AsyncSessionLocal() as db:
        saved = await db.get(Run, trace_id)
        assert (saved.current_step, saved.current_tool, saved.input_tokens, len(saved.steps)) == (1, "view_page", 50, 1)

    now = datetime.now(timezone.utc)
    done = await client.post(f"{AGENT}/traces", headers=token, json={
        "trigger": "document.submitted", "model": "m", "status": "COMPLETED", "started_at": now.isoformat(),
        "completed_at": (now + timedelta(seconds=1)).isoformat(), "steps": [STEP, {**STEP, "step_number": 2}],
        "usage": {"input_tokens": 80, "output_tokens": 20}, "run_id": run_id, "document_id": doc, "model_calls": 2, "cost_usd": 0.002})
    assert done.json()["id"] == trace_id   # the same run, finished: never a second one
    async with AsyncSessionLocal() as db:
        runs = (await db.execute(select(Run).where(Run.run_id == run_id))).scalars().all()
        assert len(runs) == 1 and runs[0].status == "COMPLETED" and len(runs[0].steps) == 2 and runs[0].current_tool is None
    # A late step report doesn't reopen a finished run.
    assert (await client.patch(f"{AGENT}/traces/{run_id}", headers=token, json={"steps": [STEP]})).json()["status"] == "COMPLETED"


async def test_a_run_that_stops_reporting_is_marked_lost(client):
    tpl, doc, event_id, token = await queued_document(client)
    run_id, trace_id = await start(client, token, doc)
    async with AsyncSessionLocal() as db:
        await db.execute(update(Run).where(Run.id == trace_id).values(last_step_at=datetime.now(timezone.utc) - timedelta(hours=1)))
        await db.commit()
        assert await run_watch.mark_lost(db) >= 1
        lost = await db.get(Run, trace_id)
        await db.refresh(lost)
        assert lost.status == "LOST" and lost.completed_at and lost.total_duration_ms > 0
        entry = (await db.execute(select(ExtractionLogEntry).where(ExtractionLogEntry.document_id == doc, ExtractionLogEntry.stage == "failed"))).scalar_one()
        assert entry.event_id == event_id
        assert await run_watch.mark_lost(db) == 0  # already lost: nothing more


async def test_staff_see_facts_and_open_content_only_through_an_audited_grant(client):
    tpl, doc, event_id, token = await queued_document(client)
    run_id, trace_id = await start(client, token, doc)
    await client.patch(f"{AGENT}/traces/{run_id}", headers=token, json={"steps": [STEP]})

    facts = (await client.get(f"{SYS}/runs/{trace_id}", headers=ADMIN_APP)).json()
    assert not facts["run"]["content_open"] and facts["final_thought"] is None
    assert facts["steps"] == [{"step_number": 1, "step_started_at": None, "step_completed_at": None, "thought_chars": len(STEP["thought"]),
                               "tool_calls": [{"tool_name": "view_page", "status": "success", "duration_ms": 12.5}]}]
    assert "secret page text" not in str(facts) and "41.20" not in str(facts)
    doc_facts = (await client.get(f"{SYS}/processes/{doc}", headers=ADMIN_APP)).json()
    assert "name" not in doc_facts["document"] and doc_facts["record"] is None
    assert (await client.get(f"{SYS}/processes/{doc}/file", headers=ADMIN_APP)).json()["detail"]["code"] == "content_access_required"

    # A viewer can't open content at all.
    viewer = {**person("usr_ops_viewer", "Vera Viewer"), **ADMIN_APP}
    await register(viewer, None)
    await client.post(f"{SYS}/staff", headers=ADMIN_APP, json={"email": "usr_ops_viewer@example.com", "role": "viewer"})
    assert (await client.post(f"{SYS}/access", headers=viewer, json={"tenant_id": TENANT, "reason": "Just looking"})).status_code == 403
    assert (await client.post(f"{SYS}/access", headers=ADMIN_APP, json={"tenant_id": TENANT, "reason": "x"})).status_code == 422

    grant = (await client.post(f"{SYS}/access", headers=ADMIN_APP, json={"tenant_id": TENANT, "reason": "Ticket 42: a wrong total", "minutes": 30})).json()
    full = (await client.get(f"{SYS}/runs/{trace_id}", headers=ADMIN_APP)).json()
    assert full["run"]["content_open"] and full["steps"][0]["tool_calls"][0]["observation"] == "secret page text"
    await client.get(f"{SYS}/runs/{trace_id}", headers=ADMIN_APP)
    opened = (await client.get(f"{SYS}/processes/{doc}", headers=ADMIN_APP)).json()
    assert opened["document"]["name"] and opened["content_open"]
    file = await client.get(f"{SYS}/processes/{doc}/file", headers=ADMIN_APP)
    assert file.status_code == 200 and file.headers["content-type"] == "image/png"

    async with AsyncSessionLocal() as db:
        viewed = (await db.execute(select(PlatformAuditEntry.target_type).where(
            PlatformAuditEntry.action == "content.viewed", PlatformAuditEntry.created_at >= datetime.fromisoformat(grant["created_at"])))).scalars().all()
        assert sorted(viewed) == ["document", "run"]   # once each, however often it is opened
    activity = (await client.get(f"{API}/organization/activity")).json()["items"]
    assert activity[0]["actor"] == "Syruss support" and "Ticket 42" in activity[0]["text"]

    assert (await client.delete(f"{SYS}/access/{grant['id']}", headers=ADMIN_APP)).status_code == 204
    assert not (await client.get(f"{SYS}/runs/{trace_id}", headers=ADMIN_APP)).json()["run"]["content_open"]
    assert (await client.get(f"{SYS}/access", headers=ADMIN_APP)).json()["items"] == []


async def test_a_documents_journey_comes_in_order_with_what_each_run_changed(client):
    tpl, doc, event_id, token = await queued_document(client)
    run_id, trace_id = await start(client, token, doc)
    status = await client.post(f"{AGENT}/documents/{doc}/status", headers=token, json={"status": "PROCESSING"})
    assert status.status_code == 200, status.text
    journey = (await client.get(f"{SYS}/processes/{doc}", headers=ADMIN_APP)).json()
    stages = [i["stage"] for i in journey["timeline"]]
    assert stages.index("received") < stages.index("queued") < stages.index("run") < stages.index("reading")
    assert [r["id"] for r in journey["runs"]] == [trace_id] and journey["events"][0]["id"] == event_id
    assert journey["timings"]["queue_wait_ms"] is not None
    assert any(c["kind"] == "log" and c["what"] == "reading" and "text" not in c for c in journey["changes"])
    listed = (await client.get(f"{SYS}/processes", headers=ADMIN_APP, params={"workspace_id": tpl})).json()
    assert listed["items"][0]["id"] == doc and listed["items"][0]["last_run"]["status"] == "RUNNING"
    runs = (await client.get(f"{SYS}/runs", headers=ADMIN_APP, params={"workspace_id": tpl, "status": "RUNNING"})).json()
    assert runs["totals"]["running"] == 1 and runs["items"][0]["id"] == trace_id


def test_the_live_view_carries_facts_and_keeps_content_for_its_own_listeners():
    ops = platform_feed.listen("ops")
    one_run = platform_feed.listen("run:run_x")
    only_this = platform_feed.listen("ops", only={"run_id": "run_y"})
    try:
        base = {"tenant_id": "t", "template_id": "w", "run_id": "run_x", "channel_id": "doc1", "timestamp": "2026-11-07T00:00:00Z"}
        platform_feed.deliver({**base, "event_type": "agent_thought", "data": {"step": 2, "thought": "private reasoning"}})
        platform_feed.deliver({**base, "event_type": "tool_called", "data": {"step": 2, "tool_name": "view_page", "arguments": "{\"page\": 3}"}})
        platform_feed.deliver({**base, "event_type": "tool_result", "data": {"step": 2, "tool_name": "view_page", "result": "ERROR: no page"}})
        # Sent on another channel as well as the workspace's: one fact, from the workspace channel.
        platform_feed.deliver({**base, "channel_id": "conv1", "event_type": "agent_question", "data": {"id": "q1"}})
        platform_feed.deliver({**base, "channel_id": "template:w", "event_type": "agent_question", "data": {"id": "q1"}})
        facts = [ops.queue.get_nowait() for _ in range(ops.queue.qsize())]
        assert [f["kind"] for f in facts] == ["run.thought", "run.tool", "run.tool_done", "question.opened"]
        assert facts[2]["ok"] is False and "private reasoning" not in str(facts) and "page\\\": 3" not in str(facts)
        content = [one_run.queue.get_nowait() for _ in range(one_run.queue.qsize())]
        assert content[0]["data"]["thought"] == "private reasoning"
        assert only_this.queue.qsize() == 0

        # A slow listener loses its oldest events, and is told how many.
        for i in range(platform_feed.QUEUE_MAX + 3):
            platform_feed.deliver({**base, "event_type": "agent_thought", "data": {"step": i}})
        assert ops.queue.qsize() == platform_feed.QUEUE_MAX and ops.take_missed() == 3
    finally:
        for listener in (ops, one_run, only_this):
            platform_feed.stop(listener)
    assert not platform_feed.listening()


async def test_staff_stop_runs_retry_documents_and_resend_events_with_a_reason(client):
    tpl, doc, event_id, token = await queued_document(client)
    run_id, trace_id = await start(client, token, doc)
    assert (await client.post(f"{SYS}/runs/{trace_id}/stop", headers=ADMIN_APP, json={"reason": "no"})).status_code == 422
    assert (await client.post(f"{SYS}/runs/{trace_id}/stop", headers=ADMIN_APP, json={"reason": "Looping on one page"})).json() == {"stopping": True}
    assert (await client.get(f"{AGENT}/run/cancelled", headers=token)).json()["cancelled"] is True
    assert (await client.post(f"{SYS}/processes/{doc}/retry", headers=ADMIN_APP, json={"reason": "Read it again"})).status_code == 409  # still running

    viewer = {**person("usr_ops_viewer2", "Val Viewer"), **ADMIN_APP}
    await register(viewer, None)
    await client.post(f"{SYS}/staff", headers=ADMIN_APP, json={"email": "usr_ops_viewer2@example.com", "role": "viewer"})
    assert (await client.post(f"{SYS}/runs/{trace_id}/stop", headers=viewer, json={"reason": "Looping on one page"})).status_code == 403

    async with AsyncSessionLocal() as db:
        await db.execute(update(Run).where(Run.id == trace_id).values(status="CANCELLED"))
        await db.execute(update(Document).where(Document.id == doc).values(status=DocumentStatus.FAILED))
        from app.models.template import DocumentTemplate
        await db.execute(update(DocumentTemplate).where(DocumentTemplate.id == tpl).values(active_version_id=None))
        await db.commit()
    unpublished = await client.post(f"{SYS}/processes/{doc}/retry", headers=ADMIN_APP, json={"reason": "Read it again"})
    assert unpublished.status_code == 409   # no published template: the agent couldn't read it
    async with AsyncSessionLocal() as db:
        from app.models.template_enterprise import DocumentTemplateVersion
        version = (await db.execute(select(DocumentTemplateVersion.id).limit(1))).scalar()
        if version:
            await db.execute(update(DocumentTemplate).where(DocumentTemplate.id == tpl).values(active_version_id=version))
            await db.commit()
    if version:
        retried = (await client.post(f"{SYS}/processes/{doc}/retry", headers=ADMIN_APP, json={"reason": "Fixed the model outage"})).json()
        assert retried["status"] == "QUEUED"
        [*_, new] = await outbox_events(tpl, "document.submitted")
        assert new.id == retried["event_id"] and new.actor["name"] == "Syruss support"

    # An event the agent never picked up is sent again; one it ran is not.
    async with AsyncSessionLocal() as db:
        waiting = AgentEventOutbox(tenant_id=TENANT, template_id=tpl, event_type="schedule.reflect", payload={}, actor={},
                                   published_at=datetime.now(timezone.utc) - timedelta(hours=1), attempts=2, last_error="boom")
        db.add(waiting)
        await db.commit()
        await db.refresh(waiting)
    assert (await client.post(f"{SYS}/events/{event_id}/resend", headers=ADMIN_APP, json={"reason": "Never ran"})).status_code == 409
    assert (await client.post(f"{SYS}/events/{waiting.id}/resend", headers=ADMIN_APP, json={"reason": "Worker lost it"})).json() == {"resent": True}
    async with AsyncSessionLocal() as db:
        again = await db.get(AgentEventOutbox, waiting.id)
        assert again.published_at is None and again.attempts == 0
        told = (await db.execute(select(AuditLog.details).where(AuditLog.event_type == "PLATFORM_ACTION", AuditLog.tenant_id == TENANT))).scalars().all()
        assert any(d["reason"] == "Worker lost it" for d in told)
    actions = {e["action"] for e in (await client.get(f"{SYS}/audit", headers=ADMIN_APP)).json()["items"]}
    assert {"run.stopped", "event.resent"} <= actions


async def test_the_queues_page_answers_even_without_kafka(client):
    page = (await client.get(f"{SYS}/queues", headers=ADMIN_APP)).json()
    assert set(page) >= {"outbox", "not_picked_up", "consumers", "runs", "documents", "deliveries", "organizations", "workspaces"}
    assert page["consumers"]["status"] in ("ok", "down", "unknown")
    stats = (await client.get(f"{SYS}/runs/stats", headers=ADMIN_APP, params={"hours": 24})).json()
    assert stats["unit"] == "hour" and isinstance(stats["series"], list)
