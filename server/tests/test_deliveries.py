"""Approved records going out: only when nothing is in doubt, signed, retried, never lost, and visible to the people who run it."""
import copy
import hashlib
import hmac
import itertools
import json
from datetime import date, datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy import select
from app.core.agent_token import mint_agent_token
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.delivery import Delivery, Destination
from app.models.notification import Notification
from app.services import delivery_service, source_data
from app.services.delivery_service import delivery_worker
from tests.conftest import TENANT, VALID_RECORD, agent, business_plan, create_template, join, person, png_bytes, upload, ws

API = "/api/v1"
AGENT = "/internal/agent/v1"
_day = itertools.count(1)


class Outside:
    """The other side of every destination: records what it was sent and answers as told."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.answer = lambda request: httpx.Response(200, json={"id": "remote-1"})

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.answer(request)

    def bodies(self):
        return [json.loads(r.content) for r in self.requests if r.content and r.headers.get("content-type", "").startswith("application/json")]


@pytest.fixture
def outside(monkeypatch):
    fake = Outside()
    monkeypatch.setattr(source_data, "HTTP_TRANSPORT", httpx.MockTransport(fake))
    monkeypatch.setattr(settings, "KNOWLEDGE_SOURCES_ALLOW_PRIVATE_HOSTS", True)
    return fake


@pytest.fixture(autouse=True)
async def clean_slate():
    """Each test starts with an empty queue, so what one leaves behind can't be sent during another."""
    async with AsyncSessionLocal() as db:
        for row in (await db.execute(select(Delivery).where(Delivery.status.in_(("held", "pending", "retrying", "sending"))))).scalars():
            row.status = "cancelled"
        await db.commit()
    delivery_service._told.clear()


async def workspace(client):
    tpl = await create_template(client)
    await business_plan(tpl)
    return tpl


async def approve(client, tpl: str, data=None, doc: str = None) -> str:
    """A document the agent read and approved."""
    doc = doc or await upload(client, tpl)
    record = data or copy.deepcopy(VALID_RECORD)
    if data is None:
        record["Roster"][0]["date"] = (date(2026, 1, 1) + timedelta(days=next(_day))).isoformat()
    res = await client.post(f"{AGENT}/documents/{doc}/record", headers=agent(tpl), json={"data": record, "decision": "COMMIT", "confidence": 0.95})
    assert res.status_code == 200, res.text
    return doc


async def webhook(client, tpl: str, url: str = "https://hooks.test/in", **extra) -> dict:
    res = await client.post(f"{API}/destinations", headers=ws(tpl), json={"kind": "webhook", "name": extra.pop("name", "Orders system"), "config": {"url": url}, **extra})
    assert res.status_code == 201, res.text
    return res.json()


async def deliveries(client, tpl: str, **params) -> list:
    return (await client.get(f"{API}/deliveries", headers=ws(tpl), params=params)).json()["items"]


def verify(request: httpx.Request, secret: str) -> bool:
    stamp = request.headers["x-syruss-timestamp"]
    expected = hmac.new(secret.encode(), f"{stamp}.".encode() + request.content, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(part.removeprefix("v1="), expected) for part in request.headers["x-syruss-signature"].split(","))


async def make_due(delivery_id: str) -> None:
    async with AsyncSessionLocal() as db:
        row = await db.get(Delivery, delivery_id)
        row.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await db.commit()


# --- the happy path -------------------------------------------------------------------------------------

