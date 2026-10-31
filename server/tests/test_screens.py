"""
Custom screens: the formula language (safe, forgiving), the engine that runs a screen over saved records (typed values,
calculated columns, filters, grouping, totals), and the API with who may build and who may only look.
"""
import pytest
from app.core.agent_token import mint_agent_token
from tests.conftest import SCHEMA, TENANT, agent, create_template, join, outbox_events, person, register, upload, ws
from app.services import formula, screen_engine
from app.services.formula import Blank, FormulaError, compile_formula, evaluate
from app.services.screen_engine import Loaded, ScreenDefinition, run_rows, validate_definition

API = "/api/v1"
AGENT = "/internal/agent/v1"


# --- formulas ---------------------------------------------------------------------------

def test_formulas_calculate_the_way_a_spreadsheet_user_expects():
    assert evaluate("round(hours_between(a, b) - 8, 1)", {"a": "19:00", "b": "07:30"}) == 4.5   # past midnight
    assert evaluate("amount * 0.15", {"amount": "200"}) == 30
    assert evaluate("'Late' if days_between(due, paid) > 0 else 'On time'", {"due": "2026-03-01", "paid": "2026-03-05"}) == "Late"
    assert evaluate("coalesce(a, b, 'none')", {"a": None, "b": ""}) == "none"
    assert evaluate("concat(upper(first), ' ', last)", {"first": "ann", "last": "Lee"}) == "ANN Lee"
    assert evaluate("min(a, b, c)", {"a": 4, "b": None, "c": "2"}) == 2
    assert evaluate("weekday(d)", {"d": "2026-10-28"}) == "Wed"
    assert evaluate("contains(note, 'LATE')", {"note": "arrived late"}) is True


def test_blank_cells_stay_blank_and_bad_input_is_blank_not_a_crash():
    assert evaluate("a + 1", {"a": None}) is None
    assert evaluate("a > 3", {"a": None}) is False
    with pytest.raises(Blank):
        evaluate("a / b", {"a": 1, "b": 0})
    with pytest.raises(Blank):
        evaluate("a * 2", {"a": "twelve"})
    with pytest.raises(Blank):
        evaluate("2 ** 999", {})


@pytest.mark.parametrize("bad", [
    "__import__('os').system('x')", "open('f')", "a.__class__", "a[0]", "[x for x in a]", "lambda: 1", "a := 1",
    "exec('1')", "(1).real", "'a'.upper()", "min(key=abs)", "f'{a}'", "a if b",
])
def test_a_formula_cannot_reach_anything_but_its_values(bad):
    with pytest.raises(FormulaError):
        compile_formula(bad, {"a", "b"})


def test_formulas_are_bounded_and_say_what_is_wrong():
    with pytest.raises(FormulaError, match="too long"):
        compile_formula("1+" * 300 + "1")
    with pytest.raises(FormulaError, match="Unknown column 'nope'"):
        compile_formula("nope + 1", {"hours"})
    with pytest.raises(FormulaError, match="empty"):
        compile_formula("  ")
    assert compile_formula("hours * rate", {"hours", "rate"}).names == frozenset({"hours", "rate"})


# --- the engine -------------------------------------------------------------------------

def rows():
    mk = lambda doc, site, day, name, i, o, h: Loaded(
        doc, f"{doc}.jpg", "COMPLETED",
        {"employee_id": name[:3].upper(), "name": name, "in_time": i, "out_time": o, "hours": h}, {"Roster.site_id": site, "Roster.date": day})
    return [
        mk("d1", "S12", "2026-10-01", "Perera", "07:00", "19:30", "12.5"),
        mk("d1", "S12", "2026-10-01", "Silva", "19:00", "07:00", "12"),
        mk("d2", "S14", "2026-10-02", "Perera", "07:00", "15:00", "8"),
        mk("d2", "S14", "2026-10-02", "Fernando", "07:00", "bad", ""),
    ]


