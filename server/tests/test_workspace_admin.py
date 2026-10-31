"""
Workspace administration: settings the server enforces, roles and invitations, API keys,
notifications and webhooks.
"""
import hashlib
import hmac
import json
import httpx
from app.core.config import settings
from app.services import source_data
from tests.conftest import VALID_RECORD, agent, create_template, person, png_bytes, register, upload, ws

API = "/api/v1"
AGENT = "/internal/agent/v1"
OTHER = person("usr_other", "Nimal")


def as_other(tpl: str) -> dict:
    return {**ws(tpl), **OTHER}


async def test_settings_are_validated_and_enforced(client):
    tpl = await create_template(client)
    got = (await client.get(f"{API}/settings", headers=ws(tpl))).json()
    assert got["access"]["role"] == "owner" and got["settings"]["documents"]["commit_threshold"] == 0.8

    bad = await client.patch(f"{API}/settings", headers=ws(tpl), json={"documents": {"commit_threshold": 2}})
    assert bad.status_code == 422
    res = await client.patch(f"{API}/settings", headers=ws(tpl), json={
        "documents": {"commit_threshold": 0.95, "allowed_types": ["image/jpeg"]},
        "agent": {"instructions": "Site codes start with S."},
    })
    assert res.json()["settings"]["documents"]["commit_threshold"] == 0.95

    # Intake: PNG is no longer accepted.
    res = await client.post(f"{API}/documents/upload", headers=ws(tpl), data={"process": "false"},
                            files={"file": ("a.png", png_bytes(), "image/png")})
    assert res.status_code == 415
    await client.patch(f"{API}/settings", headers=ws(tpl), json={"documents": {"allowed_types": ["image/png"]}})
    doc = await upload(client, tpl)

    # The agent reads the policy, and a commit under the threshold is refused.
    overview = (await client.get(f"{AGENT}/workspace", headers=agent(tpl))).json()
    assert overview["workspace_policy"]["instructions"] == "Site codes start with S."
    res = await client.post(f"{AGENT}/documents/{doc}/record", headers=agent(tpl),
                            json={"data": VALID_RECORD, "decision": "COMMIT", "confidence": 0.9})
    assert res.status_code == 422 and "at least 95%" in res.json()["detail"]["problems"][0]


async def test_roles_and_invitations(client):
    tpl = await create_template(client)
    await client.get(f"{API}/settings", headers=ws(tpl))  # the creator becomes the owner
    # Not in the organization yet: refused at the first gate. In the organization, not in the workspace: the second.
    assert (await client.get(f"{API}/settings", headers=as_other(tpl))).status_code == 403
    await register(OTHER)
    refused = await client.get(f"{API}/settings", headers=as_other(tpl))
    assert refused.status_code == 403 and refused.json()["detail"]["code"] == "not_workspace_member"

    res = await client.post(f"{API}/settings/invitations", headers=ws(tpl), json={"email": OTHER["X-Email"], "role": "reviewer"})
    token = res.json()["token"]
    preview = (await client.get(f"{API}/invitations/{token}", headers=OTHER)).json()
    assert preview["role"] == "reviewer" and preview["status"] == "PENDING"
    assert (await client.post(f"{API}/invitations/{token}/accept", headers=OTHER)).status_code == 200
    assert (await client.post(f"{API}/invitations/{token}/accept", headers=OTHER)).status_code == 409

    # A reviewer reviews, but can't change settings or roles.
    assert (await client.get(f"{API}/settings", headers=as_other(tpl))).json()["access"]["role"] == "reviewer"
    res = await client.patch(f"{API}/settings", headers=as_other(tpl), json={"general": {"timezone": "Asia/Colombo"}})
    assert res.status_code == 403 and "Reviewer" in res.json()["detail"]

    members = (await client.get(f"{API}/settings/members", headers=ws(tpl))).json()
    owner = next(m for m in members if m["role"] == "owner")
    res = await client.patch(f"{API}/settings/members/{owner['id']}", headers=ws(tpl), json={"role": "admin"})
    assert res.status_code == 409  # the last owner stays an owner

    # The owner heard that someone joined.
    feed = (await client.get(f"{API}/notifications", headers=ws(tpl))).json()
    assert feed["items"][0]["type"] == "member.joined" and feed["items"][0]["link"].endswith("settings/members/people")


