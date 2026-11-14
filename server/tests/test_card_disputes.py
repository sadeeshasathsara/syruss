"""
What happens to card payments after they are made: a cardholder disputing one with their bank, a bank debit that bounces,
and a renewal whose kept card is declined and tried again before anyone is chased.
"""
from datetime import datetime, timedelta, timezone
from app.models.billing import Invoice
from app.services.billing import dunning
from app.services.billing.methods import card as card_api
from tests.conftest import AsyncSessionLocal
from tests.test_card_payments import bought, completed, signed, stripe_on  # noqa: F401  (stripe_on is a fixture)
from tests.test_invoices import ADMIN_APP, API


async def hook(client, event: dict):
    payload, headers = signed(event)
    res = await client.post(f"{API}/billing/webhooks/stripe", content=payload, headers=headers)
    assert res.status_code == 200, res.text


def dispute(event_id: str, kind: str, pi: str, amount: int, status: str = "needs_response") -> dict:
    return {"id": event_id, "object": "event", "type": kind, "data": {"object": {
        "id": f"dp_{pi}", "object": "dispute", "payment_intent": pi, "amount": amount, "currency": "usd", "reason": "fraudulent", "status": status}}}


async def test_a_disputed_card_payment_reaches_staff_and_a_lost_one_is_recorded_as_money_back(client, stripe_on, mailbox):
    org, tenant, invoice = await bought(client, "disputed", "pages-500-dispute")
    await client.post(f"{API}/billing/invoices/{invoice['id']}/checkout", headers=org, json={})
    await hook(client, completed("evt_d1", stripe_on["sessions"][0], invoice, invoice["total_cents"], pi="pi_disputed"))

    await hook(client, dispute("evt_d2", "charge.dispute.created", "pi_disputed", invoice["total_cents"]))
    told = [m for m in mailbox if "is disputed" in m.subject]
    assert len(told) == 1 and told[0].to == "billing@syruss.example" and invoice["number"] in told[0].subject
    seen = (await client.get(f"{API}/sysadmin/billing/invoices/{invoice['id']}", headers=ADMIN_APP)).json()
    assert seen["status"] == "paid" and seen["amount_refunded_cents"] == 0  # nothing has moved yet

    # Won: nothing changes. Lost: the money went back, once however often Stripe says so.
    await hook(client, dispute("evt_d3", "charge.dispute.closed", "pi_disputed", invoice["total_cents"], status="won"))
    assert (await client.get(f"{API}/sysadmin/billing/invoices/{invoice['id']}", headers=ADMIN_APP)).json()["amount_refunded_cents"] == 0
    for event_id in ("evt_d4", "evt_d5"):
        await hook(client, dispute(event_id, "charge.dispute.closed", "pi_disputed", invoice["total_cents"], status="lost"))
    seen = (await client.get(f"{API}/sysadmin/billing/invoices/{invoice['id']}", headers=ADMIN_APP)).json()
    assert seen["amount_refunded_cents"] == invoice["total_cents"]
    assert len([m for m in mailbox if "dispute" in m.subject and "was lost" in m.subject]) == 1


async def test_a_bank_debit_that_bounces_tells_the_customer_and_leaves_the_invoice_open(client, stripe_on, mailbox):
    org, tenant, invoice = await bought(client, "bounced", "pages-500-bounce")
    await client.post(f"{API}/billing/invoices/{invoice['id']}/checkout", headers=org, json={})
    pending = completed("evt_b1", stripe_on["sessions"][0], invoice, invoice["total_cents"])
    pending["data"]["object"]["payment_status"] = "unpaid"  # a debit on its way
    await hook(client, pending)
    await hook(client, {**completed("evt_b2", stripe_on["sessions"][0], invoice, invoice["total_cents"]), "type": "checkout.session.async_payment_failed"})
    assert (await client.get(f"{API}/billing/invoices/{invoice['id']}", headers=org)).json()["status"] == "open"
    assert any("did not go through" in (m.text or "") for m in mailbox)


async def test_a_declined_renewal_card_is_tried_again_before_the_customer_is_chased(client, stripe_on, mailbox, monkeypatch):
    org, tenant, first = await bought(client, "retried", "pages-500-retry")
    await client.post(f"{API}/billing/invoices/{first['id']}/checkout", headers=org, json={"save_card": True})
    await hook(client, completed("evt_t1", stripe_on["sessions"][0], first, first["total_cents"], pi="pi_first_retry"))

    renewal = (await client.post(f"{API}/sysadmin/billing/invoices", headers=ADMIN_APP, json={
        "tenant_id": tenant, "lines": [{"description": "Next month", "unit_cents": 4900}], "issue": True, "reason": "Renewal"})).json()
    due = datetime.now(timezone.utc) + timedelta(days=2)
    async with AsyncSessionLocal() as db:
        row = await db.get(Invoice, renewal["id"])
        row.kind, row.due_at, row.period_start, row.period_end = "renewal", due, due, due + timedelta(days=30)
        await db.commit()

    tries = []

    def declined(**kw):
        tries.append(kw["attempt"])
        raise RuntimeError("Your card was declined.")

    monkeypatch.setattr(card_api, "charge", declined)
    mailbox.clear()
    async with AsyncSessionLocal() as db:
        await dunning.send_reminders(db, due - timedelta(days=2))   # declined again: now the customer is reminded
    assert len(tries) == 1 and len([m for m in mailbox if renewal["number"] in m.subject]) == 1

    def accepted(**kw):
        tries.append(kw["attempt"])
        return {"id": "pi_retry_paid", "status": "succeeded", "amount_received": kw["amount_cents"]}

    monkeypatch.setattr(card_api, "charge", accepted)
    mailbox.clear()
    async with AsyncSessionLocal() as db:
        await dunning.send_reminders(db, due + timedelta(hours=1))   # the card went through: nobody is chased
    assert (await client.get(f"{API}/billing/invoices/{renewal['id']}", headers=org)).json()["status"] == "paid"
    assert len(tries) == 2 and [m.subject for m in mailbox if renewal["number"] in m.subject] == [f"Invoice {renewal['number']} is paid"]


async def test_staff_see_where_the_money_stands(client, stripe_on):
    org, tenant, invoice = await bought(client, "summed", "pages-500-sum")
    before = {r["currency"]: r for r in (await client.get(f"{API}/sysadmin/billing/summary", headers=ADMIN_APP)).json()}["USD"]
    assert before["owed_cents"] >= invoice["total_cents"] and before["owed_invoices"] >= 1
    await client.post(f"{API}/billing/invoices/{invoice['id']}/checkout", headers=org, json={})
    await hook(client, completed("evt_s1", stripe_on["sessions"][0], invoice, invoice["total_cents"], pi="pi_summed"))
    after = {r["currency"]: r for r in (await client.get(f"{API}/sysadmin/billing/summary", headers=ADMIN_APP)).json()}["USD"]
    assert after["owed_cents"] == before["owed_cents"] - invoice["total_cents"]
    assert after["received_this_month_cents"] == before["received_this_month_cents"] + invoice["total_cents"]
