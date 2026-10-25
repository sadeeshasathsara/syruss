"""
The extraction log: written as documents move, readable by every role, changeable by none, paged so no one ever loads it
all, and pushed live on the workspace channel.
"""
import copy
from app.services import extraction_log
from app.services.event_broadcaster import broadcaster
from tests.conftest import AsyncSessionLocal, VALID_RECORD, agent, create_template, join, person, upload, ws

API = "/api/v1"
AGENT = "/internal/agent/v1"


async def entries(client, tpl, **params):
    return (await client.get(f"{API}/extraction-log", headers=ws(tpl), params=params)).json()


async def test_the_log_follows_a_document_from_upload_to_saved(client):
    tpl = await create_template(client)
    doc = await upload(client, tpl)
    await client.post(f"{API}/documents/{doc}/process", headers=ws(tpl))
    await client.post(f"{AGENT}/documents/{doc}/status", headers=agent(tpl), json={"status": "PROCESSING"})
    saved = await client.post(f"{AGENT}/documents/{doc}/record", headers=agent(tpl), json={"data": VALID_RECORD, "decision": "COMMIT", "confidence": 0.97})
    assert saved.status_code == 200, saved.text

    log = await entries(client, tpl)
    stages = [e["stage"] for e in reversed(log["items"])]
    assert stages[0] == "received" and stages[-2:] == ["reading", "saved"] and "queued" in stages, stages
    last = log["items"][0]
    assert last["stage"] == "saved" and last["level"] == "success" and last["actor"] == "Agent" and last["document_id"] == doc


async def test_trouble_is_logged_as_a_problem_and_filterable(client):
    tpl = await create_template(client)
    doc = await upload(client, tpl)
    draft = copy.deepcopy(VALID_RECORD)
    draft["Guard"][0]["name"] = "P?rera"
    await client.post(f"{AGENT}/documents/{doc}/record", headers=agent(tpl), json={
        "data": draft, "decision": "REVIEW", "confidence": 0.5, "review_reason": "Surname is smudged"})
    other = await upload(client, tpl)
    await client.post(f"{AGENT}/documents/{other}/status", headers=agent(tpl), json={"status": "FAILED", "message": "The page is blank."})

    problems = await entries(client, tpl, level="problems")
    assert {e["stage"] for e in problems["items"]} == {"review", "failed"}
    assert all(e["level"] in ("warning", "error") for e in problems["items"])
    only_one = await entries(client, tpl, document_id=other)
    assert {e["document_id"] for e in only_one["items"]} == {other}

    review = (await client.get(f"{API}/reviews", headers=ws(tpl))).json()[0]
    await client.post(f"{API}/reviews/{review['id']}/decide", headers=ws(tpl), json={"action": "APPROVE", "notes": "Looks right"})
    newest = (await entries(client, tpl))["items"][0]
    assert newest["stage"] == "decided" and "approved" in newest["message"] and "Looks right" in newest["message"]


async def test_it_is_read_a_page_at_a_time_never_all_at_once(client):
    tpl = await create_template(client)
    for n in range(7):
        await extraction_log.record(tenant_id="tenant_default", template_id=tpl, stage="received", message=f"entry {n}")
    first = await entries(client, tpl, limit=3)
    assert [e["message"] for e in first["items"]] == ["entry 6", "entry 5", "entry 4"] and first["next_before"]
    second = await entries(client, tpl, limit=3, before=first["next_before"])
    assert [e["message"] for e in second["items"]] == ["entry 3", "entry 2", "entry 1"]
    third = await entries(client, tpl, limit=3, before=second["next_before"])
    assert [e["message"] for e in third["items"]] == ["entry 0"] and third["next_before"] is None
    assert (await client.get(f"{API}/extraction-log", headers=ws(tpl), params={"limit": 1000})).status_code == 422  # a page has a ceiling


async def test_every_role_reads_it_and_nobody_can_change_it(client):
    tpl = await create_template(client)
    await extraction_log.record(tenant_id="tenant_default", template_id=tpl, stage="received", message="entry")
    viewer = person("usr_log_viewer", "Vic Viewer")
    await join(client, tpl, viewer, "viewer")
    outsider = person("usr_log_outsider", "Ola Outsider")
    from tests.conftest import register
    await register(outsider)

    mine = await client.get(f"{API}/extraction-log", headers={**ws(tpl), **viewer})
    assert mine.status_code == 200 and mine.json()["items"]
    assert (await client.get(f"{API}/extraction-log", headers={**ws(tpl), **outsider})).status_code == 403  # not in the workspace
    seq = mine.json()["items"][0]["seq"]
    for method in ("post", "put", "patch", "delete"):
        res = await getattr(client, method)(f"{API}/extraction-log/{seq}", headers={**ws(tpl), **viewer})
        assert res.status_code in (404, 405), (method, res.status_code)


async def test_new_entries_are_pushed_live_to_the_workspace_channel(client):
    tpl = await create_template(client)
    queue = broadcaster.subscribe(f"template:{tpl}", replay=False)
    await extraction_log.record(tenant_id="tenant_default", template_id=tpl, stage="reading", message="Started.", document_id="d1", document_name="a.png")
    event = queue.get_nowait()
    assert event["event_type"] == "extraction_log" and event["message"] == "Started." and event["seq"] > 0
    broadcaster.unsubscribe(f"template:{tpl}", queue)


async def test_a_problem_writing_the_log_never_breaks_the_caller():
    await extraction_log.record(tenant_id="no-such-tenant", template_id="no-such-workspace", stage="received", message="x")  # foreign keys fail: swallowed


async def test_the_overview_counts_work_without_exposing_documents(client):
    tpl = await create_template(client)
    for _ in range(3):
        doc = await upload(client, tpl)
    await client.post(f"{AGENT}/documents/{doc}/record", headers=agent(tpl), json={"data": VALID_RECORD, "decision": "COMMIT", "confidence": 0.97})
    viewer = person("usr_overview_viewer", "Vic Viewer")
    await join(client, tpl, viewer, "viewer")
    res = await client.get(f"{API}/overview", headers={**ws(tpl), **viewer}, params={"days": 7})
    assert res.status_code == 200
    data = res.json()
    assert data["totals"]["received"] == 3 and data["totals"]["saved"] == 1
    assert len(data["days"]) == 7 and data["days"][-1]["received"] == 3 and data["days"][-1]["saved"] == 1
    assert data["hands_free_rate"] == 1.0 and data["usage"]["documents_this_month"] == 3 and data["usage"]["plan"]
    assert "original_filename" not in res.text  # counts, never contents
    assert (await client.get(f"{API}/overview", headers=ws(tpl), params={"days": 1})).status_code == 422
