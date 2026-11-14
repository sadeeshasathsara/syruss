"""
A paid plan renews with an invoice before its period ends; reminders go out around the due date; unpaid, the
subscription is past due, then moves to Free after the grace period, and paying late puts it back. Moving up mid-period
charges the difference; moving down waits for the period's end. Credit notes correct issued invoices, with refunds.
"""
import re
from datetime import timedelta
from sqlalchemy import select
from app.models.billing import Invoice, OrgSubscriptionAddon
from app.services.billing import dunning, service as billing
from tests.conftest import AsyncSessionLocal
from tests.test_billing import organization
from tests.test_invoices import ADMIN_APP, API, DETAILS, balance, pack, pay, plan, seller_ready


async def on_team(client, slug: str) -> tuple:
    """An organization that bought the Team plan and paid for it: (headers, tenant id)."""
    await seller_ready(client)
    org = await organization(slug)
    await client.put(f"{API}/billing/profile", headers=org, json=DETAILS)
    invoice = (await client.post(f"{API}/billing/purchases", headers=org, json={"item_id": (await plan(client, "team"))["id"]})).json()["invoice"]
    assert (await pay(client, invoice["id"], invoice["total_cents"])).json()["invoice_status"] == "paid"
    return org, f"tenant_{slug}"


async def run_at(now) -> None:
    async with AsyncSessionLocal() as db:
        await dunning.run(db, now)


async def renewals(tenant: str) -> list:
    async with AsyncSessionLocal() as db:
        return list((await db.execute(select(Invoice).where(Invoice.tenant_id == tenant, Invoice.kind == "renewal"))).scalars().all())


async def standing(tenant: str):
    async with AsyncSessionLocal() as db:
        return await billing.standing(db, tenant)


async def test_a_paid_plan_renews_and_unpaid_it_goes_past_due_then_to_free_until_it_is_paid(client, mailbox):
    org, tenant = await on_team(client, "renews")
    addon = (await client.post(f"{API}/sysadmin/billing/catalog", headers=ADMIN_APP, json={
        "kind": "addon", "key": "more-pages-renew", "name": "1,000 more pages", "price_cents": 1000, "limits": {"pages_per_month": 1000},
        "reason": "Pages add-on"})).json()
    await client.post(f"{API}/sysadmin/billing/catalog/{addon['id']}/publish", headers=ADMIN_APP, json={"reason": "On sale"})
    await client.post(f"{API}/sysadmin/billing/organizations/{tenant}/addons", headers=ADMIN_APP, json={"addon_id": addon["id"], "quantity": 2, "reason": "Two of them"})
    end = (await standing(tenant)).subscription.period_end
    mailbox.clear()

    await run_at(end - timedelta(days=8))
    assert await renewals(tenant) == []                                              # too early (7 days' lead)
    await run_at(end - timedelta(days=5))
    await run_at(end - timedelta(days=4, hours=12))                                  # again: still one
    made = await renewals(tenant)
    assert len(made) == 1
    renewal = made[0]
    assert (renewal.status, renewal.period_start, renewal.due_at) == ("open", end, end)
    assert renewal.subtotal_cents == 4900 + 2 * 1000 and renewal.total_cents == 6900 * 118 // 100
    about = lambda: [m.subject for m in mailbox if renewal.number in m.subject or "(Renews)" in m.subject]  # noqa: E731  (the run covers every organization)
    assert about() == [f"Invoice {renewal.number} from Syruss (USD {renewal.total_cents / 100:.2f})"]

    await run_at(end - timedelta(days=3))
    await run_at(end - timedelta(days=2))
    await run_at(end + timedelta(hours=1))
    assert (await standing(tenant)).subscription.status == "past_due"
    await run_at(end + timedelta(days=3, hours=1))
    assert about()[1:] == [f"Invoice {renewal.number} is due in 3 days (Renews)", f"Invoice {renewal.number} is due today (Renews)",
                           f"Invoice {renewal.number} is overdue (Renews)"]
    assert (await standing(tenant)).plan.key == "team"                               # still within the grace period

    await run_at(end + timedelta(days=8))
    s = await standing(tenant)
    assert s.plan.key == "free" and s.subscription.status == "active"
    async with AsyncSessionLocal() as db:
        assert (await db.get(Invoice, renewal.id)).status == "uncollectible"
        assert (await db.execute(select(OrgSubscriptionAddon).where(OrgSubscriptionAddon.tenant_id == tenant))).first() is None
    assert "Renews is on the Free plan now" in [m.subject for m in mailbox]

    assert (await pay(client, renewal.id, renewal.total_cents)).json()["invoice_status"] == "paid"   # paid late
    s = await standing(tenant)
    assert s.plan.key == "team" and s.subscription.paid_until == renewal.period_end
    async with AsyncSessionLocal() as db:
        assert (await db.execute(select(OrgSubscriptionAddon.quantity).where(OrgSubscriptionAddon.tenant_id == tenant))).scalar() == 2


