"""
The second gate and what a role allows: system and custom roles, privilege escalation, an organization
admin's limits, private chats, live channels, and the agent acting for a person.
"""
import pytest
from app.core.agent_token import mint_agent_token
from app.core.auth import auth_for
from app.services import channels
from app.services.event_broadcaster import broadcaster
from app.services.kafka_ws_bridge import kafka_ws_bridge
from app.services.ui_events import publish_ui_event
from tests.conftest import (AsyncSessionLocal, TENANT, create_template, join, outbox_events, person, png_bytes, register, upload, ws)

API = "/api/v1"
AGENT = "/internal/agent/v1"


def as_(tpl: str, who: dict) -> dict:
    return {**ws(tpl), **who}


async def slug_of(client, tpl: str) -> str:
    return next(w["slug"] for w in (await client.get(f"{API}/workspaces")).json() if w["id"] == tpl)


# --- roles ----------------------------------------------------------------------------

async def test_a_custom_role_grants_exactly_its_ticked_privileges(client):
    tpl = await create_template(client)
    role = (await client.post(f"{API}/settings/roles", headers=ws(tpl), json={
        "name": "Intake clerk", "description": "Scans and uploads the daily rosters.", "permissions": ["documents.upload"]})).json()
    assert role["permissions"] == ["documents.upload", "documents.view"]  # doing it includes seeing it
    assert role["description"].startswith("Scans")

    clerk = person("usr_clerk", "Cleo Clerk")
    await join(client, tpl, clerk, role["id"])
    seen = (await client.get(f"{API}/settings", headers=as_(tpl, clerk))).json()["access"]
    assert seen["role_label"] == "Intake clerk" and seen["permissions"] == ["documents.upload", "documents.view"]

    up = await client.post(f"{API}/documents/upload", headers=as_(tpl, clerk), data={"process": "false"},
                           files={"file": ("a.png", png_bytes(), "image/png")})
    assert up.status_code == 201
    assert (await client.get(f"{API}/documents", headers=as_(tpl, clerk))).status_code == 200
    # Everything it wasn't ticked for is refused.
    for path in ("/knowledge/entities", "/settings/members", "/conversations", "/settings/api-keys", "/audit"):
        res = await client.get(f"{API}{path}", headers=as_(tpl, clerk))
        assert res.status_code == 403, path
    assert (await client.patch(f"{API}/settings", headers=as_(tpl, clerk), json={"general": {}})).status_code == 403


async def test_nobody_can_grant_privileges_they_lack(client):
    tpl = await create_template(client)
    admin, editor = person("usr_esc_admin", "Ada Admin"), person("usr_esc_editor", "Ed Editor")
    await join(client, tpl, admin, "admin")
    await join(client, tpl, editor, "editor")

    # An editor has no roles.manage; an admin lacks workspace.delete and billing.manage, so can't hand them out.
    assert (await client.post(f"{API}/settings/roles", headers=as_(tpl, editor), json={"name": "X", "permissions": []})).status_code == 403
    res = await client.post(f"{API}/settings/roles", headers=as_(tpl, admin), json={"name": "Deleter", "permissions": ["workspace.delete"]})
    assert res.status_code == 403
    assert (await client.post(f"{API}/settings/roles", headers=as_(tpl, admin), json={"name": "Typo", "permissions": ["root.everything"]})).status_code == 422
    # Nor can an admin give someone the owner role, or invite one.
    members = (await client.get(f"{API}/settings/members", headers=ws(tpl))).json()
    editor_row = next(m for m in members if m["user_id"] == "usr_esc_editor")
    assert (await client.patch(f"{API}/settings/members/{editor_row['id']}", headers=as_(tpl, admin), json={"role": "owner"})).status_code == 403
    assert (await client.post(f"{API}/settings/invitations", headers=as_(tpl, admin), json={"email": "o@example.com", "role": "owner"})).status_code == 403
    # An admin may create a role up to their own privileges, and an editor can't rewrite it.
    made = await client.post(f"{API}/settings/roles", headers=as_(tpl, admin), json={"name": "Team lead", "permissions": ["members.manage", "documents.review"]})
    assert made.status_code == 201 and "members.view" in made.json()["permissions"]
    assert (await client.patch(f"{API}/settings/roles/{made.json()['id']}", headers=as_(tpl, editor), json={"name": "Hijacked"})).status_code == 403


