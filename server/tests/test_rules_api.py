"""
Template rules over the API: saved only when they compile against the template, tried on the saved records, run on every
record (an error rule stops the commit, a warning is reported), broken when the template changes under them, and suggested
by the agent for a person to add.
"""
import copy
from tests.conftest import SCHEMA, TENANT, agent, create_template, join, person, register, upload, ws
from tests.test_screens import chat_token, commit

API = "/api/v1"
AGENT = "/internal/agent/v1"

SHIFT_LIMIT = {"name": "Shift at most 16 hours", "scope": "Guard", "check": "hours <= 16", "message": "A shift is longer than 16 hours."}


async def test_a_rule_is_saved_only_when_it_compiles_and_is_tried_on_saved_records(client):
    tpl = await create_template(client)
    await commit(client, tpl, "2026-10-01", [("75599", "Perera", "07:00", "19:00", 12)])
    await commit(client, tpl, "2026-10-02", [("75599", "Perera", "06:00", "23:00", 17)])

    # The editor's live check: problems with where and why, else what the rule says about the saved records.
    bad = (await client.post(f"{API}/rules/check", headers=ws(tpl), json={"scope": "Guard", "check": "hour <= 16"})).json()
    assert bad["ok"] is False and bad["problems"][0]["code"] == "unknown_name" and "hours" in bad["problems"][0]["hint"]
    assert bad["problems"][0]["col"] == 0 and bad["problems"][0]["end_col"] == 4
    good = (await client.post(f"{API}/rules/check", headers=ws(tpl), json={"scope": "Guard", "check": "hours <= 16"})).json()
    assert good["ok"] and good["dry_run"]["records"] == 2 and good["dry_run"]["passed"] == 1 and good["dry_run"]["failed"] == 1
    assert good["dry_run"]["failures"][0]["rows"] == ["Guard[0]"]

    refused = await client.post(f"{API}/rules", headers=ws(tpl), json={**SHIFT_LIMIT, "check": "hours <="})
    assert refused.status_code == 422 and refused.json()["detail"]["problems"][0]["code"] == "syntax"
    assert (await client.post(f"{API}/rules", headers=ws(tpl), json={**SHIFT_LIMIT, "severity": "fatal"})).status_code == 422
    made = await client.post(f"{API}/rules", headers=ws(tpl), json=SHIFT_LIMIT)
    assert made.status_code == 201, made.text
    rule = made.json()
    assert rule["status"] == "active" and rule["created_by_kind"] == "person" and rule["last_dry_run"]["failed"] == 1

    listed = (await client.get(f"{API}/rules", headers=ws(tpl))).json()
    assert [r["id"] for r in listed["rules"]] == [rule["id"]] and listed["template_checks"][0]["id"] == "shift_hours"
    lang = (await client.get(f"{API}/rules/language", headers=ws(tpl))).json()
    assert {o["type"] for o in lang["objects"]} == {"Roster", "Guard"} and any(a["name"] == "sum" for a in lang["aggregates"])

    # Changing it compiles it again; turning it off stops it.
    assert (await client.patch(f"{API}/rules/{rule['id']}", headers=ws(tpl), json={"check": "hours < "})).status_code == 422
    changed = (await client.patch(f"{API}/rules/{rule['id']}", headers=ws(tpl), json={"check": "hours <= 18"})).json()
    assert changed["check"] == "hours <= 18" and changed["last_dry_run"]["failed"] == 0
    assert (await client.patch(f"{API}/rules/{rule['id']}", headers=ws(tpl), json={"status": "disabled"})).json()["status"] == "disabled"
    assert (await client.delete(f"{API}/rules/{rule['id']}", headers=ws(tpl))).status_code == 204
    assert (await client.get(f"{API}/rules/{rule['id']}", headers=ws(tpl))).status_code == 404


