"""
Beyond the plan's pages: prepaid credits, the catalog staff manage, customers asking for what is on offer, staff
recording payments, and the alerts the organization's owners get.
"""
import time
from sqlalchemy import select
from app.models.billing import UsageEvent
from app.models.platform import PlatformAuditEntry
from app.services.billing import credits
from tests.conftest import AsyncSessionLocal, pages_allowance, person, register
from tests.test_billing import organization, send, tiff, used, workspace

API = "/api/v1"
ADMIN_APP = {"X-Client-ID": "syruss-admin"}


async def grant(tenant_id: str, pages: int) -> None:
    async with AsyncSessionLocal() as db:
        await credits.add(db, tenant_id=tenant_id, delta=pages, kind="grant", created_by="tests", reason="test")
        await db.commit()


async def balance(tenant_id: str) -> int:
    async with AsyncSessionLocal() as db:
        return await credits.balance(db, tenant_id)


async def test_credits_carry_on_where_the_plans_pages_end(client):
    org = await organization("credits-on")
    tenant = "tenant_credits-on"
    await pages_allowance(2, tenant)
    await grant(tenant, 3)
    tpl = await workspace(client, org)

    assert (await send(client, org, tpl)).status_code == 201                  # 1 of the plan's 2 pages
    scan = await send(client, org, tpl, tiff(3), "scan.tif")                     # 1 plan page + 2 credits
    assert scan.status_code == 201, scan.text
    assert await used(tenant) == 2 and await balance(tenant) == 1
    async with AsyncSessionLocal() as db:
        event = (await db.execute(select(UsageEvent).where(UsageEvent.source_id == scan.json()["id"]))).scalar_one()
        assert (event.quantity, event.from_credits) == (3, 2)

    too_big = await send(client, org, tpl, tiff(2), "two.tif")
    assert too_big.status_code == 402 and too_big.json()["detail"].startswith("This document has 2 pages, but only 1 prepaid credits is left.")
    assert (await send(client, org, tpl)).status_code == 201                  # the last credit
    out = await send(client, org, tpl)
    assert out.status_code == 402 and "are used up, and there are no prepaid credits" in out.json()["detail"]
    assert await balance(tenant) == 0


async def test_staff_build_the_catalog_in_versions_and_a_published_one_never_changes(client):
    limits = (await client.get(f"{API}/sysadmin/billing/limits", headers=ADMIN_APP)).json()
    assert {"pages_per_month", "channels_per_workspace"} <= {k["key"] for k in limits["limits"]} and limits["provider"]["kind"] == "manual"

    draft = await client.post(f"{API}/sysadmin/billing/catalog", headers=ADMIN_APP, json={
        "kind": "credits", "key": "pages-1000", "name": "1,000 pages", "price_cents": 2500, "pages": 1000, "reason": "First credit pack"})
    assert draft.status_code == 201, draft.text
    pack = draft.json()
    assert (pack["version"], pack["status"], pack["interval"]) == (1, "draft", "once")
    bad = await client.post(f"{API}/sysadmin/billing/catalog", headers=ADMIN_APP, json={
        "kind": "plan", "key": "odd", "name": "Odd", "limits": {"rockets": 3}, "reason": "Trying a made-up limit"})
    assert bad.status_code == 422 and "rockets" in bad.json()["detail"]

    assert (await client.patch(f"{API}/sysadmin/billing/catalog/{pack['id']}", headers=ADMIN_APP,
                               json={"price_cents": 2000, "reason": "Launch price"})).json()["price_cents"] == 2000
    assert (await client.post(f"{API}/sysadmin/billing/catalog/{pack['id']}/publish", headers=ADMIN_APP,
                              json={"reason": "On sale"})).json()["status"] == "published"
    frozen = await client.patch(f"{API}/sysadmin/billing/catalog/{pack['id']}", headers=ADMIN_APP, json={"price_cents": 1, "reason": "Changing a published one"})
    assert frozen.status_code == 409
    v2 = await client.post(f"{API}/sysadmin/billing/catalog/{pack['id']}/versions", headers=ADMIN_APP, json={"price_cents": 2200, "reason": "New price"})
    assert (v2.json()["version"], v2.json()["status"], v2.json()["price_cents"], v2.json()["pages"]) == (2, "draft", 2200, 1000)

    # Changes need a fresh sign-in, and a viewer changes nothing.
    stale = {**ADMIN_APP, "X-Auth-Time": str(int(time.time()) - 3600)}
    assert (await client.post(f"{API}/sysadmin/billing/catalog/{v2.json()['id']}/publish", headers=stale,
                              json={"reason": "Stale"})).json()["detail"]["code"] == "reauth_required"
    viewer = {**person("usr_bill_viewer", "Vee Viewer"), **ADMIN_APP}
    await register(viewer, None)
    await client.post(f"{API}/sysadmin/staff", headers=ADMIN_APP, json={"email": "usr_bill_viewer@example.com", "role": "viewer", "reason": "Watches"})
    assert (await client.get(f"{API}/sysadmin/billing/catalog", headers=viewer)).status_code == 200
    assert (await client.post(f"{API}/sysadmin/billing/catalog/{v2.json()['id']}/publish", headers=viewer,
                              json={"reason": "Not allowed"})).status_code == 403


