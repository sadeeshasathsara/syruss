"""
Paying by card through Stripe: the customer pays on Stripe's Checkout page, Stripe's signed event records the payment
(once, however often it is sent), a card can be kept so renewals pay themselves, and nothing unsigned is believed.
Stripe itself is never called here: the few functions that call it are replaced.
"""
import hashlib
import hmac
import json
import time
import uuid
import pytest
from app.core.config import settings
from app.services.billing import cards
from app.services.billing.methods import card as card_api
from tests.conftest import AsyncSessionLocal
from tests.test_billing import organization
from tests.test_invoices import ADMIN_APP, API, DETAILS, balance, pack, seller_ready

SECRET = "whsec_test_secret"


@pytest.fixture
def stripe_on(monkeypatch):
    """Card payments switched on, with Stripe's calls answered here. Returns what was asked of it."""
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(settings, "BILLING_METHODS", "bank_transfer,staff_recorded,card")
    asked = {"checkouts": [], "sessions": [], "charges": []}

    def create_checkout(**kw):
        asked["checkouts"].append(kw)
        session = f"cs_test_{uuid.uuid4().hex[:12]}"
        asked["sessions"].append(session)
        return {"id": session, "url": f"https://checkout.stripe.test/c/{session}", "expires_at": int(time.time()) + 86400}

    def charge(**kw):
        asked["charges"].append(kw)
        return {"id": "pi_renewal_1", "status": "succeeded", "amount_received": kw["amount_cents"]}

    monkeypatch.setattr(card_api, "create_checkout", create_checkout)
    monkeypatch.setattr(card_api, "card_of", lambda pi: {"customer": "cus_123", "payment_method": "pm_card_visa", "label": "Visa ending 4242"})
    monkeypatch.setattr(card_api, "charge", charge)
    monkeypatch.setattr(card_api, "portal", lambda customer, return_url: f"https://billing.stripe.test/p/{customer}")
    return asked


def signed(event: dict, secret: str = SECRET) -> tuple:
    payload = json.dumps(event)
    t = int(time.time())
    sig = hmac.new(secret.encode(), f"{t}.{payload}".encode(), hashlib.sha256).hexdigest()
    return payload, {"Stripe-Signature": f"t={t},v1={sig}", "Content-Type": "application/json"}


def completed(event_id: str, session_id: str, invoice: dict, amount: int, pi: str = "pi_checkout_1") -> dict:
    return {"id": event_id, "object": "event", "type": "checkout.session.completed", "data": {"object": {
        "id": session_id, "object": "checkout.session", "payment_status": "paid", "amount_total": amount, "currency": "usd",
        "payment_intent": pi, "customer": "cus_123", "metadata": {"invoice_id": invoice["id"], "tenant_id": invoice["tenant_id"], "via": "checkout"}}}}


async def bought(client, slug: str, key: str) -> tuple:
    await seller_ready(client)
    org = await organization(slug)
    await client.put(f"{API}/billing/profile", headers=org, json=DETAILS)
    item = await pack(client, key)
    invoice = (await client.post(f"{API}/billing/purchases", headers=org, json={"item_id": item["id"]})).json()["invoice"]
    return org, f"tenant_{slug}", invoice


async def test_card_is_offered_only_once_stripe_is_set_up(client):
    org, _, invoice = await bought(client, "nocard", "pages-500-nocard")
    shown = (await client.get(f"{API}/billing/invoices/{invoice['id']}", headers=org)).json()
    assert "card" not in [m["key"] for m in shown["how_to_pay"]]
    assert (await client.post(f"{API}/billing/invoices/{invoice['id']}/checkout", headers=org, json={})).status_code == 409
    assert (await client.post(f"{API}/billing/webhooks/stripe", content=b"{}")).status_code == 404


