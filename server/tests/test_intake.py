"""Documents sent in by other systems: the key decides where they go, and nothing the sender says can change that."""
import copy
import io
import os
import time

import httpx
import pytest
from PIL import Image
from sqlalchemy import func, select
from app.core import ratelimit
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.document import Document
from app.models.intake import IntakeEvent
from app.services import settings_service
from tests.conftest import VALID_RECORD, business_plan, create_template, png_bytes, ws

API = "/api/v1"
AGENT = "/internal/agent/v1"


@pytest.fixture(autouse=True)
def fresh_limits():
    ratelimit.reset()


async def channel(client, tpl: str, name: str = "Scanner", **config) -> dict:
    res = await client.post(f"{API}/channels", headers=ws(tpl), json={"name": name, "config": config})
    assert res.status_code == 201, res.text
    return res.json()


async def key(client, tpl: str, channel_id: str, **body) -> dict:
    res = await client.post(f"{API}/channels/{channel_id}/keys", headers=ws(tpl), json={"name": "Warehouse", **body})
    assert res.status_code == 201, res.text
    return res.json()


def auth(secret: str, **extra) -> dict:
    return {"Authorization": f"Bearer {secret}", **extra}


async def send(client, secret: str, content: bytes = None, name: str = "invoice.png", ctype: str = "image/png", **form):
    headers = {k: form.pop(k) for k in list(form) if k in ("Idempotency-Key", "X-Template-ID", "X-Forwarded-For")}
    return await client.post(f"{API}/intake/documents", headers=auth(secret, **headers),
                             data={"process": "false", **form}, files={"file": (name, content if content is not None else png_bytes(), ctype)})


async def setup(client, **config):
    tpl = await create_template(client)
    await business_plan(tpl)
    ch = await channel(client, tpl, **config)
    k = await key(client, tpl, ch["id"])
    return tpl, ch, k["secret"]


async def test_the_key_alone_decides_which_workspace_a_document_belongs_to(client):
    tpl_a, ch_a, secret_a = await setup(client)
    tpl_b, ch_b, secret_b = await setup(client)

    # The sender names the other workspace in every way it can; none of it counts.
    res = await send(client, secret_a, **{"X-Template-ID": tpl_b}, template_id=tpl_b, external_id="INV-1")
    assert res.status_code == 202, res.text
    body = res.json()
    assert body["status"] == "received" and body["external_id"] == "INV-1" and body["status_url"].endswith(f"/intake/documents/{body['id']}")

    async with AsyncSessionLocal() as db:
        doc = await db.get(Document, body["id"])
    assert (doc.template_id, doc.channel_id, doc.is_test) == (tpl_a, ch_a["id"], False)
    assert doc.source["channel"] == ch_a["slug"] and doc.source["sender"] == "Warehouse"

    # The other workspace's key cannot even see that it exists.
    other = await client.get(f"{API}/intake/documents/{body['id']}", headers=auth(secret_b))
    assert other.status_code == 404
    assert (await client.get(f"{API}/intake/documents/{body['id']}", headers=auth(secret_a))).status_code == 200


async def test_an_intake_key_works_on_the_intake_endpoints_and_nowhere_else(client):
    tpl, _, secret = await setup(client)
    for path in ("/documents", "/settings", "/channels", "/settings/api-keys"):
        res = await client.get(f"{API}{path}", headers=auth(secret, **{"X-Template-ID": tpl}))
        assert res.status_code == 403 and res.json()["detail"]["code"] == "intake_key_only", path
    assert (await client.post(f"{API}/documents/upload", headers=auth(secret, **{"X-Template-ID": tpl}),
                              files={"file": ("a.png", png_bytes(), "image/png")})).status_code == 403
    assert (await client.get(f"{API}/intake/me", headers=auth(secret))).status_code == 200


async def test_other_kinds_of_key_and_unknown_keys_are_refused(client):
    tpl = await create_template(client)
    write = (await client.post(f"{API}/settings/api-keys", headers=ws(tpl), json={"name": "ci", "scope": "write"})).json()["key"]
    res = await client.get(f"{API}/intake/me", headers=auth(write))
    assert res.status_code == 403 and res.json()["detail"]["code"] == "not_an_intake_key"
    for header in ({}, auth("sk_live_nonsense"), {"Authorization": "Bearer abc"}):
        res = await client.get(f"{API}/intake/me", headers=header)
        assert res.status_code == 401 and res.headers["WWW-Authenticate"] == "Bearer", header
    # The key can also come in X-API-Key.
    _, _, secret = await setup(client)
    assert (await client.get(f"{API}/intake/me", headers={"X-API-Key": secret})).status_code == 200


