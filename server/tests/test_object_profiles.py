"""
Object and thing profiles: every object of a template has a page, so does every thing it has met; each object has an
icon from one list, picked by the agent; both search boxes find objects and things.
"""
import copy
import subprocess
import sys
from pathlib import Path

from sqlalchemy import select

from app.models.graph import RecordObject
from app.services import object_icons, object_index
from tests.conftest import AsyncSessionLocal, agent, create_template, upload, ws
from tests.test_invoices import ADMIN_APP

API = "/api/v1"
AGENT = "/internal/agent/v1"
ROOT = Path(__file__).resolve().parents[2]

SCHEMA = {
    "document_type": "visits",
    "objects": [
        {"type": "Place", "icon": "map-pin", "multiple": True, "identity": {"id_field": "code", "label_field": "name"},
         "fields": [{"name": "code", "type": "string", "required": True}, {"name": "name", "type": "string"},
                    {"name": "area", "type": "string"}]},
        {"type": "Visit", "icon": "calendar-check", "multiple": True,
         "fields": [{"name": "place", "type": "string", "resolves_to": "Place"}, {"name": "hours", "type": "number"},
                    {"name": "kind", "type": "string"}]},
    ],
    "validation_rules": [],
}


def record(*visits, places=()):
    return {"Place": [{"code": c, "name": n, "area": a} for c, n, a in places],
            "Visit": [{"place": p, "hours": h, "kind": k} for p, h, k in visits]}


async def commit(client, tpl, name, data):
    doc = await upload(client, tpl, name)
    evidence = [{"object": "Place", "index": i, "field": "name", "value": p["name"], "raw_text": p["name"], "confidence": 0.95}
                for i, p in enumerate(data.get("Place", []))]
    res = await client.post(f"{AGENT}/documents/{doc}/record", headers=agent(tpl),
                            json={"data": data, "evidence": evidence, "decision": "COMMIT", "confidence": 0.97})
    assert res.status_code == 200, res.text
    return doc


# ---- the icon list ---------------------------------------------------------------------------------------------------