async def test_an_approved_record_is_sent_signed_and_recorded(client, outside):
    tpl = await workspace(client)
    hook = await webhook(client, tpl)
    assert hook["signing_secret"].startswith("whsec_") and "secrets" not in hook

    doc = await approve(client, tpl)
    [queued] = await deliveries(client, tpl)
    assert queued["status"] == "pending" and queued["document"]["id"] == doc and queued["event"] == "document.completed"
    await delivery_worker.tick()

    [request] = outside.requests
    assert verify(request, hook["signing_secret"]) and request.headers["idempotency-key"] == f"{hook['id']}:{doc}:1"
    assert request.headers["x-syruss-event"] == "document.completed" and request.headers["x-syruss-delivery"] == queued["id"]
    body = json.loads(request.content)
    assert body["type"] == "document.completed" and body["document"]["id"] == doc and body["record"]["revision"] == 1
    assert body["record"]["data"]["Roster"][0]["site_id"] == "S12" and body["workspace"]["id"] == tpl

    [done] = await deliveries(client, tpl)
    assert done["status"] == "delivered" and done["attempts"] == 1 and done["external_ref"] == "remote-1"
    detail = (await client.get(f"{API}/deliveries/{done['id']}", headers=ws(tpl))).json()
    assert detail["attempt_log"][0]["ok"] and "Idempotency-Key" in detail["attempt_log"][0]["request"]
    listed = (await client.get(f"{API}/destinations", headers=ws(tpl))).json()[0]
    assert listed["counts"]["delivered"] == 1 and listed["health"]["label"] == "Working" and listed["last_success_at"]
    assert hook["signing_secret"] not in json.dumps(listed) and listed["secrets_set"] == ["signing_secret"]


async def test_nothing_is_sent_without_a_destination_and_a_draft_is_not_an_approval(client, outside):
    tpl = await workspace(client)
    await approve(client, tpl)
    assert await deliveries(client, tpl) == []
    await webhook(client, tpl)
    doc = await upload(client, tpl)
    res = await client.post(f"{AGENT}/documents/{doc}/record", headers=agent(tpl), json={
        "data": copy.deepcopy(VALID_RECORD), "decision": "REVIEW", "confidence": 0.5, "review_reason": "Unsure about the date"})
    assert res.status_code == 200
    assert await deliveries(client, tpl) == []
    await delivery_worker.tick()
    assert outside.requests == []


# --- the hold rule ---------------------------------------------------------------------------------------

async def test_a_document_with_a_question_or_conversation_open_is_held_until_it_is_settled(client, outside):
    tpl = await workspace(client)
    await webhook(client, tpl)
    doc = await approve(client, tpl)
    flag = (await client.post(f"{AGENT}/questions", headers=agent(tpl), json={
        "question": "Is the date on this roster right?", "context": "x", "answer_type": "text", "related_document_id": doc})).json()

    # Approved, but Syruss has a doubt about it: the delivery was due, and is held at the moment of sending.
    await delivery_worker.tick()
    assert outside.requests == []
    [held] = await deliveries(client, tpl)
    assert held["status"] == "held" and "question" in held["hold_reason"]

    # The answer alone is not enough: Syruss's own conversation about it is still open.
    assert (await client.post(f"{API}/questions/{flag['id']}/answer", headers=ws(tpl), json={"answer": "Yes, it is right"})).status_code == 200
    await delivery_worker.tick()
    assert outside.requests == [] and (await deliveries(client, tpl))[0]["hold_reason"].startswith("Syruss opened a conversation")

    async with AsyncSessionLocal() as db:
        from app.models.conversation import Conversation
        thread = (await db.execute(select(Conversation).where(Conversation.template_id == tpl, Conversation.kind == "flag"))).scalar_one()
    resolver = {"Authorization": f"Bearer {mint_agent_token(TENANT, tpl, 'evt_test')}"}
    assert (await client.post(f"{AGENT}/conversations/{thread.id}/resolve", headers=resolver, json={"resolution": "Confirmed."})).status_code == 200
    await delivery_worker.tick()   # releases it
    await delivery_worker.tick()   # sends it
    assert len(outside.requests) == 1 and (await deliveries(client, tpl))[0]["status"] == "delivered"


async def test_a_person_can_choose_to_send_a_held_document_anyway(client, outside):
    tpl = await workspace(client)
    await webhook(client, tpl)
    doc = await approve(client, tpl)
    await client.post(f"{AGENT}/conversations", headers=agent(tpl), json={"title": "Odd roster", "message": "Look at this", "audience": "admin", "document_ids": [doc]})
    await delivery_worker.tick()
    [held] = await deliveries(client, tpl)
    assert held["status"] == "held"
    res = await client.post(f"{API}/deliveries/{held['id']}/release", headers=ws(tpl))
    assert res.status_code == 200 and res.json()["status"] == "pending"
    await delivery_worker.tick()
    assert len(outside.requests) == 1