async def test_rules_run_on_every_record(client):
    tpl = await create_template(client)
    await client.post(f"{API}/rules", headers=ws(tpl), json=SHIFT_LIMIT)
    await client.post(f"{API}/rules", headers=ws(tpl), json={"name": "Day shifts start early", "scope": "Guard",
                                                             "check": "in_time <= \"08:00\"", "severity": "warning"})
    record = {"Roster": [{"site_id": "S12", "date": "2026-10-01"}],
              "Guard": [{"employee_id": "75599", "name": "Perera", "in_time": "06:00", "out_time": "23:00", "hours": 17}]}

    # The agent's draft check shows the failing rule; a commit is refused with its message, and a review is allowed.
    draft = (await client.post(f"{AGENT}/records/validate", headers=agent(tpl), json={"data": record})).json()
    assert [e["code"] for e in draft["errors"]] == ["rule_failed"] and draft["errors"][0]["message"].startswith("A shift is longer")
    assert draft["warnings"] == [] and len(draft["rules"]) == 2
    doc = await upload(client, tpl)
    refused = await client.post(f"{AGENT}/documents/{doc}/record", headers=agent(tpl), json={"data": record, "decision": "COMMIT", "confidence": 0.95})
    assert refused.status_code == 422 and refused.json()["detail"]["errors"][0]["rule"] == "Shift at most 16 hours"

    # A warning is reported without blocking.
    late = copy.deepcopy(record)
    late["Guard"][0].update(in_time="09:00", out_time="19:00", hours=10)
    saved = await client.post(f"{AGENT}/documents/{doc}/record", headers=agent(tpl), json={"data": late, "decision": "COMMIT", "confidence": 0.95})
    assert saved.status_code == 200, saved.text
    assert saved.json()["validation"]["valid"] and saved.json()["validation"]["warnings"][0]["rule"] == "Day shifts start early"


async def test_a_template_change_breaks_rules_that_no_longer_fit_and_brings_them_back(client):
    tpl = await create_template(client)
    rule = (await client.post(f"{API}/rules", headers=ws(tpl), json=SHIFT_LIMIT)).json()

    without_hours = copy.deepcopy(SCHEMA)
    without_hours["objects"][1]["fields"] = [f for f in without_hours["objects"][1]["fields"] if f["name"] != "hours"]
    without_hours["validation_rules"] = []
    v2 = (await client.post(f"{API}/templates/{tpl}/versions", headers=ws(tpl), json={"schema_definition": without_hours, "version": "2"})).json()
    assert (await client.post(f"{API}/templates/{tpl}/versions/{v2['id']}/promote", headers=ws(tpl), json={"target_status": "ACTIVE"})).status_code == 200
    broken = (await client.get(f"{API}/rules/{rule['id']}", headers=ws(tpl))).json()
    assert broken["status"] == "broken" and "hours" in broken["broken_reason"]
    # A broken rule is not run.
    record = {"Roster": [{"site_id": "S12", "date": "2026-10-01"}], "Guard": [{"employee_id": "1", "name": "A", "in_time": "06:00", "out_time": "23:00"}]}
    assert (await client.post(f"{AGENT}/records/validate", headers=agent(tpl), json={"data": record})).json()["rules"] == []
    notes = (await client.get(f"{API}/notifications", headers=ws(tpl))).json()
    assert any(n["type"] == "rule.broken" for n in (notes.get("items", notes) if isinstance(notes, dict) else notes))

    v3 = (await client.post(f"{API}/templates/{tpl}/versions", headers=ws(tpl), json={"schema_definition": SCHEMA, "version": "3"})).json()
    await client.post(f"{API}/templates/{tpl}/versions/{v3['id']}/promote", headers=ws(tpl), json={"target_status": "ACTIVE"})
    assert (await client.get(f"{API}/rules/{rule['id']}", headers=ws(tpl))).json()["status"] == "active"


async def test_viewers_see_rules_and_only_publishers_change_them(client):
    tpl = await create_template(client)
    rule = (await client.post(f"{API}/rules", headers=ws(tpl), json=SHIFT_LIMIT)).json()
    viewer = person("usr_rules_viewer", "Vera Viewer")
    await join(client, tpl, viewer, "viewer")
    as_viewer = {**ws(tpl), **viewer}
    assert (await client.get(f"{API}/rules", headers=as_viewer)).status_code == 200
    assert (await client.post(f"{API}/rules/check", headers=as_viewer, json={"scope": "Guard", "check": "hours > 0"})).status_code == 200
    for method, path, body in [("post", "", SHIFT_LIMIT), ("patch", f"/{rule['id']}", {"name": "x"}), ("delete", f"/{rule['id']}", None),
                               ("post", f"/{rule['id']}/accept", None), ("post", f"/{rule['id']}/dismiss", None)]:
        res = await getattr(client, method)(f"{API}/rules{path}", headers=as_viewer, **({"json": body} if body else {}))
        assert res.status_code == 403, (method, path, res.text)
    outsider = person("usr_rules_out", "Olga Outside")
    await register(outsider)
    assert (await client.get(f"{API}/rules", headers={**ws(tpl), **outsider})).status_code == 403