def definition(**over):
    base = {
        "source": {"object": "Guard"},
        "columns": [
            {"id": "name", "label": "Guard", "field": "name"},
            {"id": "site", "label": "Site", "field": "Roster.site_id"},
            {"id": "hours", "label": "Hours", "field": "hours", "format": {"type": "number", "decimals": 1}, "aggregate": "sum"},
            {"id": "overtime", "label": "Overtime", "kind": "formula", "formula": "max(hours - 8, 0)", "aggregate": "sum",
             "highlight": [{"when": "overtime > 3", "tone": "warning"}]},
            {"id": "worked", "label": "Worked", "kind": "formula", "formula": "hours_between(in_time, out_time)", "hidden": True},
        ],
    }
    base.update(over)
    return ScreenDefinition.model_validate(base)


def test_a_screen_lists_rows_with_typed_values_and_calculated_columns():
    out = run_rows(rows(), SCHEMA, definition())
    assert [c["id"] for c in out["columns"]] == ["name", "site", "hours", "overtime"]      # the hidden one is not shown
    first = out["rows"][0]["cells"]
    assert first == {"name": "Perera", "site": "S12", "hours": 12.5, "overtime": 4.5}
    assert out["rows"][0]["tones"] == {"overtime": "warning"} and out["rows"][0]["document_id"] == "d1"
    # An empty number stays blank; a time that is not a time is blank in the calculation and counted, not fatal.
    assert out["rows"][3]["cells"]["hours"] is None and out["rows"][3]["cells"]["overtime"] == 0   # like a spreadsheet, max ignores blanks
    assert out["errors"] == [{"column": "worked", "message": "'bad' is not a time", "count": 1}]


def test_filters_sort_and_totals():
    d = definition(filters=[{"field": "site", "op": "eq", "value": "s12"}], sort=[{"column": "hours", "dir": "desc"}], totals=True)
    out = run_rows(rows(), SCHEMA, d)
    assert [r["cells"]["name"] for r in out["rows"]] == ["Perera", "Silva"] and out["total_rows"] == 2
    assert out["totals"] == {"hours": 24.5, "overtime": 8.5}
    between = run_rows(rows(), SCHEMA, definition(filters=[{"field": "hours", "op": "between", "value": [8, 12]}]))
    assert sorted(r["cells"]["name"] for r in between["rows"]) == ["Perera", "Silva"]
    formula_filter = run_rows(rows(), SCHEMA, definition(filters=[{"formula": "hours > 10 and contains(site, '12')"}]))
    assert len(formula_filter["rows"]) == 2
    # Blanks sort last, whichever way.
    asc = run_rows(rows(), SCHEMA, definition(sort=[{"column": "hours"}]))
    assert asc["rows"][-1]["cells"]["hours"] is None
    desc = run_rows(rows(), SCHEMA, definition(sort=[{"column": "hours", "dir": "desc"}]))
    assert desc["rows"][-1]["cells"]["hours"] is None


def test_grouping_totals_each_group_and_can_calculate_on_the_totals():
    d = definition(
        columns=[
            {"id": "name", "label": "Guard", "field": "name"},
            {"id": "hours", "label": "Hours", "field": "hours", "aggregate": "sum"},
            {"id": "shifts", "label": "Shifts", "field": "employee_id", "aggregate": "count"},
            {"id": "avg", "label": "Per shift", "kind": "formula", "scope": "group", "formula": "round(hours / shifts, 2)"},
        ],
        group_by=["name"], sort=[{"column": "hours", "dir": "desc"}], totals=True)
    out = run_rows(rows(), SCHEMA, d)
    by_name = {r["cells"]["name"]: r for r in out["rows"]}
    assert by_name["Perera"]["cells"] == {"name": "Perera", "hours": 20.5, "shifts": 2, "avg": 10.25} and by_name["Perera"]["size"] == 2
    assert out["grouped"] is True and out["total_rows"] == 3 and out["rows"][0]["cells"]["name"] == "Perera"
    assert out["totals"]["hours"] == 32.5 and out["totals"]["shifts"] == 4