def test_the_icon_list_and_its_readers_are_in_step():
    assert object_icons.DEFAULT_ICON in object_icons.ICON_NAMES and len(set(object_icons.ICON_NAMES)) == len(object_icons.ICON_NAMES)
    # The agent's guide and both apps' maps are written from the list (scripts/object_icons.py --check)
    done = subprocess.run([sys.executable, "-m", "scripts.object_icons", "--check"], cwd=ROOT, capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr


def test_icon_problems_name_the_object_and_suggest_close_ones():
    assert object_icons.problems(SCHEMA) == []
    bad = object_icons.problems({"objects": [{"type": "A", "icon": "map-pinn"}, {"type": "B"}]})
    assert 'Object "A"' in bad[0] and "map-pin" in bad[0]
    assert 'Object "B" has no icon' in bad[1]
    assert object_icons.icon_of({"icon": "nope"}) == object_icons.DEFAULT_ICON


async def test_the_agent_must_give_every_object_an_icon_and_keeps_the_ones_chosen(client):
    tpl = await create_template(client, schema=SCHEMA)
    proposal = {"proposal_type": "TEMPLATE_CHANGE", "observation": "x", "proposed_data": {"schema_definition": None}}

    wrong = copy.deepcopy(SCHEMA)
    wrong["objects"][0]["icon"] = "a-made-up-icon"
    res = await client.post(f"{AGENT}/proposals", headers=agent(tpl), json={**proposal, "proposed_data": {"schema_definition": wrong}})
    assert res.status_code == 422 and "a-made-up-icon" in str(res.json())

    # Left out: the object keeps the icon it has now
    kept = copy.deepcopy(SCHEMA)
    del kept["objects"][0]["icon"]
    res = await client.post(f"{AGENT}/proposals", headers=agent(tpl), json={**proposal, "proposed_data": {"schema_definition": kept}})
    assert res.status_code == 201, res.text
    assert res.json()["proposed_data"]["schema_definition"]["objects"][0]["icon"] == "map-pin"

    # A new object without one is refused
    new = copy.deepcopy(SCHEMA)
    new["objects"].append({"type": "Note", "fields": [{"name": "text", "type": "string"}]})
    res = await client.post(f"{AGENT}/proposals", headers=agent(tpl), json={**proposal, "proposed_data": {"schema_definition": new}})
    assert res.status_code == 422 and 'Object "Note" has no icon' in str(res.json())

    # People may save one without an icon (it shows the default), never with a name not in the list
    res = await client.post(f"{API}/templates/{tpl}/versions", headers=ws(tpl), json={"version": "2.0.0", "schema_definition": new, "change_summary": "note"})
    assert res.status_code in (200, 201), res.text
    res = await client.post(f"{API}/templates/{tpl}/versions", headers=ws(tpl), json={"version": "2.1.0", "schema_definition": wrong, "change_summary": "bad"})
    assert res.status_code == 422


# ---- the index -------------------------------------------------------------------------------------------------------

def test_a_record_is_indexed_object_by_object_with_things_it_only_refers_to():
    rows = object_index.entries(SCHEMA, record(("p 1", 3, "check"), ("P2", 1, "check"), places=[("P1", "North yard", "North")]))
    by = {(r["object_type"], r["thing_key"]): r for r in rows}
    assert by[("Place", "Place:P1")]["values"] == {"code": "P1", "name": "North yard", "area": "North"}
    assert by[("Place", "Place:P1")]["label"] == "North yard" and by[("Place", "Place:P1")]["rows"] == 1
    assert by[("Place", "Place:P2")]["rows"] == 0 and by[("Place", "Place:P2")]["values"] is None   # only referred to
    assert by[("Visit", None)]["rows"] == 2


async def test_committing_indexes_the_record_and_sending_it_back_to_review_forgets_it(client):
    tpl = await create_template(client, schema=SCHEMA)
    doc = await commit(client, tpl, "one.png", record(("P1", 2, "check"), places=[("P1", "North yard", "North")]))
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(RecordObject).where(RecordObject.document_id == doc))).scalars().all()
    assert {(r.object_type, r.thing_key) for r in rows} == {("Place", "Place:P1"), ("Visit", None)}

    res = await client.post(f"{AGENT}/documents/{doc}/record", headers=agent(tpl),
                            json={"data": record(("P1", 2, "check")), "decision": "REVIEW", "confidence": 0.4, "review_reason": "unsure"})
    assert res.status_code == 200, res.text
    async with AsyncSessionLocal() as db:
        assert (await db.execute(select(RecordObject).where(RecordObject.document_id == doc))).scalars().all() == []


# ---- profiles --------------------------------------------------------------------------------------------------------