async def test_a_document_that_is_not_approved_cannot_be_released(client, outside):
    tpl = await workspace(client)
    await webhook(client, tpl)
    doc = await approve(client, tpl)
    await delivery_worker.tick()   # sent
    async with AsyncSessionLocal() as db:
        from app.models.document import Document, DocumentStatus
        (await db.get(Document, doc)).status = DocumentStatus.NEEDS_REVIEW
        await db.commit()
    other = await approve(client, tpl)
    async with AsyncSessionLocal() as db:
        (await db.get(Document, other)).status = DocumentStatus.NEEDS_REVIEW
        await db.commit()
    await delivery_worker.tick()
    held = [d for d in await deliveries(client, tpl) if d["document"]["id"] == other][0]
    assert held["status"] == "held" and "not approved" in held["hold_reason"]
    assert (await client.post(f"{API}/deliveries/{held['id']}/release", headers=ws(tpl))).status_code == 409


async def test_documents_sent_with_a_test_key_are_never_published(client, outside):
    tpl = await workspace(client)
    await webhook(client, tpl)
    chan = (await client.post(f"{API}/channels", headers=ws(tpl), json={"name": "Scanner"})).json()
    key = (await client.post(f"{API}/channels/{chan['id']}/keys", headers=ws(tpl), json={"name": "Try", "mode": "test"})).json()["secret"]
    sent = await client.post(f"{API}/intake/documents", headers={"Authorization": f"Bearer {key}"}, data={"process": "false"},
                             files={"file": ("a.png", png_bytes(), "image/png")})
    assert sent.status_code == 202
    await approve(client, tpl, doc=sent.json()["id"])
    assert await deliveries(client, tpl) == []
    await delivery_worker.tick()
    assert outside.requests == []


# --- retries, dead letters, replay --------------------------------------------------------------------------

async def test_a_failing_endpoint_is_retried_with_growing_pauses_then_given_up_on_and_can_be_replayed(client, outside, monkeypatch):
    monkeypatch.setattr(settings, "DELIVERY_MAX_ATTEMPTS", 3)
    tpl = await workspace(client)
    await webhook(client, tpl)
    outside.answer = lambda r: httpx.Response(503, text="maintenance")
    await approve(client, tpl)

    await delivery_worker.tick()
    [first] = await deliveries(client, tpl)
    assert first["status"] == "retrying" and first["attempts"] == 1 and "503" in first["last_error"]
    wait = datetime.fromisoformat(first["next_attempt_at"]) - datetime.now(timezone.utc)
    assert timedelta(seconds=50) < wait < timedelta(seconds=75)          # about a minute
    await delivery_worker.tick()
    assert len(outside.requests) == 1                                      # not due yet: left alone

    await make_due(first["id"])
    await delivery_worker.tick()
    second = (await deliveries(client, tpl))[0]
    wait = datetime.fromisoformat(second["next_attempt_at"]) - datetime.now(timezone.utc)
    assert second["attempts"] == 2 and timedelta(minutes=4) < wait < timedelta(minutes=6)   # about five minutes

    await make_due(first["id"])
    await delivery_worker.tick()
    [dead] = await deliveries(client, tpl)
    assert dead["status"] == "dead" and dead["attempts"] == 3 and dead["next_attempt_at"] is None
    log = (await client.get(f"{API}/deliveries/{dead['id']}", headers=ws(tpl))).json()["attempt_log"]
    assert [a["no"] for a in log] == [3, 2, 1] and all(not a["ok"] and a["response"] == "maintenance" for a in log)
    async with AsyncSessionLocal() as db:
        told = (await db.execute(select(Notification).where(Notification.type == "delivery.dead", Notification.template_id == tpl))).scalars().all()
    assert told                                                             # the people who manage connections were told

    outside.answer = lambda r: httpx.Response(200, json={"id": "ok"})
    replayed = await client.post(f"{API}/deliveries/{dead['id']}/replay", headers=ws(tpl))
    assert replayed.json()["status"] == "pending"
    await delivery_worker.tick()
    [again] = await deliveries(client, tpl)
    assert again["status"] == "delivered"
    assert outside.requests[-1].headers["idempotency-key"] == outside.requests[0].headers["idempotency-key"]   # the same delivery, not a new one