def test_a_field_that_left_the_template_is_reported_not_fatal():
    d = definition(columns=[{"id": "gone", "label": "Gone", "field": "not_there"}, {"id": "name", "label": "Guard", "field": "name"}])
    out = run_rows(rows(), SCHEMA, d)
    assert out["rows"][0]["cells"] == {"gone": None, "name": "Perera"}
    assert out["errors"] == [{"column": "gone", "message": "'not_there' is no longer in the template", "count": 4}]


def test_search_and_the_advanced_filters_narrow_what_is_shown():
    from app.services.screen_engine import Filter
    names = lambda out: sorted(r["cells"]["name"] for r in out["rows"])
    # The search box: every word must appear somewhere in the row, any column, any case, any order.
    assert names(run_rows(rows(), SCHEMA, definition(), search="perera s12")) == ["Perera"]
    assert names(run_rows(rows(), SCHEMA, definition(), search="  SILVA ")) == ["Silva"]
    assert run_rows(rows(), SCHEMA, definition(), search="nobody")["total_rows"] == 0
    assert names(run_rows(rows(), SCHEMA, definition(), search="d1.jpg")) == ["Perera", "Silva"]            # the document's name counts too
    # "is one of" and "is between", and a written condition, as extra filters for this look.
    one_of = run_rows(rows(), SCHEMA, definition(), extra_filters=[Filter(field="name", op="in", value=["silva", "FERNANDO"])])
    assert names(one_of) == ["Fernando", "Silva"]
    between = run_rows(rows(), SCHEMA, definition(), extra_filters=[Filter(field="hours", op="between", value=["9", "12.5"])])
    assert names(between) == ["Perera", "Silva"]
    written = run_rows(rows(), SCHEMA, definition(), extra_filters=[Filter(formula='hours >= 12 and not contains(site, "14")')])
    assert names(written) == ["Perera", "Silva"]
    broken = run_rows(rows(), SCHEMA, definition(), extra_filters=[Filter(formula="hours >")])
    assert broken["total_rows"] == 0 and broken["errors"][0]["column"] == "filter"                     # a condition that can't be read says so
    # On a grouped screen the search keeps the groups that mention it, and the totals follow.
    grouped = definition(columns=[{"id": "name", "label": "Guard", "field": "name"}, {"id": "hours", "label": "Hours", "field": "hours", "aggregate": "sum"}],
                         group_by=["name"], totals=True)
    out = run_rows(rows(), SCHEMA, grouped, search="perera")
    assert names(out) == ["Perera"] and out["totals"] == {"hours": 20.5} and out["total_rows"] == 1


def test_definitions_are_checked_against_the_template_with_sentences():
    def problems(**over):
        return validate_definition(definition(**over), SCHEMA)

    assert problems() == []
    assert "no object 'Nope'" in validate_definition(definition(source={"object": "Nope"}), SCHEMA)[0]
    assert any("'age'" in p and "not a field" in p for p in problems(columns=[{"id": "a", "label": "A", "field": "age"}]))
    assert any("Unknown column 'nothing'" in p for p in problems(columns=[
        {"id": "a", "label": "A", "field": "name"}, {"id": "b", "label": "B", "kind": "formula", "formula": "nothing + 1"}]))
    circle = problems(columns=[{"id": "a", "label": "A", "kind": "formula", "formula": "b + 1"},
                               {"id": "b", "label": "B", "kind": "formula", "formula": "a + 1"}])
    assert any("circle" in p for p in circle)
    assert any("two" in p.lower() or "Two columns" in p for p in problems(columns=[{"id": "a", "label": "A", "field": "name"}, {"id": "a", "label": "B", "field": "hours"}]))
    assert any("grouped" in p for p in problems(group_by=["name"]))      # the other columns need totals
    assert any("Sort" in p for p in problems(sort=[{"column": "zzz"}]))
    with pytest.raises(ValueError):                                       # a column named like a function
        ScreenDefinition.model_validate({"source": {"object": "Guard"}, "columns": [{"id": "min", "label": "Min", "field": "hours"}]})


