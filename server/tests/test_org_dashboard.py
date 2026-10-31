"""
The organization dashboard: an overview anyone in the organization can read, an activity page for those who may audit,
and closing the organization (owners only, by typing its name).
"""
from sqlalchemy import select
from app.models.tenant import Tenant
from tests.conftest import AsyncSessionLocal, create_template, person, register

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