async def test_a_revoked_or_expired_key_stops_working(client):
    tpl, ch, secret = await setup(client)
    created = await key(client, tpl, ch["id"], name="Short lived")
    assert (await client.get(f"{API}/intake/me", headers=auth(created["secret"]))).status_code == 200
    assert (await client.delete(f"{API}/channels/{ch['id']}/keys/{created['key']['id']}", headers=ws(tpl))).status_code == 204
    res = await client.get(f"{API}/intake/me", headers=auth(created["secret"]))
    assert res.status_code == 401
    # The workspace's other key is untouched.
    assert (await client.get(f"{API}/intake/me", headers=auth(secret))).status_code == 200


async def test_sending_the_same_request_again_returns_the_same_document(client):
    tpl, _, secret = await setup(client)
    content = png_bytes()
    first = await send(client, secret, content, **{"Idempotency-Key": "req-1"})
    again = await send(client, secret, content, **{"Idempotency-Key": "req-1"})
    assert first.status_code == 202 and again.status_code == 200
    assert again.headers["Idempotent-Replayed"] == "true" and again.json()["id"] == first.json()["id"] and again.json()["duplicate"] is True
    # The same key with another file is a mistake worth telling the sender about.
    clash = await send(client, secret, png_bytes(), **{"Idempotency-Key": "req-1"})
    assert clash.status_code == 422 and "Idempotency-Key" in clash.json()["detail"]["message"]
    async with AsyncSessionLocal() as db:
        n = (await db.execute(select(func.count(Document.id)).where(Document.template_id == tpl))).scalar()
    assert n == 1


async def test_the_senders_own_reference_is_kept_and_not_reused_for_another_file(client):
    tpl, _, secret = await setup(client)
    content = png_bytes()
    first = await send(client, secret, content, external_id="PO-77", metadata='{"branch": "North", "urgent": true}')
    assert first.status_code == 202 and first.json()["metadata"] == {"branch": "North", "urgent": True}
    again = await send(client, secret, content, external_id="PO-77", metadata='{"branch": "North", "urgent": true}')
    assert again.status_code == 200 and again.json()["id"] == first.json()["id"]
    other = await send(client, secret, png_bytes(), external_id="PO-77")
    assert other.status_code == 409 and "PO-77" in other.json()["detail"]["message"]
    found = await client.get(f"{API}/intake/documents", headers=auth(secret), params={"external_id": "PO-77"})
    assert [d["id"] for d in found.json()["items"]] == [first.json()["id"]]
    for bad in ("<script>", "x" * 300):
        assert (await send(client, secret, external_id=bad)).status_code == 422
    assert (await send(client, secret, metadata="[1, 2]")).status_code == 422
    assert (await send(client, secret, metadata='{"a": {"nested": 1}}')).status_code == 422


async def test_what_a_file_really_is_decides_whether_it_is_let_in(client):
    tpl, _, secret = await setup(client)
    # A PNG called .jpg and declared as a JPEG is stored as the PNG it is.
    res = await send(client, secret, png_bytes(), name="scan.jpg", ctype="image/jpeg")
    assert res.status_code == 202
    async with AsyncSessionLocal() as db:
        doc = await db.get(Document, res.json()["id"])
    assert doc.content_type == "image/png" and doc.minio_object_key.endswith(".png")

    exe = b"MZ\x90\x00\x03\x00\x00\x00" + os.urandom(2000)
    res = await send(client, secret, exe, name="invoice.png", ctype="image/png")
    assert res.status_code == 415 and res.json()["detail"]["code"] == "unsupported_type"
    pdf = await send(client, secret, b"%PDF-1.7\n" + os.urandom(500), name="invoice.png", ctype="image/png")
    assert pdf.status_code == 415 and "PDF" in pdf.json()["detail"]["message"]
    assert (await send(client, secret, b"", name="a.png")).status_code == 400
    broken = png_bytes()[:200]
    assert (await send(client, secret, broken)).status_code == 422