async def test_paying_on_stripe_pays_the_invoice_once_however_often_stripe_says_so(client, stripe_on, mailbox):
    org, tenant, invoice = await bought(client, "carded", "pages-500-card")
    shown = (await client.get(f"{API}/billing/invoices/{invoice['id']}", headers=org)).json()
    assert "card" in [m["key"] for m in shown["how_to_pay"]]

    first = await client.post(f"{API}/billing/invoices/{invoice['id']}/checkout", headers=org, json={"save_card": True})
    session = stripe_on["sessions"][0]
    assert first.status_code == 200 and first.json()["url"] == f"https://checkout.stripe.test/c/{session}", first.text
    again = await client.post(f"{API}/billing/invoices/{invoice['id']}/checkout", headers=org, json={"save_card": True})
    assert again.json()["url"] == first.json()["url"] and len(stripe_on["checkouts"]) == 1       # the open page is reused
    asked = stripe_on["checkouts"][0]
    assert (asked["amount_cents"], asked["currency"], asked["save_card"], asked["email"]) == (invoice["total_cents"], "USD", True, DETAILS["email"])
    assert asked["success_url"].endswith(f"/carded/settings/billing/invoices?invoice={invoice['id']}&paid=card")

    forged, headers = signed(completed("evt_forged", session, invoice, invoice["total_cents"]), secret="whsec_wrong")
    assert (await client.post(f"{API}/billing/webhooks/stripe", content=forged, headers=headers)).status_code == 400
    assert await balance(tenant) == 0

    payload, headers = signed(completed("evt_1", session, invoice, invoice["total_cents"]))
    assert (await client.post(f"{API}/billing/webhooks/stripe", content=payload, headers=headers)).status_code == 200
    assert await balance(tenant) == 500
    payload, headers = signed(completed("evt_1", session, invoice, invoice["total_cents"]))
    assert (await client.post(f"{API}/billing/webhooks/stripe", content=payload, headers=headers)).status_code == 200   # sent again
    payload, headers = signed({**completed("evt_2", session, invoice, invoice["total_cents"]), "type": "checkout.session.async_payment_succeeded"})
    assert (await client.post(f"{API}/billing/webhooks/stripe", content=payload, headers=headers)).status_code == 200   # reported another way
    assert await balance(tenant) == 500

    paid = (await client.get(f"{API}/billing/invoices/{invoice['id']}", headers=org)).json()
    assert paid["status"] == "paid" and [p["method"] for p in paid["payments"]] == ["card"] and paid["payments"][0]["reference"] == "pi_checkout_1"
    assert len([m for m in mailbox if m.subject == f"Invoice {invoice['number']} is paid"]) == 1
    mine = (await client.get(f"{API}/billing", headers=org)).json()
    assert mine["card"] == {"kept": True, "label": "Visa ending 4242", "can_manage": True}
    assert (await client.post(f"{API}/billing/card/portal", headers=org)).json()["url"] == "https://billing.stripe.test/p/cus_123"
    said = [a["text"] for a in (await client.get(f"{API}/organization/activity", headers=org)).json()["items"]]
    assert any(f"Invoice {invoice['number']} was paid by card." == t for t in said)


async def test_a_kept_card_pays_an_invoice_by_itself_and_stripes_report_of_it_is_not_counted_again(client, stripe_on):
    org, tenant, invoice = await bought(client, "renewing", "pages-500-renew")
    await client.post(f"{API}/billing/invoices/{invoice['id']}/checkout", headers=org, json={"save_card": True})
    payload, headers = signed(completed("evt_r1", stripe_on["sessions"][0], invoice, invoice["total_cents"], pi="pi_first"))
    await client.post(f"{API}/billing/webhooks/stripe", content=payload, headers=headers)

    second = (await client.post(f"{API}/sysadmin/billing/invoices", headers=ADMIN_APP, json={
        "tenant_id": tenant, "lines": [{"description": "Next month", "unit_cents": 4900}], "issue": True, "reason": "Renewal"})).json()
    async with AsyncSessionLocal() as db:
        from app.models.billing import Invoice
        paid = await cards.charge_saved_card(db, await db.get(Invoice, second["id"]))
        await db.commit()
    assert paid is not None and paid[0].status == "paid"
    assert stripe_on["charges"][0]["payment_method"] == "pm_card_visa" and stripe_on["charges"][0]["customer"] == "cus_123"
    report = {"id": "evt_r2", "object": "event", "type": "payment_intent.succeeded", "data": {"object": {
        "id": "pi_renewal_1", "object": "payment_intent", "amount_received": second["total_cents"],
        "metadata": {"invoice_id": second["id"], "tenant_id": tenant, "via": "saved_card"}}}}
    payload, headers = signed(report)
    assert (await client.post(f"{API}/billing/webhooks/stripe", content=payload, headers=headers)).status_code == 200
    shown = (await client.get(f"{API}/billing/invoices/{second['id']}", headers=org)).json()
    assert shown["amount_paid_cents"] == second["total_cents"] and len(shown["payments"]) == 1

    forgot = await client.post(f"{API}/billing/card/forget", headers=org)
    assert forgot.json()["kept"] is False
    third = (await client.post(f"{API}/sysadmin/billing/invoices", headers=ADMIN_APP, json={
        "tenant_id": tenant, "lines": [{"description": "Month after", "unit_cents": 4900}], "issue": True, "reason": "Renewal"})).json()
    async with AsyncSessionLocal() as db:
        from app.models.billing import Invoice
        with pytest.raises(ValueError, match="No card is kept"):
            await cards.charge_saved_card(db, await db.get(Invoice, third["id"]))