async def test_an_answer_that_will_never_succeed_is_not_retried_and_does_not_stop_the_destination(client, outside, monkeypatch):
    monkeypatch.setattr(settings, "DELIVERY_BREAKER_FAILURES", 2)
    tpl = await workspace(client)
    hook = await webhook(client, tpl)
    outside.answer = lambda r: httpx.Response(422, json={"error": "total is missing"})
    for _ in range(3):
        await approve(client, tpl)
    await delivery_worker.tick()
    rows = await deliveries(client, tpl)
    assert {r["status"] for r in rows} == {"dead"} and all(r["attempts"] == 1 for r in rows)     # one try each, not eight
    assert (await client.get(f"{API}/destinations/{hook['id']}", headers=ws(tpl))).json()["status"] == "active"   # bad documents don't trip the breaker


async def test_a_destination_that_keeps_failing_is_paused_and_keeps_what_is_waiting(client, outside, monkeypatch):
    monkeypatch.setattr(settings, "DELIVERY_BREAKER_FAILURES", 3)
    tpl = await workspace(client)
    hook = await webhook(client, tpl)
    outside.answer = lambda r: httpx.Response(500)
    for _ in range(4):
        await approve(client, tpl)
    await delivery_worker.tick()
    dest = (await client.get(f"{API}/destinations/{hook['id']}", headers=ws(tpl))).json()
    assert dest["status"] == "paused" and dest["paused_by"] == "system" and "3 failed" in dest["paused_reason"]
    assert dest["health"]["label"] == "Paused automatically"
    states = [r["status"] for r in await deliveries(client, tpl)]
    assert "held" in states and "delivered" not in states
    async with AsyncSessionLocal() as db:
        told = (await db.execute(select(Notification).where(Notification.type == "destination.failing", Notification.template_id == tpl))).scalars().all()
    assert told

    sent_so_far = len(outside.requests)
    await delivery_worker.tick()
    assert len(outside.requests) == sent_so_far                              # paused: nothing more goes out

    outside.answer = lambda r: httpx.Response(200, json={"id": "x"})
    assert (await client.post(f"{API}/destinations/{hook['id']}/resume", headers=ws(tpl))).json()["status"] == "active"
    for _ in range(3):
        await delivery_worker.tick()
    for row in await deliveries(client, tpl):
        if row["status"] == "retrying":
            await make_due(row["id"])
    for _ in range(3):
        await delivery_worker.tick()
    assert {r["status"] for r in await deliveries(client, tpl)} == {"delivered"}


async def test_a_delivery_left_sending_by_a_crash_is_picked_up_again(client, outside):
    tpl = await workspace(client)
    await webhook(client, tpl)
    await approve(client, tpl)
    [row] = await deliveries(client, tpl)
    async with AsyncSessionLocal() as db:
        d = await db.get(Delivery, row["id"])
        d.status, d.locked_at = "sending", datetime.now(timezone.utc) - timedelta(minutes=30)
        await db.commit()
    await delivery_worker.tick()
    await delivery_worker.tick()
    assert (await deliveries(client, tpl))[0]["status"] == "delivered" and len(outside.requests) == 1


async def test_replaying_every_dead_delivery_at_once(client, outside):
    tpl = await workspace(client)
    await webhook(client, tpl)
    outside.answer = lambda r: httpx.Response(400)
    for _ in range(3):
        await approve(client, tpl)
    await delivery_worker.tick()
    assert {r["status"] for r in await deliveries(client, tpl)} == {"dead"}
    outside.answer = lambda r: httpx.Response(200)
    assert (await client.post(f"{API}/deliveries/replay", headers=ws(tpl), json={"status": "dead"})).json() == {"replayed": 3}
    await delivery_worker.tick()
    assert {r["status"] for r in await deliveries(client, tpl)} == {"delivered"}


# --- corrections ------------------------------------------------------------------------------------------