async def test_a_channel_can_narrow_what_the_workspace_accepts_but_not_widen_it(client):
    tpl, _, secret = await setup(client, allowed_types=["image/jpeg"])
    res = await send(client, secret, png_bytes())
    assert res.status_code == 415 and "JPEG" in res.json()["detail"]["message"]
    jpeg = io.BytesIO()
    Image.new("RGB", (80, 60), (3, 4, 5)).save(jpeg, "JPEG")
    assert (await send(client, secret, jpeg.getvalue(), name="a.jpg", ctype="image/jpeg")).status_code == 202
    bad = await client.post(f"{API}/channels", headers=ws(tpl), json={"name": "Odd", "config": {"allowed_types": ["application/pdf"]}})
    assert bad.status_code == 422


async def test_files_over_the_channels_size_limit_are_refused_while_being_read(client):
    tpl, _, secret = await setup(client, max_mb=1)
    noise = Image.frombytes("RGB", (1000, 1000), os.urandom(3_000_000))
    buf = io.BytesIO()
    noise.save(buf, "PNG")
    assert len(buf.getvalue()) > 1_000_000
    res = await send(client, secret, buf.getvalue())
    assert res.status_code == 413 and res.json()["detail"]["code"] == "file_too_large"


async def test_a_pictures_dimensions_are_limited_too(client, monkeypatch):
    tpl, _, secret = await setup(client)
    monkeypatch.setattr(settings, "INTAKE_MAX_PIXELS", 10_000)
    res = await send(client, secret, png_bytes(size=(400, 300)))
    assert res.status_code == 413 and "pixels" in res.json()["detail"]["message"]


async def test_a_file_name_cannot_reach_outside_its_place(client):
    tpl, _, secret = await setup(client)
    res = await send(client, secret, png_bytes(), name="../../etc/passwd.png")
    assert res.status_code == 202 and res.json()["filename"] == "passwd.png"
    res = await send(client, secret, png_bytes(), name="..\\..\\windows\\system32\\evil<>.png")
    assert res.status_code == 202 and "\\" not in res.json()["filename"] and "<" not in res.json()["filename"]
    async with AsyncSessionLocal() as db:
        keys = [d.minio_object_key for d in (await db.execute(select(Document).where(Document.template_id == tpl))).scalars()]
    assert keys and all(".." not in k and "passwd" not in k and k.startswith(f"tenants/") for k in keys)


async def test_a_key_is_limited_to_so_many_requests_a_minute_and_documents_a_day(client):
    tpl = await create_template(client)
    await business_plan(tpl)
    ch = await channel(client, tpl)
    slow = (await key(client, tpl, ch["id"], name="Chatty", rate_limit_per_minute=2))["secret"]
    assert (await client.get(f"{API}/intake/me", headers=auth(slow))).status_code == 200
    assert (await client.get(f"{API}/intake/me", headers=auth(slow))).status_code == 200
    res = await client.get(f"{API}/intake/me", headers=auth(slow))
    assert res.status_code == 429 and res.headers["Retry-After"] == "60" and res.json()["detail"]["code"] == "rate_limited"

    capped = (await key(client, tpl, ch["id"], name="Capped", daily_quota=2))["secret"]
    assert [(await send(client, capped)).status_code for _ in range(2)] == [202, 202]
    res = await send(client, capped)
    assert res.status_code == 429 and res.json()["detail"]["code"] == "quota_exceeded"