async def test_role_rules_names_system_roles_and_deleting(client):
    tpl = await create_template(client)
    role = (await client.post(f"{API}/settings/roles", headers=ws(tpl), json={"name": "Auditor", "permissions": ["audit.view"]})).json()
    assert (await client.post(f"{API}/settings/roles", headers=ws(tpl), json={"name": "auditor", "permissions": []})).status_code == 409
    assert (await client.post(f"{API}/settings/roles", headers=ws(tpl), json={"name": "Viewer", "permissions": []})).status_code == 409  # a built-in's name
    assert (await client.patch(f"{API}/settings/roles/viewer", headers=ws(tpl), json={"name": "Seer"})).status_code == 409
    assert (await client.delete(f"{API}/settings/roles/owner", headers=ws(tpl))).status_code == 409

    listing = (await client.get(f"{API}/settings/roles", headers=ws(tpl))).json()
    assert [r["id"] for r in listing["roles"][:5]] == ["owner", "admin", "editor", "reviewer", "viewer"] and role["id"] in [r["id"] for r in listing["roles"]]
    assert any(p["key"] == "documents.review" for g in listing["groups"] for p in g["permissions"])

    person_one = person("usr_auditor", "Una Auditor")
    await join(client, tpl, person_one, role["id"])
    assert (await client.delete(f"{API}/settings/roles/{role['id']}", headers=ws(tpl))).status_code == 409  # still in use
    row = next(m for m in (await client.get(f"{API}/settings/members", headers=ws(tpl))).json() if m["user_id"] == "usr_auditor")
    assert (await client.patch(f"{API}/settings/members/{row['id']}", headers=ws(tpl), json={"role": "viewer"})).status_code == 200
    assert (await client.delete(f"{API}/settings/roles/{role['id']}", headers=ws(tpl))).status_code == 204
    # A role belongs to its workspace: another workspace can't use it.
    other = await create_template(client, "Other")
    mine = (await client.post(f"{API}/settings/roles", headers=ws(tpl), json={"name": "Local", "permissions": []})).json()
    res = await client.post(f"{API}/settings/invitations", headers=ws(other), json={"email": "z@example.com", "role": mine["id"]})
    assert res.status_code == 422


async def test_role_changes_take_effect_on_the_next_request(client):
    tpl = await create_template(client)
    p = person("usr_live_role", "Lee Live")
    await join(client, tpl, p, "editor")
    assert (await client.get(f"{API}/knowledge/entities", headers=as_(tpl, p))).status_code == 200
    row = next(m for m in (await client.get(f"{API}/settings/members", headers=ws(tpl))).json() if m["user_id"] == "usr_live_role")
    await client.patch(f"{API}/settings/members/{row['id']}", headers=ws(tpl), json={"role": "viewer"})
    assert (await client.get(f"{API}/knowledge/entities", headers=as_(tpl, p))).status_code == 200  # viewers still see knowledge
    assert (await client.post(f"{API}/documents/upload", headers=as_(tpl, p), data={"process": "false"},
                              files={"file": ("a.png", png_bytes(), "image/png")})).status_code == 403
    await client.delete(f"{API}/settings/members/{row['id']}", headers=ws(tpl))
    assert (await client.get(f"{API}/knowledge/entities", headers=as_(tpl, p))).status_code == 403


# --- organization admins manage, they don't read -----------------------------------------

