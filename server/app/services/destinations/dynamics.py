"""
Microsoft Dynamics 365 Business Central: approved records become purchase invoices through the standard API v2.0. Signing in is Microsoft
Entra ID with a client secret (the app registered for Business Central). The invoice is made first, then its lines; if a line is refused
the half-made invoice is deleted so a retry starts clean. Written from Microsoft's public API reference and tested against a mock.
"""
from typing import Any, Dict, Optional, Tuple
from app.services.destinations import http
from app.services.destinations.accounting import Bill, BillAdapter, create_vendors_field, done, fail, failed_reply, fmt
from app.services.destinations.base import Field, SendContext, SendResult, TestResult, register

API = "https://api.businesscentral.dynamics.com/v2.0"


def _s(value: str) -> str:
    return value.replace("'", "''")


class DynamicsAdapter(BillAdapter):
    kind = "dynamics"
    label = "Dynamics 365 Business Central"
    blurb = "Create a purchase invoice in Business Central for each approved invoice."
    fields = [
        Field("tenant_id", "Microsoft Entra tenant ID", "text", required=True, help="The directory (tenant) ID of your Microsoft 365 organisation."),
        Field("environment", "Environment name", "text", required=True, default="Production", placeholder="Production"),
        Field("company_id", "Company ID", "text", required=True, help="The company’s GUID, as listed by the companies API."),
        Field("client_id", "Application (client) ID", "text", required=True),
        Field("client_secret", "Client secret", "password", secret=True, required=True),
        Field("gl_account", "G/L account number for the lines", "text", required=True, placeholder="6100"),
        create_vendors_field(),
    ]

    def base(self, c: Dict[str, Any]) -> str:
        return f"{API}/{c['tenant_id']}/{c['environment']}/api/v2.0/companies({c['company_id']})"

    async def sign_in(self, c: Dict[str, Any], s: Dict[str, Any]) -> Tuple[Optional[Dict[str, str]], Optional[str]]:
        token_url = f"https://login.microsoftonline.com/{c['tenant_id']}/oauth2/v2.0/token"
        bearer, problem = await http.bearer_from_client_credentials(token_url, c.get("client_id", ""), s.get("client_secret", ""), "https://api.businesscentral.dynamics.com/.default")
        if problem or bearer is None:
            return None, problem
        return {"Authorization": f"Bearer {bearer}", "Accept": "application/json", "Content-Type": "application/json", "User-Agent": "Syruss-Delivery/1"}, None

    async def test(self, config: Dict[str, Any], secrets: Dict[str, Any], destination_id: Optional[str] = None) -> TestResult:
        headers, problem = await self.sign_in(config, secrets)
        if problem or headers is None:
            return TestResult(False, problem or "Could not sign in.")
        reply = await http.call("GET", f"{self.base(config)}/vendors", headers=headers, params={"$top": "1", "$select": "id"})
        if reply.ok:
            return TestResult(True, "Connected to Business Central and allowed to read suppliers.")
        return TestResult(False, http.failure_from(reply, "Business Central")[0], http.excerpt(reply.text, 300) or None)

    async def post_bill(self, ctx: SendContext, bill: Bill) -> SendResult:
        c, s = ctx.config, ctx.secrets
        headers, problem = await self.sign_in(c, s)
        if problem or headers is None:
            return fail(problem or "Could not sign in to Business Central.")
        base, log = self.base(c), []
        number = bill.vendor_id
        if not number:
            found = await http.call("GET", f"{base}/vendors", headers=headers, params={"$filter": f"displayName eq '{_s(bill.vendor)}'", "$select": "id,number", "$top": "1"})
            log.append(f"GET vendors '{bill.vendor}' -> {found.status}")
            if not found.ok:
                return failed_reply(found, "Business Central", "\n".join(log))
            rows = (found.json() or {}).get("value") or []
            if rows:
                number = rows[0]["number"]
            elif c.get("create_suppliers", True):
                made = await http.call("POST", f"{base}/vendors", headers=headers, json_body={"displayName": bill.vendor[:100]})
                log.append(f"POST vendors -> {made.status}")
                if not made.ok:
                    return failed_reply(made, "Business Central", "\n".join(log))
                number = (made.json() or {}).get("number")
            else:
                return fail(f"The supplier '{bill.vendor}' is not in Business Central, and creating suppliers is turned off.", request="\n".join(log))
        if not number:
            return fail("Business Central did not give back a supplier number.", retryable=True, request="\n".join(log))

        dup = await http.call("GET", f"{base}/purchaseInvoices", headers=headers, params={"$filter": f"vendorInvoiceNumber eq '{_s(bill.number)}' and vendorNumber eq '{_s(number)}'", "$select": "id,number", "$top": "1"})
        if dup.ok and (dup.json() or {}).get("value"):
            return done(str(dup.json()["value"][0].get("number") or dup.json()["value"][0]["id"]), "\n".join(log) + f"\nInvoice {bill.number} already exists", dup)

        header: Dict[str, Any] = {"vendorNumber": number, "vendorInvoiceNumber": bill.number[:35], "invoiceDate": bill.date}
        if bill.due:
            header["dueDate"] = bill.due
        if bill.currency:
            header["currencyCode"] = bill.currency
        created = await http.call("POST", f"{base}/purchaseInvoices", headers=headers, json_body=header)
        log.append(f"POST purchaseInvoices {bill.number} -> {created.status}")
        if not created.ok:
            return failed_reply(created, "Business Central", "\n".join(log))
        invoice = created.json() or {}
        for i, line in enumerate(bill.lines, start=1):
            made = await http.call("POST", f"{base}/purchaseInvoices({invoice['id']})/purchaseInvoiceLines", headers=headers, json_body={
                "lineType": "Account", "lineObjectNumber": str(c["gl_account"]), "description": line.description[:100], "quantity": float(line.quantity), "unitCost": float(fmt(line.unit_price))})
            log.append(f"POST line {i} -> {made.status}")
            if not made.ok:
                await http.call("DELETE", f"{base}/purchaseInvoices({invoice['id']})", headers={**headers, "If-Match": "*"})   # leave nothing half-made behind
                log.append("DELETE half-made invoice")
                return failed_reply(made, "Business Central", "\n".join(log))
        return done(str(invoice.get("number") or invoice["id"]), "\n".join(log), created)


register(DynamicsAdapter())