async def test_a_key_can_be_limited_to_the_senders_own_addresses(client, monkeypatch):
    tpl = await create_template(client)
    await business_plan(tpl)
    ch = await channel(client, tpl)
    locked = await key(client, tpl, ch["id"], name="Office", ip_allowlist=["10.20.0.0/16", "203.0.113.7"])
    secret = locked["secret"]
    res = await client.get(f"{API}/intake/me", headers=auth(secret))
    assert res.status_code == 403 and res.json()["detail"]["code"] == "ip_not_allowed"  # the test client is 127.0.0.1
    # A forged forwarding header means nothing unless the API is told it sits behind a proxy it controls.
    assert (await client.get(f"{API}/intake/me", headers=auth(secret, **{"X-Forwarded-For": "203.0.113.7"}))).status_code == 403
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
    assert (await client.get(f"{API}/intake/me", headers=auth(secret, **{"X-Forwarded-For": "203.0.113.7"}))).status_code == 200
    assert (await client.get(f"{API}/intake/me", headers=auth(secret, **{"X-Forwarded-For": "10.20.5.9, 1.2.3.4"}))).status_code == 200
    assert (await client.get(f"{API}/intake/me", headers=auth(secret, **{"X-Forwarded-For": "10.21.0.1"}))).status_code == 403
    bad = await client.post(f"{API}/channels/{ch['id']}/keys", headers=ws(tpl), json={"name": "x", "ip_allowlist": ["not-an-ip"]})
    assert bad.status_code == 422
    log = (await client.get(f"{API}/channels/{ch['id']}/activity", headers=ws(tpl), params={"outcome": "rejected"})).json()["items"]
    assert any("not on this key's list" in e["reason"] for e in log)


async def test_a_test_key_is_a_sandbox(client):
    tpl = await create_template(client)
    await business_plan(tpl)
    ch = await channel(client, tpl)
    created = await key(client, tpl, ch["id"], name="Try it", mode="test")
    assert created["secret"].startswith("sk_test_") and created["key"]["mode"] == "test"
    res = await send(client, created["secret"])
    assert res.status_code == 202 and res.json()["test"] is True
    assert (await client.get(f"{API}/intake/me", headers=auth(created["secret"]))).json()["mode"] == "test"
    async with AsyncSessionLocal() as db:
        assert (await db.get(Document, res.json()["id"])).is_test is True
        used = await settings_service.usage(db, tpl)
    assert used["documents_this_month"] == 0  # test documents are not counted against the plan


async def test_a_paused_channel_refuses_documents_until_it_is_turned_back_on(client):
    tpl, ch, secret = await setup(client)
    assert (await client.patch(f"{API}/channels/{ch['id']}", headers=ws(tpl), json={"status": "paused"})).json()["status"] == "paused"
    res = await send(client, secret)
    assert res.status_code == 403 and res.json()["detail"]["code"] == "channel_paused"
    await client.patch(f"{API}/channels/{ch['id']}", headers=ws(tpl), json={"status": "active"})
    assert (await send(client, secret)).status_code == 202


async def test_the_plans_document_allowance_applies_to_documents_sent_in(client):
    tpl = await create_template(client)  # free plan: 200 a month
    ch = await channel(client, tpl)
    secret = (await key(client, tpl, ch["id"]))["secret"]
    free = settings_service.PLANS["free"]
    original = free["documents_per_month"]
    free["documents_per_month"] = 1
    try:
        assert (await send(client, secret)).status_code == 202
        res = await send(client, secret)
        assert res.status_code == 402 and res.json()["detail"]["code"] == "plan_limit_reached"
    finally:
        free["documents_per_month"] = original


async def test_a_document_is_queued_for_the_agent_unless_the_sender_says_not_to(client):
    from tests.conftest import outbox_events
    tpl, _, secret = await setup(client)
    kept = await send(client, secret, process="false")
    queued = await client.post(f"{API}/intake/documents", headers=auth(secret), data={"process": "true"},
                               files={"file": ("a.png", png_bytes(), "image/png")})
    assert kept.status_code == 202 and queued.status_code == 202
    events = await outbox_events(tpl, "document.submitted")
    assert [e.payload["document_id"] for e in events] == [queued.json()["id"]]
    assert events[0].actor["type"] == "api_key" and events[0].actor["source"] == "api"


async def test_the_sender_sees_the_approved_record_and_simple_words_for_where_it_is(client):
    tpl, _, secret = await setup(client)
    sent = await client.post(f"{API}/intake/documents", headers=auth(secret), data={"process": "false"},
                             files={"file": ("a.png", png_bytes(), "image/png")})
    doc_id = sent.json()["id"]
    status = (await client.get(f"{API}/intake/documents/{doc_id}", headers=auth(secret))).json()
    assert status["status"] == "received" and "record" not in status
    from tests.conftest import agent
    committed = await client.post(f"{AGENT}/documents/{doc_id}/record", headers=agent(tpl),
                                  json={"data": copy.deepcopy(VALID_RECORD), "decision": "COMMIT", "confidence": 0.95})
    assert committed.status_code == 200, committed.text
    done = (await client.get(f"{API}/intake/documents/{doc_id}", headers=auth(secret))).json()
    assert done["status"] == "approved" and done["record"]["data"]["Roster"][0]["site_id"] == "S12" and done["record"]["confidence"] == 0.95