# --- the API ----------------------------------------------------------------------------

async def commit(client, tpl, day, guards, site="S12"):
    doc = await upload(client, tpl)
    record = {"Roster": [{"site_id": site, "date": day}],
              "Guard": [{"employee_id": e, "name": n, "in_time": i, "out_time": o, "hours": h} for e, n, i, o, h in guards]}
    res = await client.post(f"{AGENT}/documents/{doc}/record", headers=agent(tpl), json={"data": record, "decision": "COMMIT", "confidence": 0.95})
    assert res.status_code == 200, res.text
    return doc


OVERTIME = {
    "source": {"object": "Guard"},
    "columns": [
        {"id": "name", "label": "Guard", "field": "name"},
        {"id": "hours", "label": "Hours", "field": "hours", "aggregate": "sum"},
        {"id": "overtime", "label": "Overtime", "kind": "formula", "formula": "max(hours - 8, 0)", "aggregate": "sum"},
    ],
    "totals": True,
}


async def test_building_and_running_a_screen_over_saved_records(client):
    tpl = await create_template(client)
    first = await commit(client, tpl, "2026-10-01", [("75599", "Perera", "07:00", "19:00", 12), ("61002", "Silva", "19:00", "07:00", 12)])
    await commit(client, tpl, "2026-10-02", [("75599", "Perera", "07:00", "15:00", 8)], site="S14")

    assert (await client.get(f"{API}/screens", headers=ws(tpl))).json() == []
    made = await client.post(f"{API}/screens", headers=ws(tpl), json={"name": "Overtime by guard", "description": "Who works long", "definition": OVERTIME})
    assert made.status_code == 201, made.text
    screen = made.json()
    assert screen["slug"] == "overtime-by-guard" and screen["created_by_kind"] == "person" and screen["columns"] == 3
    # The same name again is its own screen.
    assert (await client.post(f"{API}/screens", headers=ws(tpl), json={"name": "Overtime by guard", "definition": OVERTIME})).json()["slug"] == "overtime-by-guard-2"

    run = (await client.post(f"{API}/screens/overtime-by-guard/run", headers=ws(tpl), json={})).json()
    assert [r["cells"] for r in run["rows"]] == [
        {"name": "Perera", "hours": 8, "overtime": 0}, {"name": "Perera", "hours": 12, "overtime": 4}, {"name": "Silva", "hours": 12, "overtime": 4}]
    assert run["totals"] == {"hours": 32, "overtime": 8} and run["total_rows"] == 3 and run["documents_searched"] == 2
    assert run["rows"][0]["document_id"] and run["rows"][0]["filename"]
    # A look at it now (extra filter and order) does not change the saved screen.
    only = (await client.post(f"{API}/screens/overtime-by-guard/run", headers=ws(tpl), json={
        "filters": [{"field": "name", "op": "eq", "value": "silva"}], "sort": [{"column": "hours", "dir": "desc"}]})).json()
    assert [r["cells"]["name"] for r in only["rows"]] == ["Silva"]
    assert (await client.get(f"{API}/screens/overtime-by-guard", headers=ws(tpl))).json()["definition"].get("filters", []) == []

    # The builder previews an unsaved definition, and gets sentences for what is wrong.
    ok = await client.post(f"{API}/screens/preview", headers=ws(tpl), json={"definition": OVERTIME, "limit": 1})
    assert ok.status_code == 200 and len(ok.json()["rows"]) == 1 and ok.json()["total_rows"] == 3
    bad = await client.post(f"{API}/screens/preview", headers=ws(tpl), json={"definition": {**OVERTIME, "columns": [
        {"id": "x", "label": "X", "kind": "formula", "formula": "nothing * 2"}]}})
    assert bad.status_code == 422 and "Unknown column 'nothing'" in bad.json()["detail"]["problems"][0]

    csv = await client.get(f"{API}/screens/overtime-by-guard/export.csv", headers=ws(tpl))
    assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv")
    lines = csv.text.lstrip("﻿").strip().splitlines()
    assert lines[0] == "Guard,Hours,Overtime" and len(lines) == 5 and lines[-1] == "Total,32,8"
    cat = (await client.get(f"{API}/screens/catalogue", headers=ws(tpl))).json()
    assert {o["type"] for o in cat["objects"]} == {"Roster", "Guard"} and any(f["name"] == "hours_between" for f in cat["functions"])
    starters = (await client.get(f"{API}/screens/starters", headers=ws(tpl))).json()
    assert [s["name"] for s in starters] == ["All guard rows"] and starters[0]["definition"]["columns"][0]["field"] == "Roster.site_id"
    assert (await client.post(f"{API}/screens/preview", headers=ws(tpl), json={"definition": starters[0]["definition"]})).status_code == 200
    assert first  # saved records are what screens read