async def test_an_org_admin_cannot_open_a_workspace_until_they_join_it_on_the_record(client):
    tpl = await create_template(client, "Closed books")
    slug = await slug_of(client, tpl)
    boss = person("usr_org_admin_1", "Olga Admin")
    await register(boss, "admin")

    # They see the workspace exists (to manage it) but its data is closed.
    listed = (await client.get(f"{API}/workspaces", headers=boss)).json()
    assert next(w for w in listed if w["id"] == tpl)["my_role"] is None
    refused = await client.get(f"{API}/documents", headers=as_(tpl, boss))
    assert refused.status_code == 403 and refused.json()["detail"]["code"] == "not_workspace_member"
    assert (await client.get(f"{API}/workspaces/{slug}", headers=boss)).status_code == 200

    # Joining is a deliberate act that is recorded; ordinary members can't do it.
    plain = person("usr_org_plain", "Pat Plain")
    await register(plain, "member")
    assert (await client.post(f"{API}/workspaces/{slug}/join", headers=plain, json={"role": "admin"})).status_code == 403
    assert (await client.post(f"{API}/workspaces/{slug}/join", headers=boss, json={"role": "owner"})).status_code == 403  # owners only
    assert (await client.post(f"{API}/workspaces/{slug}/join", headers=boss, json={"role": "admin"})).status_code == 200
    assert (await client.get(f"{API}/documents", headers=as_(tpl, boss))).status_code == 200
    audit = (await client.get(f"{API}/audit", headers=ws(tpl), params={"event_type": "ORG_ADMIN_JOINED_WORKSPACE"})).json()
    assert audit and audit[0]["actor"] == "usr_org_admin_1"
    # Plain members can't create workspaces; admins can.
    assert (await client.post(f"{API}/workspaces", headers=plain, json={"name": "Nope"})).status_code == 403
    assert (await client.post(f"{API}/workspaces", headers=boss, json={"name": "Admin made"})).status_code == 201


# --- chats are private -----------------------------------------------------------------------

async def test_nobody_reads_someone_elses_chat_not_even_the_owner(client):
    tpl = await create_template(client)
    sam, owner = person("usr_chat_sam", "Sam Chat"), {}
    await join(client, tpl, sam, "viewer")
    chat = (await client.post(f"{API}/conversations", headers=as_(tpl, sam), json={"title": "Private"})).json()
    await client.post(f"{API}/conversations/{chat['id']}/messages?wait=false", headers=as_(tpl, sam), json={"content": "my salary question"})

    # The workspace owner (and an admin) get nothing, by id or in lists.
    admin = person("usr_chat_admin", "Ann Admin")
    await join(client, tpl, admin, "admin")
    for who in ({}, admin):
        h = as_(tpl, who)
        assert (await client.get(f"{API}/conversations/{chat['id']}", headers=h)).status_code == 404
        assert (await client.post(f"{API}/conversations/{chat['id']}/stop", headers=h)).status_code == 404
        assert (await client.delete(f"{API}/conversations/{chat['id']}", headers=h)).status_code == 404
        listed = (await client.get(f"{API}/conversations", headers=h, params={"kind": "history"})).json()["items"]
        assert chat["id"] not in [c["id"] for c in listed]
    # ...and their live channel is theirs alone.
    assert (await client.post(f"{API}/ws/ticket", headers=as_(tpl, admin), json={"channel": chat["id"]})).status_code == 403
    assert (await client.post(f"{API}/ws/ticket", headers=as_(tpl, {}), json={"channel": chat["id"]})).status_code == 403
    mine = await client.post(f"{API}/ws/ticket", headers=as_(tpl, sam), json={"channel": chat["id"]})
    assert mine.status_code == 200
    # The shared workspace channel does carry the work but never a private chat's telemetry.
    shared = broadcaster.subscribe(f"template:{tpl}", replay=False)
    own = broadcaster.subscribe(chat["id"], replay=False)
    await kafka_ws_bridge.deliver({"event_type": "agent_thought", "channel_id": chat["id"], "template_id": tpl, "thought": "salary", "private_to": "usr_chat_sam"})
    await kafka_ws_bridge.deliver({"event_type": "document_status", "channel_id": "doc1", "template_id": tpl})
    assert own.get_nowait()["thought"] == "salary"
    assert shared.get_nowait()["event_type"] == "document_status" and shared.empty()
    await publish_ui_event(chat["id"], "agent_message", {"content": "secret answer"}, tenant_id=TENANT, template_id=tpl, private_to="usr_chat_sam")
    assert shared.empty() and own.get_nowait()["content"] == "secret answer"