async def test_api_keys_act_inside_their_workspace(client):
    tpl = await create_template(client)
    res = await client.post(f"{API}/settings/api-keys", headers=ws(tpl), json={"name": "Reporting", "scope": "read"})
    key = res.json()["key"]
    assert key.startswith("sk_") and "key" not in json.dumps((await client.get(f"{API}/settings/api-keys", headers=ws(tpl))).json()[0])

    bearer = {"Authorization": f"Bearer {key}", "X-Template-ID": tpl}
    assert (await client.get(f"{API}/documents", headers=bearer)).status_code == 200
    res = await client.post(f"{API}/documents/upload", headers=bearer, data={"process": "false"}, files={"file": ("a.png", png_bytes(), "image/png")})
    assert res.status_code == 403  # read keys act as viewers

    other = await create_template(client, "Other")
    assert (await client.get(f"{API}/documents", headers={**bearer, "X-Template-ID": other})).status_code == 403

    key_id = res = (await client.get(f"{API}/settings/api-keys", headers=ws(tpl))).json()[0]["id"]
    await client.delete(f"{API}/settings/api-keys/{key_id}", headers=ws(tpl))
    assert (await client.get(f"{API}/documents", headers=bearer)).status_code == 401


async def test_notifications_follow_the_agents_work(client):
    tpl = await create_template(client)
    await client.get(f"{API}/settings", headers=ws(tpl))
    await client.delete(f"{API}/notifications", headers=ws(tpl))  # the same person ran the other tests
    await client.post(f"{AGENT}/questions", headers=agent(tpl), json={"question": "What is column W?", "context": "x"})
    await client.post(f"{AGENT}/questions", headers=agent(tpl), json={"question": "Who signs?", "context": "x"})

    feed = (await client.get(f"{API}/notifications", headers=ws(tpl))).json()
    assert feed["unread"] == 2 and feed["items"][0]["body"] == "Who signs?" and "/review?flag=" in feed["items"][0]["link"]
    first, second = feed["items"]
    await client.post(f"{API}/notifications/{first['id']}/read", headers=ws(tpl))
    assert (await client.get(f"{API}/notifications", headers=ws(tpl))).json()["unread"] == 1
    await client.delete(f"{API}/notifications?which=read", headers=ws(tpl))
    assert [n["id"] for n in (await client.get(f"{API}/notifications", headers=ws(tpl))).json()["items"]] == [second["id"]]

    # Turning a kind off stops it.
    await client.put(f"{API}/settings/notifications", headers=ws(tpl), json={"agent.question": False})
    await client.post(f"{AGENT}/questions", headers=agent(tpl), json={"question": "Third?", "context": "x"})
    await client.post(f"{API}/notifications/read-all", headers=ws(tpl))
    feed = (await client.get(f"{API}/notifications", headers=ws(tpl))).json()
    assert feed["unread"] == 0 and len(feed["items"]) == 1
    await client.delete(f"{API}/notifications", headers=ws(tpl))
    assert (await client.get(f"{API}/notifications", headers=ws(tpl))).json()["items"] == []


async def test_webhooks_are_signed(client, monkeypatch):
    received = []

    def handler(request: httpx.Request) -> httpx.Response:
        received.append(request)
        return httpx.Response(204)

    monkeypatch.setattr(source_data, "HTTP_TRANSPORT", httpx.MockTransport(handler))
    monkeypatch.setattr(settings, "KNOWLEDGE_SOURCES_ALLOW_PRIVATE_HOSTS", True)
    tpl = await create_template(client)
    res = await client.post(f"{API}/settings/webhooks", headers=ws(tpl), json={"url": "https://hooks.example.test/in", "events": ["agent.question"]})
    assert res.status_code == 201, res.text
    secret, hook = res.json()["secret"], res.json()["webhook"]
    result = (await client.post(f"{API}/settings/webhooks/{hook['id']}/test", headers=ws(tpl))).json()
    assert result["ok"] and result["status_code"] == 204

    request = received[0]
    expected = "sha256=" + hmac.new(secret.encode(), f"{request.headers['x-webhook-timestamp']}.".encode() + request.content, hashlib.sha256).hexdigest()
    assert request.headers["x-webhook-signature"] == expected and request.headers["x-webhook-event"] == "ping"
    bad = await client.post(f"{API}/settings/webhooks", headers=ws(tpl), json={"url": "https://hooks.example.test/in", "events": ["nope"]})
    assert bad.status_code == 422
