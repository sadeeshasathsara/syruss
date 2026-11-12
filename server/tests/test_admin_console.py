"""
The operator's console: the business in figures, customers worth a look, one page per organization and per person, notes,
and the platform's own switches, each of which really holds back what it says.
"""
from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy import select
from app.models.outbox import AgentEventOutbox
from app.services import platform_controls
from app.services.agent_events import agent_event_relay
from tests.conftest import AsyncSessionLocal, person, png_bytes, register
from tests.test_billing import organization, workspace
from tests.test_invoices import ADMIN_APP, API

AS_STAFF = ADMIN_APP


@pytest.fixture(autouse=True)
def fresh_controls():
    platform_controls.forget()
    yield
    platform_controls.forget()


async def everything_on(client):
    """Puts every switch back (tests share the database)."""
    await client.put(f"{API}/sysadmin/platform/maintenance", headers=AS_STAFF, json={"on": False, "message": "", "reason": "Back to normal"})
    for name in ("signups", "agent", "intake"):
        await client.put(f"{API}/sysadmin/platform/switches", headers=AS_STAFF, json={"name": name, "on": True, "reason": "Back to normal"})
    platform_controls.forget()


async def test_the_business_in_figures_and_a_day_at_a_time(client):
    org = await organization("figures")
    tpl = await workspace(client, org)
    await client.post(f"{API}/documents/upload", headers={**org, "X-Template-ID": tpl}, data={"process": "false"},
                      files={"file": ("p.png", png_bytes(), "image/png")})
    m = (await client.get(f"{API}/sysadmin/metrics", headers=AS_STAFF)).json()
    assert m["organizations"]["active"] >= 1 and "tenant_figures" and m["organizations"]["active_7d"] >= 1
    for series in ("organizations", "people", "documents", "pages", "agent_cost_usd"):
        assert len(m["series"][series]) == 30
    assert m["series"]["documents"][-1]["day"] == datetime.now(timezone.utc).date().isoformat() and m["series"]["documents"][-1]["value"] >= 1
    # The everyday app's sign-in doesn't reach any of this.
    assert (await client.get(f"{API}/sysadmin/metrics")).status_code == 403


async def test_one_organization_on_one_page_with_notes_only_staff_see(client):
    org = await organization("profiled")
    await workspace(client, org, "Claims")
    page = (await client.get(f"{API}/sysadmin/organizations/tenant_profiled/profile", headers=AS_STAFF)).json()
    assert page["organization"]["slug"] == "profiled" and page["plan"]["key"] == "free"
    assert [w["name"] for w in page["workspaces"]] == ["Claims"] and page["members"][0]["role"] == "owner"
    assert len(page["usage"]["documents_30d"]) == 30 and page["money"]["open_invoices"] == 0

    note = await client.post(f"{API}/sysadmin/organizations/tenant_profiled/notes", headers=AS_STAFF, json={"body": "Called about the renewal."})
    assert note.status_code == 201
    pinned = await client.post(f"{API}/sysadmin/organizations/tenant_profiled/notes/{note.json()['id']}/pin", headers=AS_STAFF)
    assert pinned.json()["pinned"] is True
    assert (await client.get(f"{API}/sysadmin/organizations/tenant_profiled/profile", headers=AS_STAFF)).json()["notes"][0]["body"] == "Called about the renewal."
    assert (await client.delete(f"{API}/sysadmin/organizations/tenant_profiled/notes/{note.json()['id']}", headers=AS_STAFF)).status_code == 204
    assert (await client.get(f"{API}/sysadmin/organizations/nope/profile", headers=AS_STAFF)).status_code == 404


async def test_one_person_on_one_page_and_their_browsers_can_be_forgotten(client):
    someone = person("usr_console_person", "Cora Console")
    await register(someone)
    page = (await client.get(f"{API}/sysadmin/people/usr_console_person/profile", headers=AS_STAFF)).json()
    assert page["person"]["email"] == "usr_console_person@example.com" and page["organizations"][0]["role"] == "member"
    gone = await client.post(f"{API}/sysadmin/people/usr_console_person/forget-browsers", headers=AS_STAFF, json={"reason": "Reported a lost laptop"})
    assert gone.status_code == 200
    events = (await client.get(f"{API}/sysadmin/people/usr_console_person/profile", headers=AS_STAFF)).json()["security_events"]
    assert events[0]["kind"] == "devices_removed"


async def test_customers_worth_a_look_come_with_their_reasons(client):
    org = await organization("struggling")
    tpl = await workspace(client, org)
    async with AsyncSessionLocal() as db:
        from app.models.document import Document, DocumentStatus
        for i in range(10):
            res = await client.post(f"{API}/documents/upload", headers={**org, "X-Template-ID": tpl}, data={"process": "false"},
                                    files={"file": (f"{i}.png", png_bytes(), "image/png")})
            doc = await db.get(Document, res.json()["id"])
            if i < 4:
                doc.status = DocumentStatus.FAILED
        await db.commit()
    risky = {r["tenant_id"]: r for r in (await client.get(f"{API}/sysadmin/organizations/at-risk", headers=AS_STAFF)).json()}
    assert "failures" in [x["code"] for x in risky["tenant_struggling"]["reasons"]]