async def test_everyone_who_sees_documents_can_open_a_screen_but_only_managers_change_it(client):
    tpl = await create_template(client)
    await commit(client, tpl, "2026-10-01", [("75599", "Perera", "07:00", "19:00", 12)])
    viewer, editor = person("usr_screens_viewer", "Vera Viewer"), person("usr_screens_editor", "Eddie Editor")
    await join(client, tpl, viewer, "viewer")
    await join(client, tpl, editor, "editor")
    outsider = person("usr_screens_out", "Olga Outside")
    await register(outsider)

    as_ = lambda who: {**ws(tpl), **who}
    made = await client.post(f"{API}/screens", headers=as_(editor), json={"name": "Hours", "definition": OVERTIME})
    assert made.status_code == 201
    # The viewer reads, runs, exports and previews; every change is refused, with the reason.
    assert (await client.get(f"{API}/screens", headers=as_(viewer))).status_code == 200
    assert (await client.post(f"{API}/screens/hours/run", headers=as_(viewer), json={})).status_code == 200
    assert (await client.get(f"{API}/screens/hours/export.csv", headers=as_(viewer))).status_code == 200
    assert (await client.post(f"{API}/screens/preview", headers=as_(viewer), json={"definition": OVERTIME})).status_code == 200
    for method, path, body in [("post", "", {"name": "N", "definition": OVERTIME}), ("patch", "/hours", {"name": "Z"}),
                               ("delete", "/hours", None), ("post", "/hours/accept", None), ("post", "/hours/dismiss", None)]:
        res = await getattr(client, method)(f"{API}/screens{path}", headers=as_(viewer), **({"json": body} if body else {}))
        assert res.status_code == 403 and "analytics screens" in res.json()["detail"].lower(), (method, path, res.text)
    # Someone who is not in the workspace gets nothing.
    assert (await client.get(f"{API}/screens", headers=as_(outsider))).status_code == 403
    # The editor can change and delete it.
    assert (await client.patch(f"{API}/screens/hours", headers=as_(editor), json={"name": "Hours worked"})).json()["name"] == "Hours worked"
    assert (await client.delete(f"{API}/screens/hours", headers=as_(editor))).status_code == 204
    assert (await client.get(f"{API}/screens/hours", headers=as_(editor))).status_code == 404


async def test_a_workspace_has_a_limited_number_of_screens_and_a_suggestion_is_accepted_or_dismissed(client):
    from app.core.database import AsyncSessionLocal
    from app.models.screen import WorkspaceScreen
    tpl = await create_template(client)
    async with AsyncSessionLocal() as db:
        db.add(WorkspaceScreen(tenant_id="tenant_default", template_id=tpl, slug="idea", name="An idea", definition=OVERTIME, status="suggested",
                               created_by="agent", created_by_kind="agent", rationale="Overtime is worth watching"))
        await db.commit()
    listed = (await client.get(f"{API}/screens", headers=ws(tpl))).json()
    assert listed[0]["status"] == "suggested" and listed[0]["rationale"] == "Overtime is worth watching"
    assert (await client.post(f"{API}/screens/idea/accept", headers=ws(tpl))).json()["status"] == "active"
    assert (await client.post(f"{API}/screens/idea/dismiss", headers=ws(tpl))).status_code == 204
    assert (await client.get(f"{API}/screens", headers=ws(tpl))).json() == []


