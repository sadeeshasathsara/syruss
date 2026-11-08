"""
Paying for what is bought: every purchase is an invoice from Syruss to the organization, numbered with no gaps and fixed
once issued, with its tax and a PDF; payments are recorded against it, and what it sells is switched on once it is paid.
"""
import asyncio
import re
from datetime import datetime, timedelta, timezone
from alembic import command
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import create_async_engine
from app.core.database import engine
from app.core.migrations import alembic_config
from app.models.billing import CreditEntry, Invoice
from app.services.billing import credits, service as billing
from app.services.billing.numbering import next_number
from app.services.billing.tax import Rate, tax_for
from tests.conftest import AsyncSessionLocal
from tests.test_billing import organization

API = "/api/v1"
ADMIN_APP = {"X-Client-ID": "syruss-admin"}
DETAILS = {"legal_name": "Acme Holdings (Pvt) Ltd", "email": "accounts@acme.example", "address_line1": "12 Galle Road",
           "city": "Colombo", "postal_code": "00300", "country": "lk"}


async def seller_ready(client) -> None:
    """Syruss's billing details, a USD account to pay into and a tax rate for Sri Lanka."""
    res = await client.put(f"{API}/sysadmin/billing/settings", headers=ADMIN_APP, json={
        "legal_name": "Syruss Ltd", "address": "1 Main Street\nColombo 01", "country": "LK", "tax_id": "VAT-1234", "email": "billing@syruss.example",
        "invoice_prefix": "SYR", "payment_terms_days": 14, "reason": "Our billing details"})
    assert res.status_code == 200, res.text
    settings = (await client.get(f"{API}/sysadmin/billing/settings", headers=ADMIN_APP)).json()
    if not any(a["currency"] == "USD" and a["active"] for a in settings["bank_accounts"]):
        assert (await client.post(f"{API}/sysadmin/billing/bank-accounts", headers=ADMIN_APP, json={
            "bank": "Commercial Bank", "account_name": "Syruss Ltd", "account_number": "1000 2000 3000", "branch": "Colombo 01",
            "swift": "CCEYLKLX", "currency": "USD", "reason": "Where customers pay"})).status_code == 201
    if not any(r["country"] == "LK" and r["active"] for r in settings["tax_rates"]):
        assert (await client.post(f"{API}/sysadmin/billing/tax-rates", headers=ADMIN_APP, json={
            "country": "LK", "name": "VAT", "rate_bp": 1800, "reason": "Sri Lankan VAT"})).status_code == 201


async def pack(client, key: str, price: int = 1500, pages: int = 500) -> dict:
    item = (await client.post(f"{API}/sysadmin/billing/catalog", headers=ADMIN_APP, json={
        "kind": "credits", "key": key, "name": f"{pages} pages", "price_cents": price, "pages": pages, "reason": "A pack"})).json()
    await client.post(f"{API}/sysadmin/billing/catalog/{item['id']}/publish", headers=ADMIN_APP, json={"reason": "On sale"})
    return item


async def plan(client, key: str) -> dict:
    return next(p for p in (await client.get(f"{API}/sysadmin/billing/catalog", headers=ADMIN_APP, params={"kind": "plan"})).json()
                if p["key"] == key and p["status"] == "published")


async def pay(client, invoice_id: str, cents: int, reference: str = "TRX-1"):
    return await client.post(f"{API}/sysadmin/billing/invoices/{invoice_id}/payments", headers=ADMIN_APP, json={
        "method": "staff_recorded", "amount_cents": cents, "received_on": "2026-11-09", "reference": reference, "reason": "Cash received at the office"})


async def balance(tenant_id: str) -> int:
    async with AsyncSessionLocal() as db:
        return await credits.balance(db, tenant_id)


