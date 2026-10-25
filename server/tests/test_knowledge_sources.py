"""
Reference data: a person adds a file or an API, the agent maps it onto the template's
identified objects, and approving the mapping imports the records as known entities.
"""
import copy
import json
import httpx
import pytest
from app.core.config import settings
from app.services import source_data
from tests.conftest import SCHEMA, agent, create_template, outbox_events, ws

API = "/api/v1"
AGENT = "/internal/agent/v1"

STAFF_CSV = (
    "EmpNo,Full name,Grade,Known as,Site\n"
    "75599,Kamal Perera,SG,K. Perera,S12\n"
    "76399,Sarath Silva,LSO,,S12\n"
    ",No Number,SG,,S12\n"
).encode()

MAPPING = {"records_path": "", "objects": [{
    "entity_type": "Guard", "id_column": "EmpNo", "name_column": "Full name",
    "attributes": {"rank": "Grade"}, "alias_columns": ["Known as"],
}]}


async def identified_workspace(client) -> str:
    schema = copy.deepcopy(SCHEMA)
    schema["objects"][1]["identity"] = {"id_field": "employee_id", "label_field": "name"}
    return await create_template(client, schema=schema)


async def test_the_agent_maps_a_file_and_approval_imports_it(client):
    tpl = await identified_workspace(client)
    chat = (await client.post(f"{API}/conversations", headers=ws(tpl), json={"title": "Setup"})).json()
    res = await client.post(f"{API}/knowledge/sources/file", headers={**ws(tpl), "X-Conversation-ID": chat["id"]},
                            files={"file": ("staff.csv", STAFF_CSV, "text/csv")}, data={"notes": "Our staff register"})
    assert res.status_code == 201, res.text
    source = res.json()
    assert source["status"] == "MAPPING" and source["record_sets"][0]["count"] == 3
    [event] = await outbox_events(tpl, "knowledge.source_added")
    assert event.payload["source_id"] == source["id"] and event.payload["notes"] == "Our staff register"
    assert event.actor["conversation_id"] == chat["id"]  # done in that chat, so its progress shows there
    thread = (await client.get(f"{API}/conversations/{chat['id']}", headers=ws(tpl))).json()
    assert thread["messages"][-1]["content"] == "You added the data source 'staff.csv'."

    # The agent sees the records and what they can map onto.
    seen = (await client.get(f"{AGENT}/sources/{source['id']}", headers=agent(tpl))).json()
    assert seen["record_sets"][0]["columns"] == ["EmpNo", "Full name", "Grade", "Known as", "Site"]
    assert [o["type"] for o in seen["identified_objects"]] == ["Guard"]

    # Mappings that do not fit are refused with reasons.
    bad = {"records_path": "", "objects": [{"entity_type": "Roster", "id_column": "Badge"}]}
    res = await client.post(f"{AGENT}/sources/{source['id']}/mapping", headers=agent(tpl), json={"mapping": bad, "observation": "x"})
    problems = res.json()["detail"]["problems"]
    assert res.status_code == 422 and any("not something this template identifies" in p for p in problems)
    assert any("no column 'Badge'" in p for p in problems)

    # A dry run shows what the import would do.
    res = await client.post(f"{AGENT}/sources/{source['id']}/mapping", headers=agent(tpl),
                            json={"mapping": MAPPING, "observation": "Staff register", "dry_run": True})
    [preview] = res.json()["preview"]
    assert (preview["new"], preview["already_known"], preview["rows_without_id"]) == (2, 0, 1)

    # A person already knows one guard under another name: the name stays, the source's becomes an alias.
    await client.post(f"{API}/knowledge/entities", headers=ws(tpl), json={
        "entity_type": "Guard", "name": "Kamal P.", "identifier_key": "employee_id", "identifier_value": "75599"})

    res = await client.post(f"{AGENT}/sources/{source['id']}/mapping", headers=agent(tpl),
                            json={"mapping": MAPPING, "observation": "Staff register: one row per guard"})
    proposal = res.json()["proposal"]
    assert res.json()["preview"][0]["names_differ"] == 1
    listed = (await client.get(f"{API}/knowledge/sources", headers=ws(tpl))).json()
    assert listed[0]["status"] == "REVIEW"

    res = await client.post(f"{API}/knowledge/proposals/{proposal['id']}/review", headers=ws(tpl), json={"action": "APPROVE"})
    assert res.status_code == 200, res.text
    entities = {e["identifier_value"]: e for e in (await client.get(f"{API}/knowledge/entities", headers=ws(tpl))).json()}
    assert entities["75599"]["name"] == "Kamal P."
    assert set(entities["75599"]["aliases"]) == {"Kamal Perera", "K. Perera"}
    assert entities["76399"]["name"] == "Sarath Silva" and entities["76399"]["attributes"] == {"rank": "LSO"}
    listed = (await client.get(f"{API}/knowledge/sources", headers=ws(tpl))).json()
    assert listed[0]["status"] == "READY" and listed[0]["imported_count"] == 2

    # Syncing again with the approved mapping changes nothing new.
    res = await client.post(f"{API}/knowledge/sources/{source['id']}/sync", headers=ws(tpl))
    assert res.json()["imported"]["created"] == 0 and res.json()["imported"]["updated"] == 2