async def test_tickets_work_once_for_their_channel_and_membership_decides(client):
    tpl = await create_template(client)
    p = person("usr_ws_member", "Wes Member")
    await join(client, tpl, p, "viewer")
    outsider = person("usr_ws_outsider", "Oz Outsider")
    await register(outsider)

    res = await client.post(f"{API}/ws/ticket", headers=as_(tpl, p), json={"channel": f"template:{tpl}"})
    ticket = res.json()["ticket"]
    async with AsyncSessionLocal() as db:
        assert await channels.redeem_ticket(db, ticket, "template:somewhere-else") is None  # bound to its channel
    async with AsyncSessionLocal() as db:
        assert await channels.redeem_ticket(db, ticket, f"template:{tpl}") is not None
    async with AsyncSessionLocal() as db:
        assert await channels.redeem_ticket(db, ticket, f"template:{tpl}") is None  # single use
    assert (await client.post(f"{API}/ws/ticket", headers=as_(tpl, outsider), json={"channel": f"template:{tpl}"})).status_code == 403
    assert (await client.post(f"{API}/ws/ticket", headers=as_(tpl, p), json={"channel": "usr_someone_else"})).status_code == 403
    assert (await client.post(f"{API}/ws/ticket", headers=as_(tpl, p), json={"channel": "user:usr_admin_default"})).status_code == 403
    assert (await client.post(f"{API}/ws/ticket", headers=as_(tpl, p), json={"channel": "notifications"})).json()["channel"] == "user:usr_ws_member"
    # A document's channel needs the right to see documents; losing membership closes it.
    doc = await upload(client, tpl)
    assert (await client.post(f"{API}/ws/ticket", headers=as_(tpl, p), json={"channel": doc})).status_code == 200
    auth = await auth_for("usr_ws_member", TENANT)
    row = next(m for m in (await client.get(f"{API}/settings/members", headers=ws(tpl))).json() if m["user_id"] == "usr_ws_member")
    await client.delete(f"{API}/settings/members/{row['id']}", headers=ws(tpl))
    async with AsyncSessionLocal() as db:
        assert not await channels.can_listen(db, auth, doc)


async def test_the_live_stream_is_refused_to_anyone_who_could_not_get_a_ticket(client):
    tpl = await create_template(client)
    p = person("usr_live_member", "Liv Member")
    await join(client, tpl, p, "viewer")
    outsider = person("usr_live_outsider", "Ola Outsider")
    await register(outsider)
    assert (await client.get(f"{API}/live/template:{tpl}", headers=as_(tpl, outsider))).status_code == 403
    assert (await client.get(f"{API}/live/usr_someone_else", headers=as_(tpl, p))).status_code == 403
    assert (await client.get(f"{API}/live/user:usr_admin_default", headers=as_(tpl, p))).status_code == 403


async def test_the_live_stream_sends_what_its_channel_carries_and_stops_when_the_listener_leaves(client):
    from app.api.v1.live import _events

    class Listener:
        def __init__(self):
            self.gone = False

        async def is_disconnected(self):
            return self.gone

    listener = Listener()
    stream = _events(listener, "template:abc", "usr_x", TENANT, replay=False)
    assert '"connected"' in await anext(stream)
    await broadcaster.broadcast_to_channel("template:abc", {"event_type": "document_status", "tenant_id": TENANT})
    assert '"document_status"' in await anext(stream)
    await stream.aclose()  # a closed tab; nothing stays subscribed
    assert "template:abc" not in broadcaster._subscribers