def test_tax_comes_from_the_buyers_country_and_a_business_with_a_tax_id_can_be_reverse_charged():
    rates = [Rate("LK", "VAT", 1800), Rate("*", "VAT", 500), Rate("DE", "VAT", 1900, reverse_charge_with_tax_id=True)]
    assert tax_for(3000, "lk", None, rates).total_cents == 540                     # 18% of 30.00
    assert tax_for(3000, "FR", None, rates).total_cents == 150                     # no French rate: the default
    assert tax_for(1, "LK", None, rates).total_cents == 0 and tax_for(3, "LK", None, rates).total_cents == 1  # rounded half up
    reverse = tax_for(3000, "DE", "DE123", rates)
    assert reverse.total_cents == 0 and "Reverse charge" in reverse.lines[0]["note"] and "DE123" in reverse.lines[0]["note"]
    assert tax_for(3000, "DE", None, rates).total_cents == 570                     # no tax ID: charged
    assert tax_for(3000, "US", None, [Rate("LK", "VAT", 1800)]).lines == []        # no rate at all: no tax line


def test_the_pdf_prints_names_beyond_plain_letters_and_markup_safely():
    from app.models.billing import InvoiceLine
    from app.services.billing.pdf import render
    now = datetime.now(timezone.utc)
    invoice = Invoice(number="SYR-2026-00001", currency="EUR", subtotal_cents=1000, tax_cents=0, total_cents=1000, amount_paid_cents=0,
                      seller={"legal_name": "Syruss Ltd", "address": "1 Main Street\nColombo 01", "country": "LK"},
                      buyer={"legal_name": "Café Ünïon & Søn <Ltd>", "address_line1": "Hauptstraße 5", "city": "Köln", "country": "DE"},
                      bank=[{"bank": "Bank", "account_name": "Syruss", "account_number": "1"}], tax_lines=[], issued_at=now, due_at=now)
    data = render(invoice, [InvoiceLine(position=0, description="Set-up <b>& training</b>", quantity=1, unit_cents=1000, amount_cents=1000)])
    assert data.startswith(b"%PDF") and len(data) > 1000


async def test_invoice_numbers_have_no_gaps_even_when_an_issue_fails_or_two_happen_at_once():
    series = f"invoice:test-{datetime.now(timezone.utc).timestamp()}"
    async with AsyncSessionLocal() as db:
        assert await next_number(db, series) == 1
        await db.commit()
    async with AsyncSessionLocal() as db:
        assert await next_number(db, series) == 2
        await db.rollback()                                                          # that invoice was never issued

    async def take():
        async with AsyncSessionLocal() as db:
            n = await next_number(db, series)
            await asyncio.sleep(0.05)
            await db.commit()
            return n
    assert sorted(await asyncio.gather(take(), take())) == [2, 3]