async def test_a_correction_after_sending_goes_on_as_an_update_and_an_unchanged_approval_does_not(client, outside):
    tpl = await workspace(client)
    await webhook(client, tpl)
    doc = await approve(client, tpl)
    await delivery_worker.tick()
    first = json.loads(outside.requests[0].content)["record"]["data"]

    fixed = copy.deepcopy(first)
    fixed["Guard"][0]["name"] = "Pereira"
    res = await client.post(f"{API}/documents/{doc}/corrections", headers=ws(tpl), json={"data": fixed, "reasons": []})
    assert res.status_code == 200, res.text
    rows = await deliveries(client, tpl)
    assert sorted((r["revision"], r["event"]) for r in rows) == [(1, "document.completed"), (2, "document.corrected")]
    await delivery_worker.tick()
    update = json.loads(outside.requests[-1].content)
    assert update["type"] == "document.corrected" and update["record"]["revision"] == 2 and update["record"]["data"]["Guard"][0]["name"] == "Pereira"
    assert outside.requests[-1].headers["idempotency-key"].endswith(":2")

    again = await client.post(f"{AGENT}/documents/{doc}/record", headers=agent(tpl), json={"data": fixed, "decision": "COMMIT", "confidence": 0.9})
    assert again.status_code == 200
    assert len(await deliveries(client, tpl)) == 2                           # nothing changed: nothing new to tell them


async def test_a_destination_that_takes_no_corrections_says_so_instead_of_sending_a_duplicate(client, outside):
    tpl = await workspace(client)
    await webhook(client, tpl, policy={"send_updates": False})
    doc = await approve(client, tpl)
    await delivery_worker.tick()
    first = json.loads(outside.requests[0].content)["record"]["data"]
    fixed = copy.deepcopy(first)
    fixed["Guard"][0]["name"] = "Pereira"
    await client.post(f"{API}/documents/{doc}/corrections", headers=ws(tpl), json={"data": fixed, "reasons": []})
    second = [r for r in await deliveries(client, tpl) if r["revision"] == 2][0]
    assert second["status"] == "cancelled" and "does not take corrections" in second["last_error"]
    await delivery_worker.tick()
    assert len(outside.requests) == 1
    assert (await client.post(f"{API}/deliveries/{second['id']}/replay", headers=ws(tpl))).json()["status"] == "pending"


async def test_a_newer_approval_replaces_a_delivery_that_has_not_gone_out(client, outside):
    tpl = await workspace(client)
    await webhook(client, tpl)
    doc = await approve(client, tpl)
    record = copy.deepcopy(VALID_RECORD)
    record["Roster"][0]["date"] = "2026-09-30"
    record["Guard"][0]["name"] = "Silva"
    await approve(client, tpl, data=record, doc=doc)                         # approved again before the first was sent
    rows = await deliveries(client, tpl)
    assert sorted((r["revision"], r["status"]) for r in rows) == [(1, "cancelled"), (2, "pending")]
    await delivery_worker.tick()
    [request] = outside.requests
    body = json.loads(request.content)
    assert body["type"] == "document.completed" and body["record"]["revision"] == 2 and body["record"]["data"]["Guard"][0]["name"] == "Silva"


# --- mapping -----------------------------------------------------------------------------------------------

MAPPED = {"mode": "mapped", "fields": [
    {"target": "site", "source": "field", "value": "Roster.site_id"},
    {"target": "when.day", "source": "field", "value": "Roster.date"},
    {"target": "double_hours", "source": "formula", "value": "Guard_hours * 2"},
    {"target": "currency", "source": "constant", "value": "EUR"},
    {"target": "file", "source": "field", "value": "document.filename"},
], "lines": {"target": "guards", "from": "Guard", "fields": [
    {"target": "who", "source": "field", "value": "Guard.name"}, {"target": "shift", "source": "formula", "value": "hours * 60"}]}}