# --- the agent ---------------------------------------------------------------------------

async def chat_token(client, tpl, who):
    chat = (await client.post(f"{API}/conversations", headers={**ws(tpl), **who}, json={"title": "t"})).json()
    await client.post(f"{API}/conversations/{chat['id']}/messages?wait=false", headers={**ws(tpl), **who}, json={"content": "make me a screen"})
    event = [e for e in await outbox_events(tpl, "human.message") if e.payload["conversation_id"] == chat["id"]][0]
    return {"Authorization": f"Bearer {mint_agent_token(TENANT, tpl, event.id, actor_id=event.actor['id'])}"}


async def test_the_agent_builds_and_answers_from_screens_for_the_person_it_is_chatting_with(client):
    tpl = await create_template(client)
    await commit(client, tpl, "2026-10-01", [("75599", "Perera", "07:00", "19:00", 12), ("61002", "Silva", "19:00", "07:00", 12)])
    editor, viewer = person("usr_agent_screens_editor", "Eddie"), person("usr_agent_screens_viewer", "Vic")
    await join(client, tpl, editor, "editor")
    await join(client, tpl, viewer, "viewer")
    as_editor = await chat_token(client, tpl, editor)
    as_viewer = await chat_token(client, tpl, viewer)

    # Both can look: the catalogue, a trial of a screen (nothing is saved), and the list.
    cat = (await client.get(f"{AGENT}/screens/catalogue", headers=as_viewer)).json()
    assert {o["type"] for o in cat["objects"]} == {"Roster", "Guard"}
    trial = await client.post(f"{AGENT}/screens/preview", headers=as_viewer, json={"definition": OVERTIME})
    assert trial.status_code == 200 and trial.json()["totals"] == {"hours": 24, "overtime": 8}
    assert (await client.get(f"{AGENT}/screens", headers=as_viewer)).json() == []
    bad = await client.post(f"{AGENT}/screens/preview", headers=as_viewer, json={"definition": {**OVERTIME, "filters": [{"field": "nope", "op": "eq", "value": "x"}]}})
    assert bad.status_code == 422 and "nope" in bad.json()["detail"]["problems"][0]

    # A viewer's chat cannot save: the agent is bounded by the person.
    refused = await client.post(f"{AGENT}/screens", headers=as_viewer, json={"name": "Hours", "definition": OVERTIME})
    assert refused.status_code == 403 and "Viewer" in refused.json()["detail"]
    # An editor's chat can; the screen is theirs at once and says who made it.
    saved = await client.post(f"{AGENT}/screens", headers=as_editor, json={"name": "Hours by guard", "definition": OVERTIME, "rationale": "asked for"})
    assert saved.status_code == 201 and saved.json()["status"] == "active" and saved.json()["created_by_kind"] == "agent"
    # ...and it answers from it with extra filters, without changing it.
    answer = (await client.post(f"{AGENT}/screens/hours-by-guard/run", headers=as_viewer, json={"filters": [{"field": "name", "op": "eq", "value": "Silva"}]})).json()
    assert [r["cells"]["name"] for r in answer["rows"]] == ["Silva"] and answer["screen"]["slug"] == "hours-by-guard"
    changed = await client.patch(f"{AGENT}/screens/hours-by-guard", headers=as_editor, json={"name": "Hours and overtime"})
    assert changed.status_code == 200 and changed.json()["name"] == "Hours and overtime"
    assert (await client.patch(f"{AGENT}/screens/hours-by-guard", headers=as_viewer, json={"name": "x"})).status_code == 403
    assert (await client.delete(f"{AGENT}/screens/hours-by-guard", headers=as_editor)).status_code == 204


