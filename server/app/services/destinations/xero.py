"""
Xero: approved records become bills (invoices of type ACCPAY), as drafts by default so someone in accounts still approves them. Signing in
is OAuth2. Xero matches a contact by name or creates it. Written from Xero's public Accounting API documentation and tested against a mock.
"""
from typing import Any, Dict, Optional
from app.services.destinations import http, oauth
from app.services.destinations.accounting import Bill, BillAdapter, done, fail, failed_reply, fmt
from app.services.destinations.base import Field, SendContext, SendResult, TestResult, make_context, register

API = "https://api.xero.com"


class XeroAdapter(BillAdapter):
    kind = "xero"
    label = "Xero"
    blurb = "Create a bill in Xero for each approved invoice."
    uses_oauth = True
    fields = [
        Field("client_id", "Client ID", "text", required=True, help="From your app at developer.xero.com."),
        Field("client_secret", "Client secret", "password", secret=True, required=True),
        Field("tenant_id", "Organisation ID", "text", help="Filled in when you connect your account."),
        Field("account_code", "Account code for the lines", "text", required=True, placeholder="400", help="The account in your chart of accounts the lines are booked to."),
        Field("status", "Create bills as", "select", default="DRAFT", options=[{"value": "DRAFT", "label": "Drafts (someone approves them in Xero)"}, {"value": "AUTHORISED", "label": "Approved, awaiting payment"}]),
    ]

    def headers(self, bearer: str, tenant: str, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        return {"Authorization": f"Bearer {bearer}", "xero-tenant-id": tenant, "Accept": "application/json", "Content-Type": "application/json", "User-Agent": "Syruss-Delivery/1", **(extra or {})}

    async def test(self, config: Dict[str, Any], secrets: Dict[str, Any], destination_id: Optional[str] = None) -> TestResult:
        ctx = make_context(config, secrets, destination_id)
        bearer, problem = await oauth.access_token("xero", ctx)
        if problem:
            return TestResult(False, problem)
        reply = await http.call("GET", f"{API}/connections", headers={"Authorization": f"Bearer {bearer}", "Accept": "application/json"})
        if not reply.ok:
            return TestResult(False, http.failure_from(reply, "Xero")[0], http.excerpt(reply.text, 300) or None)
        orgs = reply.json() if isinstance(reply.json(), list) else []
        mine = next((o for o in orgs if o.get("tenantId") == config.get("tenant_id")), None)
        if mine is None:
            return TestResult(False, "The connected account has no access to that organisation. Connect your account again.")
        return TestResult(True, f"Connected to {mine.get('tenantName', 'your organisation')}.")

    async def post_bill(self, ctx: SendContext, bill: Bill) -> SendResult:
        tenant = ctx.config.get("tenant_id")
        if not tenant:
            return fail("This destination isn’t connected to a Xero organisation yet. Open it and connect your account.")
        bearer, problem = await oauth.access_token("xero", ctx)
        if problem or bearer is None:
            return fail(problem or "Not signed in.")
        headers = self.headers(bearer, tenant)

        contact = {"ContactID": bill.vendor_id} if bill.vendor_id else {"Name": bill.vendor[:255]}
        # Already there? A retry after a lost reply must not make a second bill.
        where = f'Type=="ACCPAY" AND InvoiceNumber=="{bill.number.replace(chr(34), "")}"'
        existing = await http.call("GET", f"{API}/api.xro/2.0/Invoices", headers=headers, params={"where": where})
        if existing.ok:
            for inv in (existing.json() or {}).get("Invoices", []):
                if inv.get("Contact", {}).get("Name") == bill.vendor or bill.vendor_id:
                    return done(inv.get("InvoiceID") or bill.number, f"Bill {bill.number} already exists", existing)
        invoice: Dict[str, Any] = {
            "Type": "ACCPAY", "Contact": contact, "InvoiceNumber": bill.number[:255], "Date": bill.date, "Status": ctx.config.get("status") or "DRAFT", "LineAmountTypes": "NoTax",
            "LineItems": [{"Description": l.description[:4000], "Quantity": float(l.quantity), "UnitAmount": float(fmt(l.unit_price)), "AccountCode": ctx.config.get("account_code")} for l in bill.lines],
        }
        if bill.due:
            invoice["DueDate"] = bill.due
        if bill.currency:
            invoice["CurrencyCode"] = bill.currency
        if bill.memo:
            invoice["Reference"] = bill.memo[:255]
        reply = await http.call("PUT", f"{API}/api.xro/2.0/Invoices", headers={**headers, "Idempotency-Key": ctx.idempotency_key[:128]}, json_body={"Invoices": [invoice]})
        shown = f"PUT Invoices (ACCPAY {bill.number}) -> {reply.status}"
        if reply.ok:
            made = ((reply.json() or {}).get("Invoices") or [{}])[0]
            if made.get("HasErrors"):
                problems = "; ".join(e.get("Message", "") for e in made.get("ValidationErrors", []))
                return fail(f"Xero refused the bill: {problems or 'it did not pass its checks'}.", request=shown, reply=reply)
            return done(made.get("InvoiceID") or bill.number, shown, reply)
        return failed_reply(reply, "Xero", shown)


register(XeroAdapter())
