"""
Data that is no longer wanted leaves (a deleted workspace's files, a closed organization's content after its retention,
a person's account), and platform staff can shut an organization or an account quickly, with a reason.
"""
from datetime import datetime, timedelta, timezone
from sqlalchemy import func, select
from app.models.billing import Invoice
from app.models.document import Document
from app.models.identity import User
from app.models.tenant import Tenant
from app.services import data_lifecycle
from app.services.storage_service import storage_service
from tests.conftest import AsyncSessionLocal, person, png_bytes, register
from tests.test_billing import organization, workspace
from tests.test_invoices import ADMIN_APP, API, DETAILS, pack, seller_ready


async def send(client, org: dict, tpl: str) -> dict:
    res = await client.post(f"{API}/documents/upload", headers={**org, "X-Template-ID": tpl}, data={"process": "false"},
                            files={"file": ("page.png", png_bytes(), "image/png")})
    assert res.status_code == 201, res.text
    return res.json()


async def key_of(document_id: str) -> str:
    async with AsyncSessionLocal() as db:
        return (await db.get(Document, document_id)).minio_object_key


async def test_a_deleted_workspaces_files_leave_storage_with_it(client):
    org = await organization("tidy")
    keep, gone = await workspace(client, org, "Keep"), await workspace(client, org, "Gone")
    kept, removed = await send(client, org, keep), await send(client, org, gone)
    kept_key, removed_key = await key_of(kept["id"]), await key_of(removed["id"])
    slug = next(w["slug"] for w in (await client.get(f"{API}/workspaces", headers=org)).json() if w["id"] == gone)
    assert (await client.delete(f"{API}/workspaces/{slug}", headers=org)).status_code == 204
    assert storage_service.object_size(removed_key) is None and storage_service.object_size(kept_key) is not None


async def test_a_closed_organization_is_emptied_after_its_retention_and_its_invoices_stay(client):
    await seller_ready(client)
    org = await organization("leaving")
    tenant = "tenant_leaving"
    tpl = await workspace(client, org)
    doc = await send(client, org, tpl)
    key = await key_of(doc["id"])
    await client.put(f"{API}/billing/profile", headers=org, json=DETAILS)
    item = await pack(client, "pages-500-leaving")
    invoice = (await client.post(f"{API}/billing/purchases", headers=org, json={"item_id": item["id"]})).json()["invoice"]
    assert (await client.request("DELETE", f"{API}/organization", headers=org, json={"confirm": "Leaving"})).status_code == 204

    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as db:
        await data_lifecycle.purge_closed(db, now + timedelta(days=5))       # still inside the retention: nothing goes
    assert storage_service.object_size(key) is not None
    async with AsyncSessionLocal() as db:
        await data_lifecycle.purge_closed(db, now + timedelta(days=31))
    async with AsyncSessionLocal() as db:
        closed = await db.get(Tenant, tenant)
        assert closed.purged_at is not None and closed.status == "closed"
        assert (await db.execute(select(func.count(Document.id)).where(Document.tenant_id == tenant))).scalar() == 0
        assert (await db.get(Invoice, invoice["id"])).number == invoice["number"]   # financial records are kept
    assert storage_service.object_size(key) is None
    async with AsyncSessionLocal() as db:
        assert await data_lifecycle.purge_closed(db, now + timedelta(days=60)) == 0   # once