async def test_a_customer_asks_for_credits_and_staff_record_the_payment_that_switches_them_on(client):
    org = await organization("buyer")
    tenant = "tenant_buyer"
    pack = (await client.post(f"{API}/sysadmin/billing/catalog", headers=ADMIN_APP, json={
        "kind": "credits", "key": "pages-500", "name": "500 pages", "price_cents": 1500, "pages": 500, "reason": "Small pack"})).json()
    await client.post(f"{API}/sysadmin/billing/catalog/{pack['id']}/publish", headers=ADMIN_APP, json={"reason": "On sale"})

    offers = (await client.get(f"{API}/billing/catalog", headers=org)).json()
    assert pack["id"] in [c["id"] for c in offers["credits"]] and [p["key"] for p in offers["plan"]][:3] == ["free", "team", "business"]
    asked = await client.post(f"{API}/billing/purchases", headers=org, json={"item_id": pack["id"], "quantity": 2, "note": "Invoice to accounts"})
    assert asked.status_code == 201 and asked.json()["status"] == "requested", asked.text

    # A member who doesn't manage billing can't see or buy.
    member = person("usr_bill_member", "Mo Member")
    await register(member, None)
    from app.models.identity import OrgMember
    async with AsyncSessionLocal() as db:
        db.add(OrgMember(tenant_id=tenant, user_id="usr_bill_member", role="member"))
        await db.commit()
    assert (await client.get(f"{API}/billing", headers={**org, **member})).status_code == 403

    waiting = (await client.get(f"{API}/sysadmin/billing/requests", headers=ADMIN_APP)).json()
    request = next(r for r in waiting if r["id"] == asked.json()["request_id"])
    assert (request["quantity"], request["note"], request["organization"]["slug"]) == (2, "Invoice to accounts", "buyer")
    done = await client.post(f"{API}/sysadmin/billing/requests/{request['id']}", headers=ADMIN_APP, json={
        "action": "fulfil", "amount_cents": 3000, "reference": "INV-1001", "note": "Paid by transfer", "reason": "Payment received"})
    assert done.status_code == 200 and done.json()["status"] == "done", done.text
    assert await balance(tenant) == 1000
    again = await client.post(f"{API}/sysadmin/billing/requests/{request['id']}", headers=ADMIN_APP, json={"action": "fulfil", "reason": "Twice?"})
    assert again.status_code == 409

    mine = (await client.get(f"{API}/billing", headers=org)).json()
    assert mine["credits"]["balance"] == 1000 and mine["payments"][0]["reference"] == "INV-1001" and mine["requests"][0]["status"] == "done"
    activity = (await client.get(f"{API}/organization/activity", headers=org)).json()["items"]
    said = [a["text"] for a in activity]
    assert any("asked Syruss for 500 pages (× 2)" in t for t in said)
    assert any("of Syruss support recorded a payment of 30.00 USD and added 1,000 prepaid pages" in t and "Payment received" in t for t in said)
    async with AsyncSessionLocal() as db:
        actions = (await db.execute(select(PlatformAuditEntry.action).where(PlatformAuditEntry.tenant_id == tenant))).scalars().all()
        assert "billing.request_fulfilled" in actions