async def test_buying_issues_an_invoice_and_nothing_is_switched_on_until_it_is_paid(client, mailbox):
    await seller_ready(client)
    org = await organization("invoiced")
    tenant = "tenant_invoiced"
    item = await pack(client, "pages-500-inv")

    missing = await client.post(f"{API}/billing/purchases", headers=org, json={"item_id": item["id"], "quantity": 2})
    assert missing.status_code == 409 and missing.json()["detail"]["code"] == "billing_details_missing", missing.text
    assert (await client.put(f"{API}/billing/profile", headers=org, json={**DETAILS, "country": "L"})).status_code == 422
    saved = await client.put(f"{API}/billing/profile", headers=org, json={**DETAILS, "po_number": "PO-77"})
    assert saved.status_code == 200 and saved.json()["missing"] == [] and saved.json()["profile"]["country"] == "LK"

    bought = await client.post(f"{API}/billing/purchases", headers=org, json={"item_id": item["id"], "quantity": 2, "note": "For the audit team"})
    assert bought.status_code == 201, bought.text
    invoice = bought.json()["invoice"]
    assert re.fullmatch(r"SYR-\d{4}-\d{5}", invoice["number"]) and invoice["status"] == "open"
    assert (invoice["subtotal_cents"], invoice["tax_cents"], invoice["total_cents"]) == (3000, 540, 3540)
    assert invoice["buyer"]["legal_name"] == DETAILS["legal_name"] and invoice["seller"]["tax_id"] == "VAT-1234"
    assert await balance(tenant) == 0                                               # not until it is paid
    issued = [m for m in mailbox if m.to == "accounts@acme.example"]
    assert [m.subject for m in issued] == [f"Invoice {invoice['number']} from Syruss (USD 35.40)"]
    name, data, kind = issued[0].attachments[0]
    assert name == f"Invoice {invoice['number']}.pdf" and kind == "application/pdf" and data.startswith(b"%PDF")

    shown = (await client.get(f"{API}/billing/invoices/{invoice['id']}", headers=org)).json()
    assert [line["quantity"] for line in shown["lines"]] == [2] and shown["buyer"]["po_number"] == "PO-77"
    transfer = next(m for m in shown["how_to_pay"] if m["key"] == "bank_transfer")
    assert transfer["instructions"]["reference"] == invoice["number"] and transfer["instructions"]["accounts"][0]["swift"] == "CCEYLKLX"
    assert all(m["key"] != "staff_recorded" for m in shown["how_to_pay"])            # cash is recorded by staff, not chosen by customers
    printed = await client.get(f"{API}/billing/invoices/{invoice['id']}/pdf", headers=org)
    assert printed.status_code == 200 and printed.headers["content-type"] == "application/pdf" and printed.content.startswith(b"%PDF")
    assert (await client.get(f"{API}/billing/invoices/{invoice['id']}/receipt", headers=org)).status_code == 404

    again = await client.post(f"{API}/billing/purchases", headers=org, json={"item_id": item["id"], "quantity": 2})
    assert again.json()["invoice"]["id"] == invoice["id"] and len([m for m in mailbox if m.to == "accounts@acme.example"]) == 1

    part = await pay(client, invoice["id"], 2000)
    assert part.status_code == 201 and part.json()["invoice_status"] == "open", part.text
    waiting = (await client.get(f"{API}/billing/invoices/{invoice['id']}", headers=org)).json()
    assert (waiting["amount_paid_cents"], waiting["amount_due_cents"]) == (2000, 1540) and await balance(tenant) == 0

    rest = await pay(client, invoice["id"], 1540, "TRX-2")
    assert rest.status_code == 201 and rest.json()["invoice_status"] == "paid", rest.text
    assert await balance(tenant) == 1000
    assert (await pay(client, invoice["id"], 100)).status_code == 409                 # nothing left to pay
    paid = [m for m in mailbox if m.subject == f"Invoice {invoice['number']} is paid"]
    assert len(paid) == 1 and paid[0].attachments[0][0] == f"Receipt {invoice['number']}.pdf"
    receipt = await client.get(f"{API}/billing/invoices/{invoice['id']}/receipt", headers=org)
    assert receipt.status_code == 200 and receipt.content.startswith(b"%PDF")
    async with AsyncSessionLocal() as db:
        assert (await db.execute(select(func.count()).select_from(CreditEntry).where(CreditEntry.ref == f"invoice:{invoice['id']}"))).scalar() == 1

    said = [a["text"] for a in (await client.get(f"{API}/organization/activity", headers=org)).json()["items"]]
    assert any(f"bought 500 pages (× 2): invoice {invoice['number']}" in t for t in said)
    assert any(f"recorded a payment of USD 15.40 for invoice {invoice['number']}, which is now paid" in t for t in said)
    mine = (await client.get(f"{API}/billing", headers=org)).json()
    assert mine["invoices_waiting"] == [] and {p["invoice"] for p in mine["payments"]} == {invoice["number"]}


