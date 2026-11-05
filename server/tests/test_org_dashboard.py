"""
The organization dashboard: an overview anyone in the organization can read, an activity page for those who may audit,
and closing the organization (owners only, by typing its name).
"""
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, update
from app.models.document import Document, DocumentStatus
from app.models.tenant import Tenant
from app.models.trace import AgentExecutionTrace
from tests.conftest import TENANT, AsyncSessionLocal, create_template, person, register, upload

API = "/api/v1"


async def test_the_overview_counts_the_organization_and_hides_what_a_role_may_not_see(client):
    await create_template(client, "Overview rosters")
    boss = await client.get(f"{API}/organization/overview")
    assert boss.status_code == 200
    data = boss.json()
    assert data["organization"]["slug"] == "default" and data["workspaces"]["total"] >= 1
    assert data["people"]["total"] >= 1 and data["pending_invitations"] is not None and data["activity"] is not None

    worker = person("usr_dash_worker", "Wen Worker")
    await register(worker, "member")
    seen = (await client.get(f"{API}/organization/overview", headers=worker)).json()
    assert seen["pending_invitations"] is None and seen["activity"] is None  # a member sees the shape, not the management
    assert seen["workspaces"]["mine"] == 0


async def test_the_overview_reads_the_period_against_the_one_before_and_each_workspaces_health(client):
    tpl, unpriced = await create_template(client, "Overview health"), await create_template(client, "Overview unpriced")
    saved, failed, old = await upload(client, tpl), await upload(client, tpl), await upload(client, tpl)
    async with AsyncSessionLocal() as db:
        await db.execute(update(Document).where(Document.id == saved).values(status=DocumentStatus.COMPLETED))
        await db.execute(update(Document).where(Document.id == failed).values(status=DocumentStatus.FAILED))
        await db.execute(update(Document).where(Document.id == old).values(created_at=datetime.now(timezone.utc) - timedelta(days=10)))
        db.add(AgentExecutionTrace(tenant_id=TENANT, template_id=tpl, profile="document.submitted", model="m", cost_usd=0.25))
        # A model without a price records tokens but no cost: that reads as unknown, never as free.
        db.add(AgentExecutionTrace(tenant_id=TENANT, template_id=unpriced, profile="document.submitted", model="m", input_tokens=900, output_tokens=100))
        await db.commit()

    assert (await client.get(f"{API}/organization/overview", params={"days": 5})).status_code == 422
    data = (await client.get(f"{API}/organization/overview", params={"days": 7})).json()
    assert len(data["period"]["series"]) == 7 and data["period"]["current"]["cost_usd"] is not None
    [row] = [w for w in data["workspace_health"] if w["id"] == tpl]
    assert (row["received"], row["saved"], row["failed"], row["failed_now"]) == (2, 1, 1, 1)   # the older one is last period's
    assert row["hands_free_rate"] == 1.0 and row["cost_usd"] == 0.25 and row["documents_this_month"] >= 2
    [dark] = [w for w in data["workspace_health"] if w["id"] == unpriced]
    assert dark["cost_usd"] is None and dark["tokens"] == 1000 and data["costs"]
    assert any(n["slug"] == row["slug"] and n["failed"] == 1 for n in data["needs_you"])
    assert data["setup"]["workspace"] and data["setup"]["documents"]
    assert any(w["workspace"] == "Overview health" for w in data["latest_work"])

    # A member of the organization outside every workspace sees no workspace's figures, and no costs.
    member = person("usr_dash_counts", "Cal Counts")
    await register(member, "member")
    seen = (await client.get(f"{API}/organization/overview", headers=member)).json()
    assert seen["workspace_health"] == [] and seen["latest_work"] == [] and not seen["costs"] and seen["period"]["current"]["tokens"] is None


async def test_the_activity_page_tells_who_did_what_a_page_at_a_time(client):
    await client.patch(f"{API}/organization", json={"name": "Renamed Co"})
    await client.post(f"{API}/organization/invitations", json={"email": "newbie@example.com", "role": "admin"})
    assert (await client.post(f"{API}/workspaces", json={"name": "Activity rosters"})).status_code == 201

    page = (await client.get(f"{API}/organization/activity", params={"limit": 2})).json()
    assert len(page["items"]) == 2 and page["next_before"]
    texts = " ".join(i["text"] for i in (await client.get(f"{API}/organization/activity", params={"limit": 20})).json()["items"])
    assert "renamed the organization" in texts and "invited newbie@example.com as Admin" in texts and "created the workspace Activity rosters" in texts
    older = (await client.get(f"{API}/organization/activity", params={"limit": 2, "before": page["next_before"]})).json()
    assert older["items"] and older["items"][0]["id"] != page["items"][0]["id"]

    worker = person("usr_dash_member", "Mia Member")
    await register(worker, "member")
    assert (await client.get(f"{API}/organization/activity", headers=worker)).status_code == 403
    await client.patch(f"{API}/organization", json={"name": "Test Org"})  # put the name back for other tests


async def test_closing_the_organization_needs_an_owner_and_its_exact_name(client):
    async with AsyncSessionLocal() as db:
        db.add(Tenant(id="tenant_to_close", name="Close Me Ltd", slug="close-me", is_active=True))
        await db.commit()
    from app.models.identity import OrgMember
    boss = {"X-Org-Slug": "close-me"}
    async with AsyncSessionLocal() as db:
        db.add(OrgMember(tenant_id="tenant_to_close", user_id="usr_admin_default", role="owner"))
        await db.commit()
    admin = person("usr_dash_admin", "Ann Admin")
    await register(admin, None)
    async with AsyncSessionLocal() as db:
        db.add(OrgMember(tenant_id="tenant_to_close", user_id="usr_dash_admin", role="admin"))
        await db.commit()

    assert (await client.request("DELETE", f"{API}/organization", headers={**admin, **boss}, json={"confirm": "Close Me Ltd"})).status_code == 403
    assert (await client.request("DELETE", f"{API}/organization", headers=boss, json={"confirm": "close me"})).status_code == 422
    done = await client.request("DELETE", f"{API}/organization", headers=boss, json={"confirm": "Close Me Ltd"})
    assert done.status_code == 204
    assert (await client.get(f"{API}/organization/overview", headers=boss)).status_code == 403  # it no longer opens
    async with AsyncSessionLocal() as db:
        assert (await db.get(Tenant, "tenant_to_close")).is_active is False  # closed, not erased