async def test_staff_change_an_organizations_plan_add_ons_limits_trial_and_credits(client):
    org = await organization("managed")
    tenant = "tenant_managed"
    base = f"{API}/sysadmin/billing/organizations/{tenant}"
    catalog = (await client.get(f"{API}/sysadmin/billing/catalog", headers=ADMIN_APP, params={"kind": "plan"})).json()
    team = next(p for p in catalog if p["key"] == "team" and p["status"] == "published")
    addon = (await client.post(f"{API}/sysadmin/billing/catalog", headers=ADMIN_APP, json={
        "kind": "addon", "key": "more-pages", "name": "1,000 more pages a month", "price_cents": 1000,
        "limits": {"pages_per_month": 1000}, "reason": "Pages add-on"})).json()
    await client.post(f"{API}/sysadmin/billing/catalog/{addon['id']}/publish", headers=ADMIN_APP, json={"reason": "On sale"})

    assert (await client.post(f"{base}/plan", headers=ADMIN_APP, json={"plan_id": team["id"], "reason": "Signed up"})).status_code == 200
    assert (await client.post(f"{base}/addons", headers=ADMIN_APP, json={"addon_id": addon["id"], "quantity": 2, "reason": "Bought two"})).status_code == 200
    tpl = await workspace(client, org)
    assert (await client.post(f"{base}/overrides", headers=ADMIN_APP, json={
        "key": "channels_per_workspace", "value": 9, "template_id": tpl, "reason": "Custom deal"})).status_code == 201
    assert (await client.post(f"{base}/overrides", headers=ADMIN_APP, json={
        "key": "pages_per_month", "value": 5, "template_id": tpl, "reason": "Not per workspace"})).status_code == 422
    assert (await client.post(f"{base}/credits", headers=ADMIN_APP, json={"pages": 50, "reason": "Sorry for the outage"})).status_code == 200
    assert (await client.post(f"{base}/credits", headers=ADMIN_APP, json={"pages": -500, "kind": "adjustment", "reason": "Too much"})).status_code == 409

    seen = (await client.get(base, headers=ADMIN_APP)).json()
    assert seen["subscription"]["plan"]["key"] == "team"
    assert seen["limits"]["effective"]["pages_per_month"] == 4000           # 2,000 + 2 × 1,000
    assert seen["limits"]["overrides"][0]["workspace"] == "Forms" and seen["credits"]["balance"] == 50

    trial = await client.post(f"{base}/trial", headers=ADMIN_APP, json={"plan_id": team["id"], "days": 14, "reason": "Evaluating"})
    assert trial.status_code == 200
    follow_up = (await client.get(f"{API}/sysadmin/billing/expiring", headers=ADMIN_APP, params={"days": 30})).json()
    assert any(f["tenant_id"] == tenant and f["status"] == "trialing" for f in follow_up)
    paid = await client.post(f"{base}/payments", headers=ADMIN_APP, json={
        "amount_cents": 4900, "item_id": team["id"], "reference": "INV-2001", "reason": "First month paid"})
    assert paid.status_code == 201
    after = (await client.get(base, headers=ADMIN_APP)).json()["subscription"]
    assert after["status"] == "active" and after["paid_until"] == after["period_end"]


async def test_the_owners_hear_once_when_pages_run_low_and_when_they_run_out(client, mailbox):
    org = await organization("alerts")
    tenant = "tenant_alerts"
    await pages_allowance(5, tenant)
    tpl = await workspace(client, org)
    for _ in range(3):
        await send(client, org, tpl)
    assert not mailbox
    await send(client, org, tpl)                       # 4 of 5: 80%
    await send(client, org, tpl)                       # 5 of 5
    await send(client, org, tpl)                       # refused: no more alerts
    subjects = [m.subject for m in mailbox]
    assert subjects == ["4 of 5 pages used this period (Alerts)", "All 5 pages for this period are used (Alerts)"], subjects
    from app.models.notification import Notification
    async with AsyncSessionLocal() as db:
        bell = (await db.execute(select(Notification).where(Notification.tenant_id == tenant))).scalars().all()
        assert {n.type for n in bell} == {"billing.usage"} and len(bell) == 2