async def test_the_agent_only_suggests_rules_and_a_person_adds_them(client):
    tpl = await create_template(client)
    await commit(client, tpl, "2026-10-01", [("75599", "Perera", "07:00", "19:00", 12)])

    checked = (await client.post(f"{AGENT}/rules/check", headers=agent(tpl), json={"scope": "Guard", "check": "hours == hours_between(in_time, out_time)"})).json()
    assert checked["ok"] and checked["dry_run"]["passed"] == 1
    bad = (await client.post(f"{AGENT}/rules/check", headers=agent(tpl), json={"scope": "Guard", "check": "__import__('os')"})).json()
    assert bad["ok"] is False and bad["problems"][0]["code"] == "unknown_function"

    made = await client.post(f"{AGENT}/rules/suggest", headers=agent(tpl), json={
        "name": "Hours match the times", "scope": "Guard", "check": "hours == hours_between(in_time, out_time)",
        "rationale": "On every roster so far the hours column is the time between in and out."})
    assert made.status_code == 201, made.text
    suggestion = made.json()
    assert suggestion["status"] == "suggested" and suggestion["created_by_kind"] == "agent"
    # A suggestion is not run until a person adds it.
    assert (await client.post(f"{AGENT}/records/validate", headers=agent(tpl), json={"data": {"Roster": [], "Guard": []}})).json()["rules"] == []
    overview = (await client.get(f"{AGENT}/workspace", headers=agent(tpl))).json()
    assert overview["rules"][0]["status"] == "suggested"
    notes = (await client.get(f"{API}/notifications", headers=ws(tpl))).json()
    assert any(n["type"] == "agent.rule" for n in (notes.get("items", notes) if isinstance(notes, dict) else notes))

    assert (await client.post(f"{API}/rules/{suggestion['id']}/accept", headers=ws(tpl))).json()["status"] == "active"
    assert (await client.post(f"{API}/rules/{suggestion['id']}/accept", headers=ws(tpl))).status_code == 409
    assert len((await client.post(f"{AGENT}/records/validate", headers=agent(tpl), json={"data": {"Roster": [], "Guard": []}})).json()["rules"]) == 1

    # In a person's chat it is still a suggestion, tied to that chat, for them to add with one click.
    editor = person("usr_rules_editor", "Eddie Editor")
    await join(client, tpl, editor, "editor")
    token = await chat_token(client, tpl, editor)
    asked = (await client.post(f"{AGENT}/rules/suggest", headers=token, json={
        "name": "Short shifts", "scope": "Guard", "check": "hours <= 14", "rationale": "Eddie asked for it."})).json()
    assert asked["status"] == "suggested" and asked["source_conversation_id"]
    assert (await client.post(f"{API}/rules/{asked['id']}/dismiss", headers=ws(tpl))).status_code == 204


async def test_rule_ideas_are_thought_of_once_per_template_version(client):
    from app.core.agent_token import mint_agent_token
    from tests.conftest import outbox_events
    tpl = await create_template(client)
    viewer = person("usr_ideas_viewer", "Vera Viewer")
    await join(client, tpl, viewer, "viewer")
    # Someone who can only look doesn't wake Syruss.
    assert (await client.post(f"{API}/rules/ideas", headers={**ws(tpl), **viewer})).json() == {"status": "none", "ideas": []}
    assert await outbox_events(tpl, "rules.ideas") == []

    # The first look by someone who writes rules asks Syruss; looking again while it thinks asks nothing more.
    assert (await client.post(f"{API}/rules/ideas", headers=ws(tpl))).json()["status"] == "thinking"
    assert (await client.post(f"{API}/rules/ideas", headers=ws(tpl))).json()["status"] == "thinking"
    [event] = await outbox_events(tpl, "rules.ideas")

    # Only that run may give ideas; they are cleaned and kept for the version it looked at.
    assert (await client.post(f"{AGENT}/rules/ideas", headers=agent(tpl), json={"ideas": ["x"]})).status_code == 403
    token = {"Authorization": f"Bearer {mint_agent_token(TENANT, tpl, event.id)}"}
    given = await client.post(f"{AGENT}/rules/ideas", headers=token, json={"ideas": ["  Values  never repeat ", "values never repeat", "A total matches its parts"]})
    assert given.status_code == 200, given.text
    ready = (await client.post(f"{API}/rules/ideas", headers={**ws(tpl), **viewer})).json()
    assert ready == {"status": "ready", "ideas": [{"text": "Values never repeat"}, {"text": "A total matches its parts"}]}

    # A new template version asks again.
    v2 = (await client.post(f"{API}/templates/{tpl}/versions", headers=ws(tpl), json={"schema_definition": SCHEMA, "version": "2"})).json()
    await client.post(f"{API}/templates/{tpl}/versions/{v2['id']}/promote", headers=ws(tpl), json={"target_status": "ACTIVE"})
    assert (await client.post(f"{API}/rules/ideas", headers=ws(tpl))).json()["status"] == "thinking"
    assert len(await outbox_events(tpl, "rules.ideas")) == 2