async def test_a_mapping_reshapes_the_record_and_is_checked_before_it_is_saved(client, outside):
    tpl = await workspace(client)
    res = await client.post(f"{API}/destinations", headers=ws(tpl), json={
        "kind": "rest", "name": "Payroll", "config": {"url": "https://api.test/payroll", "auth_type": "bearer"}, "secrets": {"token": "abc123"}, "mapping": MAPPED})
    assert res.status_code == 201, res.text
    doc = await approve(client, tpl)
    await delivery_worker.tick()
    [request] = outside.requests
    assert request.headers["authorization"] == "Bearer abc123" and request.url.path == "/payroll"
    data = json.loads(request.content)["record"]["data"]
    assert data["when"]["day"].startswith("2026-")
    assert {k: v for k, v in data.items() if k != "when"} == {"site": "S12", "double_hours": 24, "currency": "EUR", "file": "roster.png",
                                                              "guards": [{"who": "Perera", "shift": 720}]}

    sources = (await client.get(f"{API}/destinations/sources", headers=ws(tpl))).json()
    assert {o["type"] for o in sources["objects"]} == {"Roster", "Guard"} and any(d["id"] == doc for d in sources["documents"])
    bad = await client.post(f"{API}/destinations", headers=ws(tpl), json={
        "kind": "rest", "name": "Bad", "config": {"url": "https://api.test/x"}, "mapping": {"mode": "mapped", "fields": [
            {"target": "a", "source": "field", "value": "Roster.nope"}, {"target": "b", "source": "formula", "value": "nothing_here + 1"}]}})
    assert bad.status_code == 422 and len(bad.json()["detail"]["problems"]) == 2


async def test_a_mapping_can_be_tried_on_a_real_document_before_anything_is_sent(client, outside):
    tpl = await workspace(client)
    doc = await approve(client, tpl)
    res = await client.post(f"{API}/destinations/preview", headers=ws(tpl), json={"kind": "rest", "mapping": MAPPED, "document_id": doc})
    assert res.status_code == 200
    out = res.json()
    assert out["problems"] == [] and out["envelope"]["record"]["data"]["site"] == "S12" and out["envelope"]["record"]["mode"] == "mapped"
    assert outside.requests == []
    broken = await client.post(f"{API}/destinations/preview", headers=ws(tpl), json={
        "kind": "rest", "mapping": {"mode": "mapped", "fields": [{"target": "x", "source": "formula", "value": "1 +"}]}, "document_id": doc})
    assert broken.json()["problems"]


async def test_fixing_a_mapping_fixes_a_failed_delivery_when_it_is_replayed(client, outside):
    tpl = await workspace(client)
    dest = (await client.post(f"{API}/destinations", headers=ws(tpl), json={
        "kind": "rest", "name": "Payroll", "config": {"url": "https://api.test/in"}, "mapping": {"mode": "mapped", "fields": [{"target": "site", "source": "field", "value": "Roster.site_id"}]}})).json()
    async with AsyncSessionLocal() as db:
        row = await db.get(Destination, dest["id"])
        row.mapping = {"mode": "mapped", "fields": [{"target": "site", "source": "formula", "value": "boom(1)"}]}   # something that was valid once and no longer is
        await db.commit()
    await approve(client, tpl)
    await delivery_worker.tick()
    [dead] = await deliveries(client, tpl)
    assert dead["status"] == "dead" and "mapping could not be applied" in dead["last_error"] and outside.requests == []
    fix = await client.patch(f"{API}/destinations/{dest['id']}", headers=ws(tpl), json={"mapping": {"mode": "mapped", "fields": [{"target": "site", "source": "field", "value": "Roster.site_id"}]}})
    assert fix.status_code == 200
    await client.post(f"{API}/deliveries/{dead['id']}/replay", headers=ws(tpl))
    await delivery_worker.tick()
    assert json.loads(outside.requests[0].content)["record"]["data"] == {"site": "S12"}


# --- connecting safely ---------------------------------------------------------------------------------------

async def test_destinations_cannot_point_at_private_networks(client, monkeypatch):
    monkeypatch.setattr(settings, "KNOWLEDGE_SOURCES_ALLOW_PRIVATE_HOSTS", False)
    tpl = await workspace(client)
    for url in ("http://127.0.0.1:9/hook", "http://localhost/hook", "http://169.254.169.254/latest/meta-data", "ftp://example.com/x"):
        res = await client.post(f"{API}/destinations", headers=ws(tpl), json={"kind": "webhook", "name": "x", "config": {"url": url}})
        assert res.status_code == 422, url