async def test_object_and_thing_profiles(client):
    tpl = await create_template(client, schema=SCHEMA)
    first = await commit(client, tpl, "first.png", record(("P1", 2, "check"), ("P2", 4, "repair"), places=[("P1", "North yard", "North"), ("P2", "South gate", "South")]))
    second = await commit(client, tpl, "second.png", record(("P1", 6, "check"), places=[("P1", "North yard", "North side")]))

    objects = (await client.get(f"{API}/objects", headers=ws(tpl))).json()
    assert [(o["type"], o["icon"], o["documents"], o["things"]) for o in objects] == [("Place", "map-pin", 2, 2), ("Visit", "calendar-check", 2, 0)]

    visit = (await client.get(f"{API}/objects/Visit", headers=ws(tpl))).json()
    assert visit["object"]["icon"] == "calendar-check" and [f["name"] for f in visit["object"]["fields"]] == ["place", "hours", "kind"]
    assert visit["links"] == {"place": "Place"} and visit["things"] is None
    assert visit["stats"]["documents"] == 2 and visit["stats"]["rows"] == 3
    figures = {f["field"]: f for f in visit["figures"]}
    assert figures["hours"]["number"] == {"sum": 12.0, "average": 4.0, "min": 2.0, "max": 6.0}
    assert figures["kind"]["top"][0] == {"value": "check", "count": 2} and figures["kind"]["filled"] == 3
    assert sum(w["count"] for w in visit["weeks"]) == 2 and len(visit["weeks"]) == 12
    assert [d["id"] for d in visit["documents"]["items"]] == [second, first]

    place = (await client.get(f"{API}/objects/Place", headers=ws(tpl))).json()
    assert place["referenced_by"] == [{"type": "Visit", "field": "place"}]
    assert [(t["label"], t["documents"]) for t in place["things"]["items"]] == [("North yard", 2), ("South gate", 1)]
    assert place["things"]["items"][0]["path"] == "objects/Place/things/P1"

    thing = (await client.get(f"{API}/objects/Place/things/P1", headers=ws(tpl))).json()
    assert thing["thing"] == {"key": "Place:P1", "id": "P1", "label": "North yard"} and thing["object"]["icon"] == "map-pin"
    attrs = {a["field"]: a for a in thing["attributes"]}
    assert attrs["area"]["value"] == "North side" and attrs["area"]["other_values"] == ["North"]   # the newest record wins
    assert thing["stats"]["documents"] == 2 and {d["id"] for d in thing["documents"]["items"]} == {first, second}

    assert (await client.get(f"{API}/objects/Place/things/ZZ9", headers=ws(tpl))).status_code == 404
    assert (await client.get(f"{API}/objects/Nothing", headers=ws(tpl))).status_code == 404
    assert (await client.get(f"{API}/objects/Visit/things", headers=ws(tpl))).status_code == 404   # no identity, no things
    found = (await client.get(f"{API}/objects/Place/things", headers=ws(tpl), params={"q": "north"})).json()
    assert [t["id"] for t in found["items"]] == ["P1"]

    # Another workspace can't reach these
    other = await create_template(client, name="Other", schema=SCHEMA)
    assert (await client.get(f"{API}/objects/Place/things/P1", headers=ws(other))).status_code == 404

    # Staff see the same, with the organization and the workspace
    staff = (await client.get(f"{API}/sysadmin/objects/{tpl}/Place/things/P1", headers=ADMIN_APP)).json()
    assert staff["thing"]["label"] == "North yard" and staff["workspace"]["id"] == tpl and staff["organization"]["id"]
    listed = (await client.get(f"{API}/sysadmin/objects/{tpl}", headers=ADMIN_APP)).json()
    assert [o["type"] for o in listed["objects"]] == ["Place", "Visit"]
    assert (await client.get(f"{API}/sysadmin/objects/{tpl}", params={"q": "x"})).status_code == 403


async def test_the_scheduler_indexes_records_committed_before_the_index(client):
    from app.models.template import DocumentTemplate
    from app.services import scheduler
    tpl = await create_template(client, schema=SCHEMA)
    doc = await commit(client, tpl, "old.png", record(("P7", 1, "check"), places=[("P7", "Old gate", "West")]))
    async with AsyncSessionLocal() as db:
        await object_index.forget_record(db, doc)
        await db.commit()
    async with AsyncSessionLocal() as db:
        state = await scheduler.sync_objects(db, await db.get(DocumentTemplate, tpl))
        await db.commit()
    assert state["rows"] == 2
    thing = (await client.get(f"{API}/objects/Place/things/P7", headers=ws(tpl))).json()
    assert thing["thing"]["label"] == "Old gate"
    async with AsyncSessionLocal() as db:   # nothing to do until the template changes
        again = await scheduler.sync_objects(db, await db.get(DocumentTemplate, tpl))
        await db.commit()
    assert again == state


# ---- search ----------------------------------------------------------------------------------------------------------

async def test_both_search_boxes_find_objects_and_things(client):
    tpl = await create_template(client, name="Quarry visits", schema=SCHEMA)
    await commit(client, tpl, "q.png", record(("QX1", 2, "check"), places=[("QX1", "Quarrystone depot", "East")]))

    found = {g["key"]: g["items"] for g in (await client.get(f"{API}/search", params={"q": "quarrystone"})).json()["groups"]}
    [thing] = found["things"]
    assert thing["title"] == "Quarrystone depot" and thing["icon"] == "map-pin" and thing["href"].endswith("/objects/Place/things/QX1")
    objects = {g["key"]: g["items"] for g in (await client.get(f"{API}/search", params={"q": "place"})).json()["groups"]}["objects"]
    assert any(o["title"] == "Place" and o["href"].endswith("/objects/Place") for o in objects)

    staff = {g["key"]: g["items"] for g in (await client.get(f"{API}/sysadmin/search", headers=ADMIN_APP, params={"q": "quarrystone"})).json()["groups"]}
    [thing] = staff["things"]
    assert thing["href"] == f"/objects/{tpl}/Place/things/QX1" and "Quarry visits" in thing["tags"] and len(thing["tags"]) == 2