async def test_every_attempt_is_written_down_with_the_reason_it_was_refused(client):
    tpl, ch, secret = await setup(client)
    await send(client, secret, png_bytes(), external_id="A-1")
    await send(client, secret, b"not an image at all", name="x.png")
    log = (await client.get(f"{API}/channels/{ch['id']}/activity", headers=ws(tpl))).json()
    outcomes = [(e["outcome"], e["via"]) for e in log["items"]]
    assert ("accepted", "api") in outcomes and ("rejected", "api") in outcomes
    refused = next(e for e in log["items"] if e["outcome"] == "rejected")
    assert "image" in refused["reason"] and refused["sender"] == "Warehouse" and refused["filename"] == "x.png"
    listed = (await client.get(f"{API}/channels", headers=ws(tpl))).json()[0]
    assert listed["stats"]["accepted_24h"] == 1 and listed["stats"]["rejected_24h"] == 1 and listed["stats"]["last_received_at"]


async def test_rotating_a_key_gives_a_new_one_and_retires_the_old_one_after_the_overlap(client):
    tpl, ch, secret = await setup(client)
    old = (await client.get(f"{API}/channels", headers=ws(tpl))).json()[0]["keys"][0]
    rotated = await client.post(f"{API}/channels/{ch['id']}/keys/{old['id']}/rotate", headers=ws(tpl), json={"overlap_hours": 2})
    assert rotated.status_code == 201
    new_secret = rotated.json()["secret"]
    assert new_secret != secret and rotated.json()["old_key"]["state"] == "retiring"
    assert (await client.get(f"{API}/intake/me", headers=auth(secret))).status_code == 200      # both work during the overlap
    assert (await client.get(f"{API}/intake/me", headers=auth(new_secret))).status_code == 200
    now = rotated.json()["old_key"]
    assert now["expires_at"] is not None
    cut = await client.post(f"{API}/channels/{ch['id']}/keys/{rotated.json()['key']['id']}/rotate", headers=ws(tpl), json={"overlap_hours": 0})
    assert (await client.get(f"{API}/intake/me", headers=auth(new_secret))).status_code == 401  # no overlap: stops at once


async def test_only_people_who_manage_integrations_can_manage_channels(client):
    from tests.conftest import join, person
    tpl = await create_template(client)
    viewer = person("usr_chan_viewer", "Vi Viewer")
    await join(client, tpl, viewer, "viewer")
    for method, path in (("get", "/channels"), ("post", "/channels")):
        res = await getattr(client, method)(f"{API}{path}", headers={**viewer, **ws(tpl)}, **({"json": {"name": "x"}} if method == "post" else {}))
        assert res.status_code == 403, (method, path)


async def test_channels_are_limited_by_the_plan_and_belong_to_one_workspace(client):
    tpl = await create_template(client)  # free: one channel
    first = await channel(client, tpl, "First")
    second = await client.post(f"{API}/channels", headers=ws(tpl), json={"name": "Second"})
    assert second.status_code == 402
    other = await create_template(client, "Other")
    assert (await client.patch(f"{API}/channels/{first['id']}", headers=ws(other), json={"name": "Hijack"})).status_code == 404
    assert (await client.post(f"{API}/channels/{first['id']}/keys", headers=ws(other), json={"name": "k"})).status_code == 404
    assert (await client.delete(f"{API}/channels/{first['id']}", headers=ws(tpl))).status_code == 204
    assert (await client.get(f"{API}/channels", headers=ws(tpl))).json() == []


async def test_a_test_document_goes_through_the_whole_path_and_is_marked_as_a_test(client):
    tpl, ch, _ = await setup(client)
    res = await client.post(f"{API}/channels/{ch['id']}/test-document", headers=ws(tpl), json={})
    assert res.status_code == 201, res.text
    async with AsyncSessionLocal() as db:
        doc = await db.get(Document, res.json()["document_id"])
    assert doc.is_test and doc.channel_id == ch["id"] and doc.original_filename == "test-document.png"