async def test_redirects_are_not_followed(client, outside):
    tpl = await workspace(client)
    await webhook(client, tpl)
    outside.answer = lambda r: httpx.Response(302, headers={"location": "http://169.254.169.254/"})
    await approve(client, tpl)
    await delivery_worker.tick()
    [row] = await deliveries(client, tpl)
    assert row["status"] == "dead" and "redirect" in row["last_error"] and len(outside.requests) == 1


async def test_credentials_are_sealed_never_returned_and_kept_when_other_things_change(client, outside):
    tpl = await workspace(client)
    res = await client.post(f"{API}/destinations", headers=ws(tpl), json={
        "kind": "rest", "name": "Secure API", "config": {"url": "https://api.test/in", "auth_type": "bearer"}, "secrets": {"token": "very-secret-token"}})
    assert res.status_code == 201 and "very-secret-token" not in res.text
    dest_id = res.json()["id"]
    assert "very-secret-token" not in (await client.get(f"{API}/destinations", headers=ws(tpl))).text
    async with AsyncSessionLocal() as db:
        row = await db.get(Destination, dest_id)
        assert "very-secret-token" not in (row.secrets or "") and "very-secret-token" not in json.dumps(row.config)
    renamed = await client.patch(f"{API}/destinations/{dest_id}", headers=ws(tpl), json={"name": "Renamed", "secrets": {"token": ""}})
    assert renamed.status_code == 200 and renamed.json()["secrets_set"] == ["token"]
    await approve(client, tpl)
    await delivery_worker.tick()
    assert outside.requests[0].headers["authorization"] == "Bearer very-secret-token"
    missing = await client.post(f"{API}/destinations", headers=ws(tpl), json={"kind": "rest", "name": "No token", "config": {"url": "https://api.test/in", "auth_type": "bearer"}})
    assert missing.status_code == 422 and "Token is needed." in missing.json()["detail"]["problems"]


async def test_oauth_client_credentials_fetch_a_token_before_sending(client, outside):
    tpl = await workspace(client)

    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/token":
            assert b"grant_type=client_credentials" in request.content and b"client_secret=s3cret" in request.content
            return httpx.Response(200, json={"access_token": "tok-1", "expires_in": 3600})
        return httpx.Response(200, json={"id": "9"})

    outside.answer = answer
    await client.post(f"{API}/destinations", headers=ws(tpl), json={"kind": "rest", "name": "OAuth API", "config": {
        "url": "https://api.test/in", "auth_type": "oauth2", "token_url": "https://auth.test/oauth/token", "client_id": "me"}, "secrets": {"client_secret": "s3cret"}})
    await approve(client, tpl)
    await delivery_worker.tick()
    assert outside.requests[-1].headers["authorization"] == "Bearer tok-1"


async def test_trying_a_connection_before_saving_it(client, outside):
    tpl = await workspace(client)
    ok = await client.post(f"{API}/destinations/try", headers=ws(tpl), json={"kind": "webhook", "config": {"url": "https://hooks.test/in"}})
    assert ok.json()["ok"] is True and outside.requests[0].headers["x-syruss-test"] == "true"
    outside.answer = lambda r: httpx.Response(401, text="no")
    refused = await client.post(f"{API}/destinations/try", headers=ws(tpl), json={"kind": "webhook", "config": {"url": "https://hooks.test/in"}})
    assert refused.json()["ok"] is False and "refused the credentials" in refused.json()["message"]
    assert (await client.post(f"{API}/destinations/try", headers=ws(tpl), json={"kind": "nothing", "config": {}})).status_code == 422


async def test_rotating_a_webhook_secret_signs_with_both_during_the_overlap(client, outside):
    tpl = await workspace(client)
    hook = await webhook(client, tpl)
    new = (await client.post(f"{API}/destinations/{hook['id']}/rotate-secret", headers=ws(tpl), json={"overlap_hours": 2})).json()["signing_secret"]
    assert new != hook["signing_secret"]
    await approve(client, tpl)
    await delivery_worker.tick()
    request = outside.requests[0]
    assert request.headers["x-syruss-signature"].count("v1=") == 2
    assert verify(request, new) and verify(request, hook["signing_secret"])
    await client.post(f"{API}/destinations/{hook['id']}/rotate-secret", headers=ws(tpl), json={"overlap_hours": 0})
    await approve(client, tpl)
    await delivery_worker.tick()
    assert outside.requests[-1].headers["x-syruss-signature"].count("v1=") == 1


