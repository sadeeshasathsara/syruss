"""The identification key: how a document is named to people instead of by its file name."""
from app.services.document_key import identification_key, identification_spec
from app.services.rules.engine import check_rule_definitions

SCHEMA = {
    "objects": [
        {"type": "Roster", "multiple": False, "fields": [{"name": "location"}, {"name": "date"}, {"name": "shift"}]},
        {"type": "Assignment", "multiple": True, "fields": [{"name": "employee"}]},
    ],
    "identification": {"parts": [{"object": "Roster", "field": "location"}, {"object": "Roster", "field": "date"}], "separator": "_"},
}
DATA = {"Roster": [{"location": "Colombo  Port", "date": "2026-07-22"}], "Assignment": [{"employee": "A"}]}


def test_key_joins_the_parts_in_order():
    assert identification_key(SCHEMA, DATA) == "Colombo Port_2026-07-22"
    assert identification_key(SCHEMA, {"Roster": {"location": "Galle", "date": "2026-07-23"}}) == "Galle_2026-07-23"


def test_key_skips_missing_values_and_gives_none_without_any():
    assert identification_key(SCHEMA, {"Roster": [{"location": "Galle"}]}) == "Galle"
    assert identification_key(SCHEMA, {"Roster": [{}]}) is None
    assert identification_key(SCHEMA, None) is None
    assert identification_key({"objects": SCHEMA["objects"]}, DATA) is None


def test_separator_can_change():
    schema = {**SCHEMA, "identification": {**SCHEMA["identification"], "separator": " / "}}
    assert identification_key(schema, DATA) == "Colombo Port / 2026-07-22"
    assert identification_spec({"identification": {"parts": [{"object": "Roster", "field": "date"}]}})["separator"] == "_"


def test_a_valid_identification_passes_the_template_checks():
    assert check_rule_definitions(SCHEMA) == []


def test_only_one_per_document_fields_may_identify():
    def problems(parts):
        return check_rule_definitions({**SCHEMA, "identification": {"parts": parts}})
    assert any("once per document" in p for p in problems([{"object": "Assignment", "field": "employee"}]))
    assert any("unknown object" in p for p in problems([{"object": "Nope", "field": "x"}]))
    assert any("not a field" in p for p in problems([{"object": "Roster", "field": "nope"}]))
    assert any("twice" in p for p in problems([{"object": "Roster", "field": "date"}, {"object": "Roster", "field": "date"}]))
    assert any("at least one" in p for p in problems([]))


# ---- through the API -------------------------------------------------------------------------------------------------
from sqlalchemy import select  # noqa: E402
from app.models.notification import Notification  # noqa: E402
from tests.conftest import AsyncSessionLocal, SCHEMA as ROSTER_SCHEMA, VALID_RECORD, agent, create_template, upload, ws  # noqa: E402

API = "/api/v1"
AGENT = "/internal/agent/v1"
KEY = {"parts": [{"object": "Roster", "field": "site_id"}, {"object": "Roster", "field": "date"}], "separator": "_"}


async def _listed(client, tpl):
    return {d["id"]: d for d in (await client.get(f"{API}/documents", headers=ws(tpl))).json()}


async def test_documents_are_named_by_their_key_once_read_and_by_file_when_not(client):
    tpl = await create_template(client, schema={**ROSTER_SCHEMA, "identification": KEY})
    doc = await upload(client, tpl, "94771079086_20260722_223851.jpeg")
    assert (await _listed(client, tpl))[doc]["display_name"] is None  # nothing read yet: the file name stands in

    saved = await client.post(f"{AGENT}/documents/{doc}/record", headers=agent(tpl),
                              json={"data": VALID_RECORD, "decision": "COMMIT", "confidence": 0.97})
    assert saved.status_code == 200, saved.text
    listed = (await _listed(client, tpl))[doc]
    assert listed["display_name"] == "S12_2026-07-22" and listed["original_filename"] == "94771079086_20260722_223851.jpeg"

    log = (await client.get(f"{API}/extraction-log", headers=ws(tpl))).json()["items"]
    assert log[0]["stage"] == "saved" and log[0]["document_name"] == "S12_2026-07-22"


