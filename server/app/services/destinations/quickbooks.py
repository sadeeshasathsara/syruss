"""
QuickBooks Online: approved records become Bills. Signing in is OAuth2 (the person approves access once). A bill needs a vendor, found
by name or created, and an expense account for its lines. Written from Intuit's public Accounting API documentation and tested against a mock.
"""
import hashlib
from typing import Any, Dict, Optional
from app.services.destinations import http
from app.services.destinations.accounting import Bill, BillAdapter, create_vendors_field, done, failed_reply, fail, fmt
from app.services.destinations.base import Field, SendContext, TestResult, make_context, register
from app.services.destinations import oauth

MINOR = "75"


def _q(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


class QuickBooksAdapter(BillAdapter):
    kind = "quickbooks"
    label = "QuickBooks Online"
    blurb = "Create a bill in QuickBooks for each approved invoice."
    uses_oauth = True
    fields = [
        Field("environment", "Company file", "select", default="production", options=[{"value": "production", "label": "Your real company"}, {"value": "sandbox", "label": "A sandbox company"}]),
        Field("client_id", "Client ID", "text", required=True, help="From your app in the Intuit developer portal (Keys and credentials)."),
        Field("client_secret", "Client secret", "password", secret=True, required=True),
        Field("realm_id", "Company ID", "text", help="Filled in when you connect your account."),
        Field("expense_account_id", "Expense account ID", "text", help="The account the bill’s lines are booked to. Empty: the first expense account."),
        create_vendors_field(),
    ]

    def base(self, config: Dict[str, Any]) -> str:
        host = "sandbox-quickbooks.api.intuit.com" if config.get("environment") == "sandbox" else "quickbooks.api.intuit.com"
        return f"https://{host}/v3/company/{config.get('realm_id', '')}"

    def headers(self, bearer: str) -> Dict[str, str]:
        return {"Authorization": f"Bearer {bearer}", "Accept": "application/json", "Content-Type": "application/json", "User-Agent": "Syruss-Delivery/1"}

    async def query(self, ctx: SendContext, bearer: str, sql: str) -> http.Reply:
        return await http.call("GET", f"{self.base(ctx.config)}/query", headers=self.headers(bearer), params={"query": sql, "minorversion": MINOR})

    async def test(self, config: Dict[str, Any], secrets: Dict[str, Any], destination_id: Optional[str] = None) -> TestResult:
        ctx = make_context(config, secrets, destination_id)
        if not config.get("realm_id"):
            return TestResult(False, "Connect your account first: QuickBooks hasn’t told us which company this is.")
        bearer, problem = await oauth.access_token("quickbooks", ctx)
        if problem:
            return TestResult(False, problem)
        reply = await http.call("GET", f"{self.base(config)}/companyinfo/{config['realm_id']}", headers=self.headers(bearer), params={"minorversion": MINOR})
        if reply.ok:
            name = ((reply.json() or {}).get("CompanyInfo") or {}).get("CompanyName", "your company")
            return TestResult(True, f"Connected to {name}.")
        return TestResult(False, http.failure_from(reply, "QuickBooks")[0], http.excerpt(reply.text, 300) or None)

    async def post_bill(self, ctx: SendContext, bill: Bill) -> SendResult:
        if not ctx.config.get("realm_id"):
            return fail("This destination isn’t connected to a QuickBooks company yet. Open it and connect your account.")
        bearer, problem = await oauth.access_token("quickbooks", ctx)
        if problem or bearer is None:
            return fail(problem or "Not signed in.")
        log = []

        # The supplier: by number if the mapping gave one, else by name, else created.
        vendor_id = bill.vendor_id
        if not vendor_id:
            found = await self.query(ctx, bearer, f"select Id from Vendor where DisplayName = '{_q(bill.vendor)}'")
            log.append(f"GET query Vendor '{bill.vendor}' -> {found.status}")
            if not found.ok:
                return failed_reply(found, "QuickBooks", "\n".join(log))
            vendors = ((found.json() or {}).get("QueryResponse") or {}).get("Vendor") or []
            if vendors:
                vendor_id = vendors[0]["Id"]
            elif ctx.config.get("create_suppliers", True):
                made = await http.call("POST", f"{self.base(ctx.config)}/vendor", headers=self.headers(bearer), params={"minorversion": MINOR}, json_body={"DisplayName": bill.vendor[:100]})
                log.append(f"POST vendor '{bill.vendor}' -> {made.status}")
                if not made.ok:
                    return failed_reply(made, "QuickBooks", "\n".join(log))
                vendor_id = ((made.json() or {}).get("Vendor") or {}).get("Id")
            else:
                return fail(f"The supplier '{bill.vendor}' is not in QuickBooks, and creating suppliers is turned off.", request="\n".join(log))
        if not vendor_id:
            return fail("QuickBooks did not give back a supplier.", retryable=True, request="\n".join(log))

        # Already there? (a retry after a reply was lost must not make a second bill)
        number = bill.number[:21]
        existing = await self.query(ctx, bearer, f"select Id from Bill where VendorRef = '{_q(str(vendor_id))}' and DocNumber = '{_q(number)}'")
        if existing.ok:
            bills = ((existing.json() or {}).get("QueryResponse") or {}).get("Bill") or []
            if bills:
                return done(str(bills[0]["Id"]), "\n".join(log) + f"\nBill {number} already exists", existing)

        account = ctx.config.get("expense_account_id")
        if not account:
            found = await self.query(ctx, bearer, "select Id from Account where AccountType = 'Expense' maxresults 1")
            accounts = ((found.json() or {}).get("QueryResponse") or {}).get("Account") if found.ok else None
            if not accounts:
                return fail("No expense account was found in QuickBooks. Give the account’s ID in this destination’s settings.", request="\n".join(log))
            account = accounts[0]["Id"]

        body: Dict[str, Any] = {
            "VendorRef": {"value": str(vendor_id)}, "DocNumber": number, "TxnDate": bill.date,
            "Line": [{"DetailType": "AccountBasedExpenseLineDetail", "Amount": float(fmt(l.amount)), "Description": l.description[:4000],
                      "AccountBasedExpenseLineDetail": {"AccountRef": {"value": str(account)}}} for l in bill.lines],
        }
        if bill.due:
            body["DueDate"] = bill.due
        if bill.memo:
            body["PrivateNote"] = bill.memo[:4000]
        if bill.currency:
            body["CurrencyRef"] = {"value": bill.currency}
        request_id = hashlib.sha1(ctx.idempotency_key.encode()).hexdigest()   # QuickBooks repeats the same answer for the same request id
        reply = await http.call("POST", f"{self.base(ctx.config)}/bill", headers=self.headers(bearer), params={"minorversion": MINOR, "requestid": request_id}, json_body=body)
        shown = "\n".join(log) + f"\nPOST bill {number} -> {reply.status}"
        if reply.ok:
            bill_id = ((reply.json() or {}).get("Bill") or {}).get("Id")
            return done(str(bill_id or number), shown, reply) if bill_id else fail("QuickBooks accepted the bill but did not say its ID.", retryable=True, request=shown, reply=reply)
        return failed_reply(reply, "QuickBooks", shown)


register(QuickBooksAdapter())
