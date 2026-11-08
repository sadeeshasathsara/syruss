"""
Paying by bank transfer: the customer sends the slip for an invoice; staff look at it, then approve it (the payment is
recorded for what arrived and the invoice settles) or reject it with a reason the customer sees and can answer.
"""
import asyncio
import io
from PIL import Image
from tests.conftest import AsyncSessionLocal
from tests.test_billing import organization
from tests.test_invoices import ADMIN_APP, API, DETAILS, balance, pack, seller_ready

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def photo(color=(30, 120, 200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (240, 160), color).save(buf, format="JPEG")
    return buf.getvalue()


async def bought(client, slug: str, key: str) -> tuple:
    await seller_ready(client)
    org = await organization(slug)
    await client.put(f"{API}/billing/profile", headers=org, json=DETAILS)
    item = await pack(client, key)
    invoice = (await client.post(f"{API}/billing/purchases", headers=org, json={"item_id": item["id"]})).json()["invoice"]
    return org, f"tenant_{slug}", invoice


async def send_slip(client, org, invoice_id, files, amount=1770, reference="BANK-REF-1", paid_on="2026-11-08"):
    return await client.post(f"{API}/billing/invoices/{invoice_id}/payments", headers=org,
                             data={"method": "bank_transfer", "amount_cents": str(amount), "paid_on": paid_on, "reference": reference},
                             files=[("files", f) for f in files])


async def test_a_slip_is_checked_by_staff_and_approving_it_pays_the_invoice(client, mailbox):
    org, tenant, invoice = await bought(client, "slipper", "pages-500-slip")
    assert invoice["total_cents"] == 1770

    fake = await send_slip(client, org, invoice["id"], [("slip.pdf", b"MZ\x90\x00 not a pdf", "application/pdf")])
    assert fake.status_code == 422 and "PDF or a photo" in fake.json()["detail"], fake.text      # the content decides, not the name
    big = await send_slip(client, org, invoice["id"], [("slip.pdf", PDF + b"0" * (10 * 1024 * 1024), "application/pdf")])
    assert big.status_code == 422 and "10 MB" in big.json()["detail"]
    future = await send_slip(client, org, invoice["id"], [("slip.jpg", photo(), "image/jpeg")], paid_on="2099-01-01")
    assert future.status_code == 422

    sent = await send_slip(client, org, invoice["id"], [("slip.jpg", photo(), "image/jpeg"), ("confirmation.pdf", PDF, "application/pdf")])
    assert sent.status_code == 201 and sent.json()["status"] == "submitted" and len(sent.json()["files"]) == 2, sent.text
    assert [m.subject for m in mailbox if m.to == "billing@syruss.example"] == [f"Slipper sent a payment for invoice {invoice['number']}"]
    twice = await send_slip(client, org, invoice["id"], [("slip.jpg", photo(), "image/jpeg")])
    assert twice.status_code == 409 and "still being checked" in twice.json()["detail"]
    shown = (await client.get(f"{API}/billing/invoices/{invoice['id']}", headers=org)).json()
    assert shown["submissions"][0]["status"] == "submitted" and shown["status"] == "open" and await balance(tenant) == 0

    waiting = (await client.get(f"{API}/sysadmin/billing/submissions", headers=ADMIN_APP)).json()
    mine = next(s for s in waiting if s["id"] == sent.json()["id"])
    assert mine["invoice"]["number"] == invoice["number"] and mine["organization"]["slug"] == "slipper" and mine["warnings"] == []
    first = mine["files"][0]
    seen = await client.get(f"{API}/sysadmin/billing/submissions/{mine['id']}/files/{first['id']}", headers=ADMIN_APP)
    assert seen.status_code == 200 and seen.headers["content-type"] == "image/jpeg"
    assert seen.headers["x-content-type-options"] == "nosniff" and "sandbox" in seen.headers["content-security-policy"]
    assert (await client.get(f"{API}/billing/submissions/{mine['id']}/files/{first['id']}", headers=org)).status_code == 200

    approved = await client.post(f"{API}/sysadmin/billing/submissions/{mine['id']}/approve", headers=ADMIN_APP, json={"reason": "On the statement"})
    assert approved.status_code == 200 and approved.json()["invoice_status"] == "paid", approved.text
    assert await balance(tenant) == 500
    again = await client.post(f"{API}/sysadmin/billing/submissions/{mine['id']}/approve", headers=ADMIN_APP, json={"reason": "Twice"})
    assert again.status_code == 409
    after = (await client.get(f"{API}/billing/invoices/{invoice['id']}", headers=org)).json()
    assert after["payments"][0]["method"] == "bank_transfer" and after["payments"][0]["reference"] == "BANK-REF-1"
    assert any(m.subject == f"Invoice {invoice['number']} is paid" for m in mailbox)


async def test_a_rejected_slip_tells_the_customer_why_and_they_can_send_it_again(client, mailbox):
    org, tenant, invoice = await bought(client, "rejected", "pages-500-rej")
    first = (await send_slip(client, org, invoice["id"], [("slip.jpg", photo((5, 5, 5)), "image/jpeg")], reference="REF-9")).json()
    no = await client.post(f"{API}/sysadmin/billing/submissions/{first['id']}/reject", headers=ADMIN_APP,
                           json={"reason": "Nothing arrived on our account with that reference yet."})
    assert no.status_code == 200 and no.json()["status"] == "rejected"
    told = [m for m in mailbox if m.to == "accounts@acme.example" and "couldn't confirm" in m.subject.lower()]
    assert len(told) == 1 and "Nothing arrived" in told[0].text
    shown = (await client.get(f"{API}/billing/invoices/{invoice['id']}", headers=org)).json()
    assert shown["submissions"][0]["rejection_reason"].startswith("Nothing arrived") and shown["status"] == "open"

    second = await send_slip(client, org, invoice["id"], [("slip.jpg", photo((5, 5, 5)), "image/jpeg")], reference="REF-9")
    assert second.status_code == 201
    waiting = next(s for s in (await client.get(f"{API}/sysadmin/billing/submissions", headers=ADMIN_APP)).json() if s["id"] == second.json()["id"])
    assert set(waiting["warnings"]) == {"The same file was sent before, with another payment.", "The same bank reference was given for another payment."}

    withdrawn = await client.post(f"{API}/billing/submissions/{second.json()['id']}/withdraw", headers=org)
    assert withdrawn.status_code == 200 and withdrawn.json()["status"] == "withdrawn"
    assert (await client.post(f"{API}/sysadmin/billing/submissions/{second.json()['id']}/approve", headers=ADMIN_APP,
                              json={"reason": "Too late"})).status_code == 409


async def test_staff_approve_what_actually_arrived_and_more_than_is_due_needs_confirming(client):
    org, tenant, invoice = await bought(client, "partial", "pages-500-part")
    short = (await send_slip(client, org, invoice["id"], [("slip.pdf", PDF, "application/pdf")], amount=1770)).json()
    less = await client.post(f"{API}/sysadmin/billing/submissions/{short['id']}/approve", headers=ADMIN_APP,
                             json={"amount_cents": 1000, "reason": "Only 10.00 arrived (bank charges)"})
    assert less.status_code == 200 and less.json()["invoice_status"] == "open" and await balance(tenant) == 0

    more = (await send_slip(client, org, invoice["id"], [("slip2.pdf", PDF + b" ", "application/pdf")], amount=2000, reference="R2")).json()
    asked = await client.post(f"{API}/sysadmin/billing/submissions/{more['id']}/approve", headers=ADMIN_APP, json={"reason": "Arrived"})
    assert asked.status_code == 409 and asked.json()["detail"]["code"] == "overpayment"
    yes = await client.post(f"{API}/sysadmin/billing/submissions/{more['id']}/approve", headers=ADMIN_APP,
                            json={"reason": "Arrived; the extra is credited next month", "overpaid_ok": True})
    assert yes.status_code == 200 and yes.json()["invoice_status"] == "paid" and await balance(tenant) == 500


async def test_other_organizations_never_see_a_slip_and_viewers_cannot_approve(client):
    from tests.conftest import person, register
    org, tenant, invoice = await bought(client, "private", "pages-500-priv")
    sent = (await send_slip(client, org, invoice["id"], [("slip.jpg", photo(), "image/jpeg")])).json()
    other = await organization("nosy")
    assert (await client.get(f"{API}/billing/submissions/{sent['id']}/files/{sent['files'][0]['id']}", headers=other)).status_code == 404
    assert (await client.post(f"{API}/billing/submissions/{sent['id']}/withdraw", headers=other)).status_code == 404
    viewer = {**person("usr_pay_viewer", "Val Viewer"), **ADMIN_APP}
    await register(viewer, None)
    await client.post(f"{API}/sysadmin/staff", headers=ADMIN_APP, json={"email": "usr_pay_viewer@example.com", "role": "viewer", "reason": "Watches"})
    assert (await client.get(f"{API}/sysadmin/billing/submissions", headers=viewer)).status_code == 200
    assert (await client.post(f"{API}/sysadmin/billing/submissions/{sent['id']}/approve", headers=viewer, json={"reason": "Nope"})).status_code == 403


async def test_staff_attach_the_receipt_they_issued_to_a_cash_payment(client):
    org, tenant, invoice = await bought(client, "cashier", "pages-500-cash")
    paid = (await client.post(f"{API}/sysadmin/billing/invoices/{invoice['id']}/payments", headers=ADMIN_APP, json={
        "method": "staff_recorded", "amount_cents": 1770, "reference": "Receipt 0042", "reason": "Cash at the office"})).json()
    attached = await client.post(f"{API}/sysadmin/billing/payments/{paid['id']}/files", headers=ADMIN_APP,
                                 data={"reason": "Our receipt"}, files=[("files", ("receipt.pdf", PDF, "application/pdf"))])
    assert attached.status_code == 201 and attached.json()[0]["filename"] == "receipt.pdf", attached.text
    shown = (await client.get(f"{API}/sysadmin/billing/invoices/{invoice['id']}", headers=ADMIN_APP)).json()
    assert shown["payments"][0]["files"][0]["filename"] == "receipt.pdf"
    await asyncio.sleep(0)