# --- the agent acts for the person it is chatting with ----------------------------------------

async def chat_token(client, tpl: str, who: dict) -> tuple:
    chat = (await client.post(f"{API}/conversations", headers=as_(tpl, who), json={"title": "t"})).json()
    await client.post(f"{API}/conversations/{chat['id']}/messages?wait=false", headers=as_(tpl, who), json={"content": "hello"})
    event = [e for e in await outbox_events(tpl, "human.message") if e.payload["conversation_id"] == chat["id"]][0]
    return chat, {"Authorization": f"Bearer {mint_agent_token(TENANT, tpl, event.id, actor_id=event.actor['id'])}"}


async def test_in_a_private_chat_the_agent_is_bounded_by_the_person(client):
    tpl = await create_template(client)
    doc = await upload(client, tpl)
    viewer = person("usr_agent_viewer", "Vic Viewer")
    await join(client, tpl, viewer, "viewer")
    chat, token = await chat_token(client, tpl, viewer)

    assert (await client.get(f"{AGENT}/documents", headers=token)).status_code == 200  # a viewer may see documents
    refused = await client.post(f"{AGENT}/documents/{doc}/record", headers=token, json={"data": {}, "decision": "COMMIT", "confidence": 0.9})
    assert refused.status_code == 403 and "Viewer" in refused.json()["detail"]
    assert (await client.post(f"{AGENT}/entities", headers=token, json={"entity_type": "Person", "name": "X"})).status_code == 403
    assert (await client.get(f"{AGENT}/conversations/{chat['id']}/messages", headers=token)).status_code == 200
    # Someone else's conversation is off limits for this run, whoever asks.
    other = (await client.post(f"{API}/conversations", headers=ws(tpl), json={"title": "Owner's"})).json()
    assert (await client.get(f"{AGENT}/conversations/{other['id']}/messages", headers=token)).status_code == 403
    # Once the person loses access, the run's work stops.
    row = next(m for m in (await client.get(f"{API}/settings/members", headers=ws(tpl))).json() if m["user_id"] == "usr_agent_viewer")
    await client.delete(f"{API}/settings/members/{row['id']}", headers=ws(tpl))
    assert (await client.get(f"{AGENT}/documents", headers=token)).status_code == 403


async def test_agent_tokens_must_match_their_event(client):
    tpl = await create_template(client)
    other = await create_template(client, "Another")
    chat = (await client.post(f"{API}/conversations", headers=ws(tpl), json={"title": "t"})).json()
    await client.post(f"{API}/conversations/{chat['id']}/messages?wait=false", headers=ws(tpl), json={"content": "hi"})
    event = [e for e in await outbox_events(tpl, "human.message")][-1]
    # The right event id but another workspace's scope is refused.
    forged = {"Authorization": f"Bearer {mint_agent_token(TENANT, other, event.id)}"}
    assert (await client.get(f"{AGENT}/documents", headers=forged)).status_code == 401
    assert (await client.get(f"{AGENT}/documents", headers={"Authorization": "Bearer not-a-token"})).status_code == 401


async def test_org_admins_can_delete_a_workspace_without_opening_it(client):
    tpl = await create_template(client, "Doomed")
    slug = await slug_of(client, tpl)
    await client.post(f"{API}/settings/roles", headers=ws(tpl), json={"name": "Temp role", "permissions": ["documents.view"]})
    boss, plain = person("usr_del_admin", "Dee Admin"), person("usr_del_plain", "Pip Plain")
    await register(boss, "admin")
    await register(plain, "member")
    assert (await client.delete(f"{API}/workspaces/{slug}", headers=plain)).status_code == 403
    assert (await client.delete(f"{API}/workspaces/{slug}", headers=boss)).status_code == 204
    assert slug not in [w["slug"] for w in (await client.get(f"{API}/workspaces")).json()]