async def test_staff_switch_an_invoice_on_before_it_is_paid_and_paying_later_does_not_switch_it_on_twice(client):
    await seller_ready(client)
    org = await organization("trusted")
    tenant = "tenant_trusted"
    await client.put(f"{API}/billing/profile", headers=org, json=DETAILS)
    item = await pack(client, "pages-200-trust", price=1000, pages=200)
    made = await client.post(f"{API}/sysadmin/billing/invoices", headers=ADMIN_APP, json={
        "tenant_id": tenant, "lines": [{"item_id": item["id"], "quantity": 1}, {"description": "Onboarding session", "unit_cents": 5000}],
        "issue": True, "reason": "Signed order form"})
    assert made.status_code == 201, made.text
    invoice = made.json()
    assert invoice["status"] == "open" and invoice["subtotal_cents"] == 6000 and len(invoice["lines"]) == 2

    early = await client.post(f"{API}/sysadmin/billing/invoices/{invoice['id']}/switch-on", headers=ADMIN_APP, json={"reason": "Long-standing customer"})
    assert early.status_code == 200 and early.json()["status"] == "open" and early.json()["switched_on"], early.text
    assert await balance(tenant) == 200
    assert (await client.post(f"{API}/sysadmin/billing/invoices/{invoice['id']}/switch-on", headers=ADMIN_APP,
                              json={"reason": "Twice?"})).status_code == 409
    assert (await pay(client, invoice["id"], invoice["total_cents"])).json()["invoice_status"] == "paid"
    assert await balance(tenant) == 200                                             # switched on once


async def test_drafts_are_thrown_away_unpaid_invoices_voided_and_a_paid_one_stays(client):
    await seller_ready(client)
    org = await organization("voids")
    tenant = "tenant_voids"
    base = f"{API}/sysadmin/billing/invoices"
    draft = (await client.post(base, headers=ADMIN_APP, json={"tenant_id": tenant, "lines": [{"description": "Setup", "unit_cents": 900}],
                                                              "reason": "Quote"})).json()
    assert draft["status"] == "draft" and draft["number"] is None
    blocked = await client.post(f"{base}/{draft['id']}/issue", headers=ADMIN_APP, json={"reason": "Send it"})
    assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "billing_details_missing"
    assert (await client.get(f"{API}/billing/invoices/{draft['id']}", headers=org)).status_code == 404      # customers don't see drafts
    assert (await client.post(f"{base}/{draft['id']}/discard", headers=ADMIN_APP, json={"reason": "Not needed"})).status_code == 200

    await client.put(f"{API}/billing/profile", headers=org, json=DETAILS)
    issued = (await client.post(base, headers=ADMIN_APP, json={"tenant_id": tenant, "lines": [{"description": "Setup", "unit_cents": 900}],
                                                               "issue": True, "reason": "Order"})).json()
    assert (await client.post(f"{base}/{issued['id']}/discard", headers=ADMIN_APP, json={"reason": "Too late"})).status_code == 409
    voided = await client.post(f"{base}/{issued['id']}/void", headers=ADMIN_APP, json={"reason": "Wrong amount"})
    assert voided.status_code == 200 and voided.json()["status"] == "void" and voided.json()["number"] == issued["number"]
    assert (await pay(client, issued["id"], 900)).status_code == 409

    partly = (await client.post(base, headers=ADMIN_APP, json={"tenant_id": tenant, "lines": [{"description": "Setup", "unit_cents": 900}],
                                                               "issue": True, "reason": "Order again"})).json()
    await pay(client, partly["id"], 100)
    refused = await client.post(f"{base}/{partly['id']}/void", headers=ADMIN_APP, json={"reason": "Changed mind"})
    assert refused.status_code == 409 and "credit note" in refused.json()["detail"]
    numbers = [issued["number"], partly["number"]]
    assert int(numbers[1][-5:]) == int(numbers[0][-5:]) + 1                          # the voided number stays used