async def test_maintenance_makes_the_platform_read_only_but_staff_and_sign_in_keep_working(client):
    org = await organization("readonly")
    tpl = await workspace(client, org)
    try:
        on = await client.put(f"{API}/sysadmin/platform/maintenance", headers=AS_STAFF,
                              json={"on": True, "message": "Upgrading the database until 14:00 UTC.", "reason": "Planned upgrade"})
        assert on.status_code == 200
        held = await client.post(f"{API}/documents/upload", headers={**org, "X-Template-ID": tpl}, files={"file": ("p.png", png_bytes(), "image/png")})
        assert held.status_code == 503 and held.json()["detail"]["code"] == "maintenance" and "14:00" in held.json()["detail"]["message"]
        assert (await client.get(f"{API}/workspaces", headers=org)).status_code == 200                    # looking still works
        told = (await client.get(f"{API}/announcements", headers=org)).json()
        assert told["maintenance"]["message"].startswith("Upgrading")
        assert (await client.post(f"{API}/sysadmin/organizations/tenant_readonly/notes", headers=AS_STAFF, json={"body": "During maintenance"})).status_code == 201
    finally:
        await everything_on(client)
    assert (await client.post(f"{API}/documents/upload", headers={**org, "X-Template-ID": tpl}, data={"process": "false"},
                              files={"file": ("p.png", png_bytes(), "image/png")})).status_code == 201


async def test_switches_hold_sign_ups_intake_and_the_agent(client):
    newcomer = person("usr_console_new", "Nia New")
    try:
        await client.put(f"{API}/sysadmin/platform/switches", headers=AS_STAFF, json={"name": "signups", "on": False, "reason": "Spam wave"})
        refused = await client.post(f"{API}/onboarding", headers=newcomer, json={"first_name": "Nia", "last_name": "New"})
        assert refused.status_code == 503 and refused.json()["detail"]["code"] == "signups_paused"

        await client.put(f"{API}/sysadmin/platform/switches", headers=AS_STAFF, json={"name": "agent", "on": False, "reason": "Model outage"})
        org = await organization("agentless")
        tpl = await workspace(client, org)
        async with AsyncSessionLocal() as db:
            db.add(AgentEventOutbox(event_type="document.submitted", tenant_id="tenant_agentless", template_id=tpl, payload={}, actor={}))
            await db.commit()
        agent_event_relay._producer = object()  # "connected": it would publish if it were allowed to
        try:
            assert await agent_event_relay.publish_pending() == 0
        finally:
            agent_event_relay._producer = None
        async with AsyncSessionLocal() as db:
            waiting = (await db.execute(select(AgentEventOutbox).where(AgentEventOutbox.template_id == tpl))).scalars().all()
            assert waiting and all(e.published_at is None for e in waiting)            # nothing lost: it waits in order
    finally:
        await everything_on(client)


async def test_the_agent_can_be_paused_for_one_organization(client):
    org = await organization("paused")
    await workspace(client, org)
    res = await client.post(f"{API}/sysadmin/organizations/tenant_paused/agent", headers=AS_STAFF, json={"paused": True, "reason": "Runaway costs"})
    assert res.json() == {"agent_paused": True}
    platform_controls.forget()
    assert (await platform_controls.current()).agent_runs_for("tenant_paused") is False
    assert (await platform_controls.current()).agent_runs_for("tenant_default") is True
    shown = (await client.get(f"{API}/sysadmin/platform", headers=AS_STAFF)).json()
    assert {"id": "tenant_paused", "name": "Paused"} in shown["agent_paused_orgs"]
    await client.post(f"{API}/sysadmin/organizations/tenant_paused/agent", headers=AS_STAFF, json={"paused": False, "reason": "Fixed the loop"})


async def test_announcements_reach_their_audience_between_their_times(client):
    org, other = await organization("hearing"), await organization("elsewhere")
    soon = datetime.now(timezone.utc) + timedelta(hours=2)
    everyone = (await client.post(f"{API}/sysadmin/announcements", headers=AS_STAFF, json={
        "title": "New: PDF documents", "body": "Send PDFs anywhere you send pictures.", "reason": "Release note"})).json()
    just_one = (await client.post(f"{API}/sysadmin/announcements", headers=AS_STAFF, json={
        "title": "Your renewal", "severity": "warning", "tenant_id": "tenant_hearing", "reason": "Account manager"})).json()
    later = (await client.post(f"{API}/sysadmin/announcements", headers=AS_STAFF, json={
        "title": "Maintenance tonight", "starts_at": soon.isoformat(), "reason": "Planned"})).json()
    assert later["state"] == "scheduled"
    mine = [a["title"] for a in (await client.get(f"{API}/announcements", headers=org)).json()["items"]]
    theirs = [a["title"] for a in (await client.get(f"{API}/announcements", headers=other)).json()["items"]]
    assert mine[0] == "Your renewal" and "New: PDF documents" in mine and "Maintenance tonight" not in mine
    assert "Your renewal" not in theirs and "New: PDF documents" in theirs
    for a in (everyone, just_one, later):
        assert (await client.post(f"{API}/sysadmin/announcements/{a['id']}/end", headers=AS_STAFF)).json()["state"] == "ended"
    assert (await client.get(f"{API}/announcements", headers=org)).json()["items"] == [] or all(
        a["title"] not in ("New: PDF documents", "Your renewal") for a in (await client.get(f"{API}/announcements", headers=org)).json()["items"])


async def test_one_search_box_and_the_security_view(client):
    await organization("searchable")
    found = (await client.get(f"{API}/sysadmin/search", headers=AS_STAFF, params={"q": "searchab"})).json()
    assert [o["id"] for o in found["organizations"]] == ["tenant_searchable"]
    assert (await client.get(f"{API}/sysadmin/search", headers=AS_STAFF, params={"q": "x"})).status_code == 422
    view = (await client.get(f"{API}/sysadmin/security", headers=AS_STAFF)).json()
    assert set(view) == {"last_7_days", "wrong_code_bursts", "open_access", "staff_review"}
    assert any(s["user_id"] == "usr_admin_default" and s["role"] == "owner" for s in view["staff_review"])
