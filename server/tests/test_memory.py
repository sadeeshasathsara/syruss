"""
What Syruss remembers: scopes, privacy, retrieval, and the people's controls.
"""
from datetime import datetime, timezone
from app.core.agent_token import mint_agent_token
from tests.conftest import TENANT, agent, create_template, join, outbox_events, person, ws

API = "/api/v1"
AGENT = "/internal/agent/v1"
NIMAL = person("usr_mem_nimal", "Nimal")


async def chat_run(client, tpl: str, headers: dict, text: str = "hello") -> tuple:
    """A chat from someone, and the agent's token for the run that answers it."""
    chat = (await client.post(f"{API}/conversations", headers={**ws(tpl), **headers}, json={"title": "t"})).json()
    await client.post(f"{API}/conversations/{chat['id']}/messages?wait=false", headers={**ws(tpl), **headers}, json={"content": text})
    event = [e for e in await outbox_events(tpl, "human.message") if e.payload["conversation_id"] == chat["id"]][0]
    return chat, {"Authorization": f"Bearer {mint_agent_token(TENANT, tpl, event.id)}"}


async def test_personal_memory_stays_with_its_person(client):
    tpl = await create_template(client)
    await client.get(f"{API}/settings", headers=ws(tpl))
    await join(client, tpl, NIMAL, "reviewer")
    _, as_owner_chat = await chat_run(client, tpl, {})
    _, as_nimal_chat = await chat_run(client, tpl, NIMAL)

    res = await client.post(f"{AGENT}/memories", headers=as_owner_chat,
                            json={"scope": "personal", "kind": "preference", "content": "Prefers answers as short tables"})
    assert res.status_code == 201, res.text
    # Only from that person's own chat: a work run can't write personal memory.
    res = await client.post(f"{AGENT}/memories", headers=agent(tpl), json={"scope": "personal", "kind": "fact", "content": "Something"})
    assert res.status_code == 403

    found = (await client.post(f"{AGENT}/memories/search", headers=as_owner_chat,
                               json={"query": "how to format answers tables", "scopes": ["personal", "workspace"]})).json()
    assert [m["content"] for m in found] == ["Prefers answers as short tables"]
    assert (await client.post(f"{AGENT}/memories/search", headers=as_nimal_chat,
                              json={"query": "tables", "scopes": ["personal"]})).json() == []

    mine = (await client.get(f"{API}/memories?scope=personal", headers=ws(tpl))).json()
    assert len(mine["items"]) == 1 and mine["counts"]["personal"] == 1
    assert (await client.get(f"{API}/memories?scope=personal", headers={**ws(tpl), **NIMAL})).json()["items"] == []
    # Forget me.
    await client.delete(f"{API}/memories", headers=ws(tpl))
    assert (await client.get(f"{API}/memories?scope=personal", headers=ws(tpl))).json()["items"] == []


async def test_workspace_lessons_are_found_and_strengthened(client):
    tpl = await create_template(client)
    for text in ("Night shifts run from 19:00 to 07:00", "Site codes start with S and three digits"):
        res = await client.post(f"{AGENT}/memories", headers=agent(tpl), json={"kind": "lesson", "content": text})
        assert res.json()["created"] is True
    again = (await client.post(f"{AGENT}/memories", headers=agent(tpl), json={"kind": "lesson", "content": "night shifts run from 19:00 to 07:00 "})).json()
    assert again["created"] is False and again["importance"] > 0.5

    found = (await client.post(f"{AGENT}/memories/search", headers=agent(tpl), json={"query": "when do night shifts end?"})).json()
    assert found[0]["content"].startswith("Night shifts")
    everything = (await client.post(f"{AGENT}/memories/search", headers=agent(tpl), json={"limit": 10})).json()
    assert len(everything) == 2

    # Pinned memories always come first.
    site = next(m for m in everything if m["content"].startswith("Site"))
    await client.get(f"{API}/settings", headers=ws(tpl))
    await client.patch(f"{API}/memories/{site['id']}", headers=ws(tpl), json={"pinned": True})
    found = (await client.post(f"{AGENT}/memories/search", headers=agent(tpl), json={"query": "night shifts"})).json()
    assert found[0]["id"] == site["id"]


async def test_people_manage_workspace_memory_by_role(client):
    tpl = await create_template(client)
    await client.get(f"{API}/settings", headers=ws(tpl))
    await join(client, tpl, NIMAL, "reviewer")
    taught = (await client.post(f"{API}/memories", headers=ws(tpl), json={"scope": "workspace", "kind": "fact", "content": "Hemas sites are in Colombo"})).json()
    assert taught["created_by"] == "usr_admin_default"
    res = await client.patch(f"{API}/memories/{taught['id']}", headers={**ws(tpl), **NIMAL}, json={"content": "Changed"})
    assert res.status_code == 403
    assert (await client.post(f"{API}/memories", headers={**ws(tpl), **NIMAL}, json={"scope": "personal", "kind": "preference", "content": "Call me Nimal"})).status_code == 201
    edited = (await client.patch(f"{API}/memories/{taught['id']}", headers=ws(tpl), json={"content": "Hemas sites are in Colombo 03"})).json()
    assert edited["content"].endswith("03")
    assert (await client.delete(f"{API}/memories/{taught['id']}", headers=ws(tpl))).status_code == 204


async def test_conversation_summaries_live_and_die_with_their_chat(client):
    tpl = await create_template(client)
    chat, as_chat = await chat_run(client, tpl, {})
    covers = datetime.now(timezone.utc).isoformat()
    res = await client.put(f"{AGENT}/conversations/{chat['id']}/summary", headers=as_chat,
                           json={"content": "They asked about July rosters.", "covers_until": covers})
    assert res.status_code == 200, res.text
    got = (await client.get(f"{AGENT}/conversations/{chat['id']}/summary", headers=as_chat)).json()
    assert got["content"] == "They asked about July rosters." and got["covers_until"].startswith(covers[:19])

    await client.delete(f"{API}/conversations/{chat['id']}", headers=ws(tpl))
    assert (await client.get(f"{AGENT}/conversations/{chat['id']}/summary", headers=as_chat)).json()["content"] is None


async def test_older_notes_move_into_workspace_memory(client):
    tpl = await create_template(client)
    res = await client.post(f"{AGENT}/memories/import", headers=agent(tpl), json={"notes": [
        {"kind": "lesson", "content": "Ranks are SG, LSO or JSO"}, {"kind": "observation", "content": "Sheets often have a signature column"},
    ]})
    assert res.json() == {"imported": 2}
    listed = (await client.get(f"{API}/memories?scope=workspace", headers=ws(tpl))).json()["items"]
    assert {m["source"]["type"] for m in listed} == {"import"}