async def test_what_the_agent_thinks_of_by_itself_is_only_a_suggestion(client):
    tpl = await create_template(client)
    await commit(client, tpl, "2026-10-01", [("75599", "Perera", "07:00", "19:00", 12)])
    # A run nobody asked for (as when it reflects, or an insight run): its screen waits for a person.
    first = await client.post(f"{AGENT}/screens", headers=agent(tpl), json={
        "name": "Long shifts", "definition": OVERTIME, "rationale": "Three guards work past twelve hours a week."})
    assert first.status_code == 201 and first.json()["status"] == "suggested" and first.json()["rationale"].startswith("Three guards")
    # It cannot change or delete screens on its own, and only a few suggestions wait at once.
    assert (await client.patch(f"{AGENT}/screens/long-shifts", headers=agent(tpl), json={"name": "x"})).status_code == 403
    assert (await client.delete(f"{AGENT}/screens/long-shifts", headers=agent(tpl))).status_code == 403
    for n in range(2, 6):
        assert (await client.post(f"{AGENT}/screens", headers=agent(tpl), json={"name": f"Idea {n}", "definition": OVERTIME})).status_code == 201
    assert (await client.post(f"{AGENT}/screens", headers=agent(tpl), json={"name": "One too many", "definition": OVERTIME})).status_code == 409
    # People see them as suggestions, and keeping one makes it a screen.
    listed = (await client.get(f"{API}/screens", headers=ws(tpl))).json()
    assert {s["status"] for s in listed} == {"suggested"}
    assert (await client.post(f"{API}/screens/long-shifts/accept", headers=ws(tpl))).json()["status"] == "active"
    # Whoever made them, they are told: the owner has a notification about the suggestion.
    notes = (await client.get(f"{API}/notifications", headers=ws(tpl))).json()
    assert any(n["type"] == "agent.screen" for n in (notes.get("items", notes) if isinstance(notes, dict) else notes))


async def test_insights_wake_the_agent_to_suggest_screens_only_when_there_is_something_to_look_at(client):
    from datetime import datetime, timedelta, timezone
    from app.core.database import AsyncSessionLocal
    from app.models.template import DocumentTemplate
    from app.services import scheduler
    tpl = await create_template(client)
    await client.get(f"{API}/settings", headers=ws(tpl))
    # Off by default: nothing happens on a schedule. A person can still ask, but three records are needed to see a pattern.
    async with AsyncSessionLocal() as db:
        template = await db.get(DocumentTemplate, tpl)
        assert await scheduler.insights(db, template) is None
    few = await client.post(f"{API}/screens/insights/run", headers=ws(tpl))
    assert few.status_code == 200 and few.json()["status"] == "too_few_records"
    assert await outbox_events(tpl, "schedule.insights") == []

    for n in range(3):
        await commit(client, tpl, f"2026-10-0{n + 1}", [("75599", "Perera", "07:00", "19:00", 12)])
    asked = await client.post(f"{API}/screens/insights/run", headers=ws(tpl))
    assert asked.json()["status"] == "woke_agent" and asked.json()["records"] == 3
    [event] = await outbox_events(tpl, "schedule.insights")
    assert event.actor["origin"] == "user" and event.payload["records"] == 3

    # Turned on weekly: due a week later, and quiet when nothing new was read since.
    saved = await client.patch(f"{API}/settings", headers=ws(tpl), json={"agent": {"insights": "weekly"}})
    assert saved.status_code == 200, saved.text
    async with AsyncSessionLocal() as db:
        template = await db.get(DocumentTemplate, tpl)
        assert (await scheduler.insights(db, template))["status"] == "nothing_new"   # first look after turning it on: nothing new since the request
        await db.commit()
        assert await scheduler.insights(db, template) is None                         # and now not due for a week
        result = await scheduler.insights(db, template, now=datetime.now(timezone.utc) + timedelta(days=8))
        await db.commit()
    assert result["status"] == "nothing_new" and len(await outbox_events(tpl, "schedule.insights")) == 1
    # Only people who manage screens can ask.
    viewer = person("usr_insights_viewer", "Vera")
    await join(client, tpl, viewer, "viewer")
    assert (await client.post(f"{API}/screens/insights/run", headers={**ws(tpl), **viewer})).status_code == 403