async def test_moving_up_mid_period_charges_the_difference_and_moving_down_waits_for_the_period_end(client):
    org, tenant = await on_team(client, "upgrader")
    started = (await standing(tenant)).subscription.period_start
    business = await plan(client, "business")
    bought = (await client.post(f"{API}/billing/purchases", headers=org, json={"item_id": business["id"]})).json()["invoice"]
    shown = (await client.get(f"{API}/billing/invoices/{bought['id']}", headers=org)).json()
    assert shown["kind"] == "upgrade" and [line["kind"] for line in shown["lines"]] == ["plan", "proration"]
    assert shown["lines"][1]["amount_cents"] < 0 and 14_900 <= shown["subtotal_cents"] <= 15_000      # 199.00 - 49.00, for (almost) the whole period
    await pay(client, bought["id"], bought["total_cents"])
    s = await standing(tenant)
    assert s.plan.key == "business" and s.subscription.period_start == started   # same period, nothing reset

    team = await plan(client, "team")
    later = await client.post(f"{API}/billing/purchases", headers=org, json={"item_id": team["id"]})
    assert later.json()["status"] == "scheduled" and later.json()["invoice"] is None
    assert (await standing(tenant)).subscription.pending_plan_id == team["id"]


async def test_a_credit_note_corrects_a_paid_invoice_and_refunds_it_and_can_settle_an_open_one(client, mailbox):
    await seller_ready(client)
    org = await organization("credited")
    tenant = "tenant_credited"
    await client.put(f"{API}/billing/profile", headers=org, json=DETAILS)
    item = await pack(client, "pages-500-credit")
    invoice = (await client.post(f"{API}/billing/purchases", headers=org, json={"item_id": item["id"]})).json()["invoice"]
    payment = (await pay(client, invoice["id"], invoice["total_cents"])).json()

    base = f"{API}/sysadmin/billing/invoices/{invoice['id']}/credit-notes"
    note = await client.post(base, headers=ADMIN_APP, json={"amount_cents": 500, "reason": "Charged twice for part of it",
                                                            "refund_payment_id": payment["id"], "refund_reference": "Transfer back TRX-77"})
    assert note.status_code == 201, note.text
    assert re.fullmatch(r"SYR-CN-\d{4}-\d{5}", note.json()["number"]) and note.json()["refunds"][0]["amount_cents"] == 500
    shown = (await client.get(f"{API}/billing/invoices/{invoice['id']}", headers=org)).json()
    assert (shown["status"], shown["amount_credited_cents"], shown["amount_refunded_cents"], shown["amount_due_cents"]) == ("paid", 500, 500, 0)
    assert shown["credit_notes"][0]["number"] == note.json()["number"]
    assert (await client.post(base, headers=ADMIN_APP, json={"amount_cents": 1500, "reason": "Too much"})).status_code == 409
    printed = await client.get(f"{API}/billing/credit-notes/{note.json()['id']}/pdf", headers=org)
    assert printed.status_code == 200 and printed.content.startswith(b"%PDF")
    sent = [m for m in mailbox if m.subject == f"Credit note {note.json()['number']} for invoice {invoice['number']}"]
    assert len(sent) == 1 and sent[0].attachments[0][2] == "application/pdf"
    assert await balance(tenant) == 500                                               # taking credits back is staff's separate choice

    second = (await client.post(f"{API}/billing/purchases", headers=org, json={"item_id": item["id"], "quantity": 2})).json()["invoice"]
    await pay(client, second["id"], 2000)
    covered = await client.post(f"{API}/sysadmin/billing/invoices/{second['id']}/credit-notes", headers=ADMIN_APP,
                                json={"amount_cents": second["total_cents"] - 2000, "reason": "Agreed discount"})
    assert covered.status_code == 201
    assert (await client.get(f"{API}/billing/invoices/{second['id']}", headers=org)).json()["status"] == "paid"

    csv_invoices = await client.get(f"{API}/sysadmin/billing/export", headers=ADMIN_APP, params={"what": "invoices"})
    assert csv_invoices.status_code == 200 and csv_invoices.headers["content-type"].startswith("text/csv") and invoice["number"] in csv_invoices.text
    assert note.json()["number"] in (await client.get(f"{API}/sysadmin/billing/export", headers=ADMIN_APP, params={"what": "credit_notes"})).text
    assert "staff_recorded" in (await client.get(f"{API}/sysadmin/billing/export", headers=ADMIN_APP, params={"what": "payments"})).text