async def test_nested_json_records_are_found(client):
    tpl = await identified_workspace(client)
    body = json.dumps({"meta": {"page": 1}, "data": {"staff": [{"id": 1, "profile": {"name": "Ann"}}]}}).encode()
    res = await client.post(f"{API}/knowledge/sources/file", headers=ws(tpl), files={"file": ("staff.json", body, "application/json")})
    source = res.json()
    seen = (await client.get(f"{AGENT}/sources/{source['id']}", headers=agent(tpl))).json()
    [records] = seen["record_sets"]
    assert records["records_path"] == "data.staff" and records["columns"] == ["id", "profile.name"]
    mapping = {"records_path": "data.staff", "objects": [{"entity_type": "Guard", "id_column": "id", "name_column": "profile.name"}]}
    res = await client.post(f"{AGENT}/sources/{source['id']}/mapping", headers=agent(tpl), json={"mapping": mapping, "observation": "x", "dry_run": True})
    assert res.json()["preview"][0]["examples"][0] == {"id": "1", "name": "Ann", "attributes": {}, "aliases": []}


@pytest.fixture
def staff_api(monkeypatch):
    """An API behind OAuth client credentials, answered in-process."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/oauth/token":
            assert b"client_secret=s3cret" in request.content
            return httpx.Response(200, json={"access_token": "tok-1"})
        if request.headers.get("authorization") != "Bearer tok-1" or request.headers.get("x-tenant") != "acme":
            return httpx.Response(401)
        return httpx.Response(200, json={"items": [{"code": "75599", "name": "Kamal Perera"}]})

    monkeypatch.setattr(source_data, "HTTP_TRANSPORT", httpx.MockTransport(handler))
    monkeypatch.setattr(settings, "KNOWLEDGE_SOURCES_ALLOW_PRIVATE_HOSTS", True)
    return calls


API_SOURCE = {
    "name": "HR system", "url": "https://hr.example.test/staff", "headers": {"X-Tenant": "acme"},
    "auth": {"type": "oauth2_client_credentials", "token_url": "https://hr.example.test/oauth/token",
             "client_id": "docs", "client_secret": "s3cret", "scope": "staff.read"},
}


async def test_api_sources_keep_their_credentials_secret(client, staff_api):
    tpl = await identified_workspace(client)
    res = await client.post(f"{API}/knowledge/sources/api/test", headers=ws(tpl), json=API_SOURCE)
    assert res.status_code == 200, res.text
    assert res.json()["record_sets"][0]["records_path"] == "items"

    res = await client.post(f"{API}/knowledge/sources/api", headers=ws(tpl), json=API_SOURCE)
    assert res.status_code == 201, res.text
    source = res.json()
    text = json.dumps((await client.get(f"{API}/knowledge/sources", headers=ws(tpl))).json())
    assert "s3cret" not in text and "acme" not in text
    assert source["api"]["auth"]["saved_secrets"] == ["client_secret"] and source["api"]["header_names"] == ["X-Tenant"]
    assert "s3cret" not in json.dumps((await client.get(f"{AGENT}/sources/{source['id']}", headers=agent(tpl))).json())

    # Changing the settings without retyping the secrets keeps them.
    changed = {**API_SOURCE, "name": "HR", "headers": {"X-Tenant": ""}, "auth": {**API_SOURCE["auth"], "client_secret": ""}}
    res = await client.put(f"{API}/knowledge/sources/{source['id']}", headers=ws(tpl), json=changed)
    assert res.status_code == 200, res.text

    # Wrong credentials are reported in words.
    wrong = {**API_SOURCE, "headers": {"X-Tenant": "other"}}
    res = await client.post(f"{API}/knowledge/sources/api/test", headers=ws(tpl), json=wrong)
    assert res.status_code == 422 and "refused the credentials" in res.json()["detail"]


async def test_private_addresses_are_not_called(client, monkeypatch):
    monkeypatch.setattr(settings, "KNOWLEDGE_SOURCES_ALLOW_PRIVATE_HOSTS", False)
    tpl = await identified_workspace(client)
    res = await client.post(f"{API}/knowledge/sources/api/test", headers=ws(tpl), json={"url": "http://127.0.0.1:8000/admin"})
    assert res.status_code == 422 and "private network" in res.json()["detail"]