async def test_buying_a_paid_plan_starts_a_new_period_once_paid_and_a_free_plan_waits_for_the_period_end(client):
    await seller_ready(client)
    org = await organization("upgrading")
    tenant = "tenant_upgrading"
    await client.put(f"{API}/billing/profile", headers=org, json=DETAILS)
    team = await plan(client, "team")
    bought = (await client.post(f"{API}/billing/purchases", headers=org, json={"item_id": team["id"]})).json()["invoice"]
    assert bought["total_cents"] == 4900 * 118 // 100                               # the Team plan and Sri Lankan VAT
    async with AsyncSessionLocal() as db:
        assert (await billing.standing(db, tenant)).plan.key == "free"
    assert (await pay(client, bought["id"], bought["total_cents"])).json()["invoice_status"] == "paid"
    async with AsyncSessionLocal() as db:
        s = await billing.standing(db, tenant)
        assert s.plan.key == "team" and s.subscription.paid_until == s.subscription.period_end
        assert abs(s.subscription.period_start - datetime.now(timezone.utc)) < timedelta(minutes=1)    # a whole month from payment

    free = await plan(client, "free")
    later = await client.post(f"{API}/billing/purchases", headers=org, json={"item_id": free["id"]})
    assert later.status_code == 201 and later.json()["status"] == "scheduled" and later.json()["invoice"] is None
    assert (await client.post(f"{API}/billing/purchases", headers=org, json={"item_id": team["id"]})).status_code == 409   # already on it


async def test_upgrading_the_database_turns_open_requests_into_draft_invoices_for_staff_to_issue():
    name = f"{engine.url.database}_invoices"
    admin = create_async_engine(engine.url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = engine.url.set(database=name).render_as_string(hide_password=False)
    try:
        await asyncio.to_thread(command.upgrade, alembic_config(url), "0026")
        old = create_async_engine(url)
        async with old.begin() as conn:
            await conn.execute(text("INSERT INTO tenants (id, name, slug, is_active, status, created_at, updated_at) "
                                    "VALUES ('t_asked', 'Asked', 'asked', true, 'active', now(), now())"))
            for rid, status in (("r_open", "open"), ("r_done", "done")):
                await conn.execute(text("INSERT INTO billing_requests (id, tenant_id, item_id, quantity, note, requested_by, requested_by_name, status, "
                                        "created_at) VALUES (:id, 't_asked', 'plan-team-1', 1, 'Invoice our office', 'u1', 'Una', :status, now())"),
                                   {"id": rid, "status": status})
        await old.dispose()
        await asyncio.to_thread(command.upgrade, alembic_config(url), "head")
        new = create_async_engine(url)
        async with new.connect() as conn:
            drafts = (await conn.execute(text("SELECT id, tenant_id, status, kind, currency, total_cents, note, number FROM invoices"))).all()
            assert [tuple(d)[1:] for d in drafts] == [("t_asked", "draft", "purchase", "USD", 4900, "Invoice our office", None)]
            lines = (await conn.execute(text("SELECT item_id, kind, quantity, unit_cents FROM invoice_lines WHERE invoice_id = :i"), {"i": drafts[0].id})).all()
            assert [tuple(r) for r in lines] == [("plan-team-1", "plan", 1, 4900)]
            assert (await conn.execute(text("SELECT to_regclass('billing_requests')"))).scalar() is None
    finally:
        for made in ("old", "new"):
            if made in locals():
                await locals()[made].dispose()
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await admin.dispose()


async def test_a_trial_that_is_paid_for_keeps_its_plan(client):
    await seller_ready(client)
    org = await organization("trialing")
    tenant = "tenant_trialing"
    await client.put(f"{API}/billing/profile", headers=org, json=DETAILS)
    team = await plan(client, "team")
    base = f"{API}/sysadmin/billing/organizations/{tenant}"
    assert (await client.post(f"{base}/trial", headers=ADMIN_APP, json={"plan_id": team["id"], "days": 14, "reason": "Evaluating"})).status_code == 200
    invoice = (await client.post(f"{API}/sysadmin/billing/invoices", headers=ADMIN_APP, json={
        "tenant_id": tenant, "lines": [{"item_id": team["id"]}], "issue": True, "reason": "Trial converts"})).json()
    await pay(client, invoice["id"], invoice["total_cents"])
    after = (await client.get(base, headers=ADMIN_APP)).json()
    assert after["subscription"]["status"] == "active" and after["subscription"]["paid_until"] == after["subscription"]["period_end"]
    assert after["invoices"][0]["number"] == invoice["number"] and after["billing_details"]["city"] == "Colombo"
    async with AsyncSessionLocal() as db:
        row = await db.get(Invoice, invoice["id"])
        assert row.fulfilled_at is not None and row.receipt_key