# --- who can see and change it -------------------------------------------------------------------------------

async def test_only_people_who_manage_integrations_see_destinations_and_deliveries(client, outside):
    tpl = await workspace(client)
    hook = await webhook(client, tpl)
    viewer = person("usr_dest_viewer", "Vi Viewer")
    await join(client, tpl, viewer, "viewer")
    for path in ("/destinations", "/deliveries", "/connections/overview", f"/destinations/{hook['id']}"):
        assert (await client.get(f"{API}{path}", headers={**viewer, **ws(tpl)})).status_code == 403, path
    other = await workspace(client)
    assert (await client.get(f"{API}/destinations/{hook['id']}", headers=ws(other))).status_code == 404
    assert (await client.patch(f"{API}/destinations/{hook['id']}", headers=ws(other), json={"name": "Mine now"})).status_code == 404
    await approve(client, tpl)
    [row] = await deliveries(client, tpl)
    assert (await client.get(f"{API}/deliveries/{row['id']}", headers=ws(other))).status_code == 404
    assert (await client.post(f"{API}/deliveries/{row['id']}/replay", headers=ws(other))).status_code == 404


async def test_destinations_are_limited_by_the_plan(client, outside):
    tpl = await create_template(client)       # free: one destination
    await webhook(client, tpl)
    res = await client.post(f"{API}/destinations", headers=ws(tpl), json={"kind": "webhook", "name": "Second", "config": {"url": "https://hooks.test/2"}})
    assert res.status_code == 402


async def test_the_overview_counts_what_came_in_what_went_out_and_what_is_held_back(client, outside):
    tpl = await workspace(client)
    await webhook(client, tpl)
    doc = await approve(client, tpl)
    await client.post(f"{AGENT}/questions", headers=agent(tpl), json={"question": "Is this site right?", "context": "x", "answer_type": "text", "related_document_id": doc})
    await delivery_worker.tick()
    other = await approve(client, tpl)
    await delivery_worker.tick()
    overview = (await client.get(f"{API}/connections/overview", headers=ws(tpl))).json()
    assert overview["publish"]["destinations"] == 1 and overview["publish"]["held"] == 1 and overview["publish"]["delivered_24h"] == 1
    assert overview["held"][0]["document"]["id"] == doc and "question" in overview["held"][0]["reason"]
    assert overview["held_reasons"][0]["count"] == 1 and overview["state"] == "ok"
    stats = (await client.get(f"{API}/deliveries/stats", headers=ws(tpl))).json()
    assert stats["by_status"]["held"] == 1 and stats["by_status"]["delivered"] == 1 and stats["delivered_24h"] == 1
    found = await deliveries(client, tpl, q="roster", status="held")
    assert [r["document"]["id"] for r in found] == [doc]
    assert await deliveries(client, tpl, q="no-such-file") == []


async def test_pausing_a_destination_holds_what_is_waiting_and_resuming_sends_it(client, outside):
    tpl = await workspace(client)
    hook = await webhook(client, tpl)
    paused = (await client.post(f"{API}/destinations/{hook['id']}/pause", headers=ws(tpl))).json()
    assert paused["status"] == "paused" and paused["paused_by"] == "user"
    await approve(client, tpl)
    await delivery_worker.tick()
    assert outside.requests == [] and (await deliveries(client, tpl))[0]["status"] == "held"
    await client.post(f"{API}/destinations/{hook['id']}/resume", headers=ws(tpl))
    await delivery_worker.tick()
    await delivery_worker.tick()
    assert len(outside.requests) == 1


async def test_deleting_a_destination_removes_its_deliveries(client, outside):
    tpl = await workspace(client)
    hook = await webhook(client, tpl)
    await approve(client, tpl)
    assert (await client.delete(f"{API}/destinations/{hook['id']}", headers=ws(tpl))).status_code == 204
    assert await deliveries(client, tpl) == []