# --- large files, straight to storage -----------------------------------------------------------------------

async def start_upload(client, secret: str, content: bytes, **extra):
    return await client.post(f"{API}/intake/uploads", headers=auth(secret), json={
        "filename": "big.png", "content_type": "image/png", "size_bytes": len(content), "process": False, **extra})


async def test_a_big_file_goes_straight_to_storage_and_is_checked_when_it_is_done(client):
    tpl, ch, secret = await setup(client)
    content = png_bytes()
    started = await start_upload(client, secret, content, external_id="BIG-1", metadata={"batch": 7})
    assert started.status_code == 201, started.text
    form = started.json()

    early = await client.post(form["complete_url"].replace("http://localhost:8000", ""), headers=auth(secret))
    assert early.status_code == 409 and early.json()["detail"]["code"] == "not_uploaded"

    async with httpx.AsyncClient() as storage:
        sent = await storage.post(form["url"], data=form["fields"], files={"file": ("big.png", content, "image/png")})
    assert sent.status_code in (200, 201, 204), sent.text

    done = await client.post(form["complete_url"].replace("http://localhost:8000", ""), headers=auth(secret))
    assert done.status_code == 200 and done.json()["external_id"] == "BIG-1" and done.json()["metadata"] == {"batch": 7}
    again = await client.post(form["complete_url"].replace("http://localhost:8000", ""), headers=auth(secret))
    assert again.json()["id"] == done.json()["id"] and again.headers["Idempotent-Replayed"] == "true"
    async with AsyncSessionLocal() as db:
        doc = await db.get(Document, done.json()["id"])
    assert doc.channel_id == ch["id"] and doc.template_id == tpl and doc.source["via"] == "upload"


async def test_storage_itself_refuses_a_bigger_file_than_was_announced(client):
    tpl, ch, secret = await setup(client)
    small = png_bytes(size=(40, 30))
    form = (await start_upload(client, secret, small)).json()
    async with httpx.AsyncClient() as storage:
        sent = await storage.post(form["url"], data=form["fields"], files={"file": ("big.png", png_bytes(size=(900, 900)), "image/png")})
        assert sent.status_code == 400
        wrong_name = dict(form["fields"], key=form["fields"]["key"] + "x")
        assert (await storage.post(form["url"], data=wrong_name, files={"file": ("big.png", small, "image/png")})).status_code == 403


async def test_a_big_file_is_checked_for_what_it_really_is_after_it_arrives(client):
    tpl, ch, secret = await setup(client)
    fake = b"MZ\x90\x00" + os.urandom(3000)
    form = (await start_upload(client, secret, fake)).json()
    async with httpx.AsyncClient() as storage:
        assert (await storage.post(form["url"], data=form["fields"], files={"file": ("big.png", fake, "image/png")})).status_code in (200, 201, 204)
    path = form["complete_url"].replace("http://localhost:8000", "")
    res = await client.post(path, headers=auth(secret))
    assert res.status_code == 415
    assert (await client.post(path, headers=auth(secret))).status_code == 409   # a refused upload stays refused
    from app.services.storage_service import storage_service
    assert storage_service.object_size(form["fields"]["key"]) is None            # and the file is removed


async def test_an_upload_link_is_for_its_own_channel_and_expires(client):
    tpl, ch, secret = await setup(client)
    other_secret = (await setup(client))[2]
    content = png_bytes()
    form = (await start_upload(client, secret, content)).json()
    path = form["complete_url"].replace("http://localhost:8000", "")
    assert (await client.post(path, headers=auth(other_secret))).status_code == 404
    from app.models.intake import IntakeUpload
    from datetime import datetime, timedelta, timezone
    async with AsyncSessionLocal() as db:
        row = await db.get(IntakeUpload, form["upload_id"])
        row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await db.commit()
    assert (await client.post(path, headers=auth(secret))).status_code == 410
    unsupported = await client.post(f"{API}/intake/uploads", headers=auth(secret), json={
        "filename": "a.pdf", "content_type": "application/pdf", "size_bytes": 100})
    assert unsupported.status_code == 415
    huge = await client.post(f"{API}/intake/uploads", headers=auth(secret), json={
        "filename": "a.png", "content_type": "image/png", "size_bytes": 10_000_000_000})
    assert huge.status_code == 413