async def test_notifications_name_the_document_by_its_key(client):
    tpl = await create_template(client, schema={**ROSTER_SCHEMA, "identification": KEY})
    doc = await upload(client, tpl, "94771079086_20260722_223851.jpeg")
    await client.post(f"{AGENT}/documents/{doc}/record", headers=agent(tpl), json={
        "data": VALID_RECORD, "decision": "REVIEW", "confidence": 0.5, "review_reason": "Hours are smudged"})
    async with AsyncSessionLocal() as db:
        told = (await db.execute(select(Notification).where(Notification.type == "document.needs_review", Notification.template_id == tpl))).scalars().all()
    assert told and {n.title for n in told} == {"S12_2026-07-22 needs review"}


async def test_the_key_can_be_changed_and_everything_follows(client):
    tpl = await create_template(client, schema={**ROSTER_SCHEMA, "identification": KEY})
    doc = await upload(client, tpl)
    await client.post(f"{AGENT}/documents/{doc}/record", headers=agent(tpl),
                      json={"data": VALID_RECORD, "decision": "COMMIT", "confidence": 0.97})

    changed = await client.put(f"{API}/templates/{tpl}/identification", headers=ws(tpl), json={
        "parts": [{"object": "Roster", "field": "date"}, {"object": "Roster", "field": "site_id"}], "separator": " - "})
    assert changed.status_code == 200, changed.text
    assert changed.json()["schema_definition"]["identification"]["separator"] == " - "
    assert (await _listed(client, tpl))[doc]["display_name"] == "2026-07-22 - S12"
    assert (await client.get(f"{API}/extraction-log", headers=ws(tpl))).json()["items"][0]["document_name"] == "2026-07-22 - S12"

    cleared = await client.put(f"{API}/templates/{tpl}/identification", headers=ws(tpl), json={"parts": []})
    assert cleared.status_code == 200 and "identification" not in cleared.json()["schema_definition"]
    assert (await _listed(client, tpl))[doc]["display_name"] is None


async def test_a_key_must_use_fields_that_appear_once_per_document(client):
    tpl = await create_template(client)
    refused = await client.put(f"{API}/templates/{tpl}/identification", headers=ws(tpl),
                               json={"parts": [{"object": "Guard", "field": "name"}]})
    assert refused.status_code == 422 and "once per document" in str(refused.json())
    assert (await client.put(f"{API}/templates/{tpl}/identification", headers=ws(tpl),
                             json={"parts": [{"object": "Roster", "field": "nope"}]})).status_code == 422


async def test_the_agent_can_name_a_document_before_it_has_a_record(client):
    tpl = await create_template(client, schema={**ROSTER_SCHEMA, "identification": KEY})
    doc = await upload(client, tpl, "94771079086_20260722_223851.jpeg")

    named = await client.post(f"{AGENT}/documents/{doc}/identification", headers=agent(tpl),
                              json={"details": {"Roster": {"site_id": "S12", "date": "2026-07-22"}}})
    assert named.status_code == 200 and named.json()["name"] == "S12_2026-07-22", named.text
    assert (await _listed(client, tpl))[doc]["display_name"] == "S12_2026-07-22"
    seen = (await client.get(f"{AGENT}/documents/{doc}", headers=agent(tpl))).json()
    assert seen["name"] == "S12_2026-07-22" and seen["filename"] == "94771079086_20260722_223851.jpeg"

    empty = await client.post(f"{AGENT}/documents/{doc}/identification", headers=agent(tpl), json={"details": {"Guard": {"name": "x"}}})
    assert empty.status_code == 422 and "Roster" in empty.text

    plain = await create_template(client, name="Plain")
    other = await upload(client, plain)
    refused = await client.post(f"{AGENT}/documents/{other}/identification", headers=agent(plain), json={"details": {"Roster": {"site_id": "S1"}}})
    assert refused.status_code == 409