async def test_a_person_can_delete_their_account_unless_an_organization_would_be_left_without_an_owner(client, mailbox):
    org = await organization("shared")
    leaver = person("usr_leaver", "Lee Leaver")
    await register(leaver, org_role=None)
    async with AsyncSessionLocal() as db:
        from app.models.identity import OrgMember
        db.add(OrgMember(tenant_id="tenant_shared", user_id="usr_leaver", role="member"))
        db.add(Tenant(id="tenant_solo", name="Solo", slug="solo", is_active=True))
        await db.flush()
        db.add(OrgMember(tenant_id="tenant_solo", user_id="usr_leaver", role="owner"))
        await db.commit()
    refused = await client.delete(f"{API}/me", headers={**leaver, **org})
    assert refused.status_code == 409 and "only owner of Solo" in refused.json()["detail"]
    assert (await client.request("DELETE", f"{API}/organization", headers={**leaver, "X-Org-Slug": "solo"}, json={"confirm": "Solo"})).status_code == 204

    assert (await client.delete(f"{API}/me", headers={**leaver, **org})).status_code == 204
    async with AsyncSessionLocal() as db:
        row = await db.get(User, "usr_leaver")
        assert row.status == "deleted" and row.email.endswith("@deleted.invalid") and row.display_name == "Deleted account"
    assert (await client.get(f"{API}/me", headers={**leaver, **org})).status_code == 403   # nothing opens for them any more
    assert [m.to for m in mailbox if "remove its sign-in" in m.subject] in ([], ["billing@syruss.example"])
    members = (await client.get(f"{API}/organization/members", headers=org)).json()
    assert "usr_leaver" not in [m["user_id"] for m in members]


async def test_staff_can_suspend_an_organization_and_disable_an_account_with_a_reason(client):
    org = await organization("troubled")
    tpl = await workspace(client, org)
    found = (await client.get(f"{API}/sysadmin/organizations", headers=ADMIN_APP, params={"q": "troubl"})).json()
    assert [(o["id"], o["status"], o["workspaces"]) for o in found] == [("tenant_troubled", "active", 1)]

    assert (await client.post(f"{API}/sysadmin/organizations/tenant_troubled/suspend", headers=ADMIN_APP, json={"reason": "x"})).status_code == 422
    shut = await client.post(f"{API}/sysadmin/organizations/tenant_troubled/suspend", headers=ADMIN_APP, json={"reason": "Card fraud reported"})
    assert shut.status_code == 200 and shut.json()["status"] == "suspended"
    assert (await client.get(f"{API}/workspaces", headers=org)).status_code == 403          # shut for its own people
    back = await client.post(f"{API}/sysadmin/organizations/tenant_troubled/reactivate", headers=ADMIN_APP, json={"reason": "Sorted out with the owner"})
    assert back.json()["status"] == "active" and (await client.get(f"{API}/workspaces", headers=org)).status_code == 200
    said = [a["text"] for a in (await client.get(f"{API}/organization/activity", headers=org)).json()["items"]]
    assert any("suspended the organization" in t for t in said) and any("reactivated the organization" in t for t in said)

    someone = person("usr_nuisance", "Nat Nuisance")
    await register(someone)
    looked_up = (await client.get(f"{API}/sysadmin/people", headers=ADMIN_APP, params={"q": "nuisance"})).json()
    assert [p["id"] for p in looked_up] == ["usr_nuisance"] and looked_up[0]["organizations"][0]["role"] == "member"
    off = await client.post(f"{API}/sysadmin/people/usr_nuisance/disable", headers=ADMIN_APP, json={"reason": "Abusing the upload API"})
    assert off.status_code == 200 and off.json()["status"] == "disabled"
    assert (await client.get(f"{API}/workspaces", headers=someone)).status_code == 403
    on = await client.post(f"{API}/sysadmin/people/usr_nuisance/enable", headers=ADMIN_APP, json={"reason": "Apologised, key rotated"})
    assert on.json()["status"] == "active" and (await client.get(f"{API}/workspaces", headers=someone)).status_code == 200
    # Nobody disables themselves, and the everyday app's sign-in can't reach these switches.
    assert (await client.post(f"{API}/sysadmin/people/usr_admin_default/disable", headers=ADMIN_APP, json={"reason": "Testing myself"})).status_code == 409
    assert (await client.post(f"{API}/sysadmin/people/usr_nuisance/disable", json={"reason": "From the wrong app"})).status_code == 403
    log = (await client.get(f"{API}/sysadmin/audit", headers=ADMIN_APP)).json()
    actions = [e["action"] for e in (log["items"] if isinstance(log, dict) else log)]
    assert "organization.suspended" in actions and "account.disabled" in actions
