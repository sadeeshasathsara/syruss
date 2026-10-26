"""
SAP S/4HANA: approved records become supplier invoices through the standard OData service API_SUPPLIERINVOICE_PROCESS_SRV, posted to
G/L accounts (invoices without a purchase order). SAP wants a CSRF token fetched first and sent back with its cookies. Suppliers are looked up
in API_BUSINESS_PARTNER and never created: supplier master data belongs to whoever governs it in SAP. Written from SAP's public API
documentation and tested against a mock.
"""
import base64
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple
from app.services.destinations import http
from app.services.destinations.accounting import Bill, BillAdapter, done, fail, failed_reply, fmt
from app.services.destinations.base import Field, SendContext, SendResult, TestResult, register

INVOICE = "/sap/opu/odata/sap/API_SUPPLIERINVOICE_PROCESS_SRV"
PARTNER = "/sap/opu/odata/sap/API_BUSINESS_PARTNER"


def _s(value: str) -> str:
    return value.replace("'", "''")


def _stamp(day: str) -> str:
    return f"{day}T00:00:00"


class SapAdapter(BillAdapter):
    kind = "sap"
    label = "SAP S/4HANA"
    blurb = "Post a supplier invoice in SAP for each approved invoice."
    fields = [
        Field("base_url", "System address", "url", required=True, placeholder="https://my-system.s4hana.cloud.sap", help="The address of your S/4HANA system, without a path."),
        Field("auth_type", "Sign-in", "select", default="basic", options=[{"value": "basic", "label": "Communication user (username and password)"}, {"value": "oauth2", "label": "OAuth2 client credentials"}]),
        Field("username", "Communication user", "text", required=True, show_if={"auth_type": "basic"}),
        Field("password", "Password", "password", secret=True, required=True, show_if={"auth_type": "basic"}),
        Field("token_url", "Token address", "url", required=True, show_if={"auth_type": "oauth2"}),
        Field("client_id", "Client ID", "text", required=True, show_if={"auth_type": "oauth2"}),
        Field("client_secret", "Client secret", "password", secret=True, required=True, show_if={"auth_type": "oauth2"}),
        Field("company_code", "Company code", "text", required=True, placeholder="1710"),
        Field("gl_account", "G/L account for the lines", "text", required=True, placeholder="0000400000"),
        Field("sap_client", "SAP client", "text", placeholder="100", help="Only if your system needs it (sent as sap-client)."),
    ]

    async def sign_in(self, c: Dict[str, Any], s: Dict[str, Any]) -> Tuple[Optional[Dict[str, str]], Optional[str]]:
        headers = {"Accept": "application/json", "User-Agent": "Syruss-Delivery/1"}
        if c.get("auth_type") == "oauth2":
            bearer, problem = await http.bearer_from_client_credentials(c.get("token_url", ""), c.get("client_id", ""), s.get("client_secret", ""))
            if problem:
                return None, problem
            headers["Authorization"] = f"Bearer {bearer}"
        else:
            headers["Authorization"] = "Basic " + base64.b64encode(f"{c.get('username', '')}:{s.get('password', '')}".encode()).decode()
        return headers, None

    def url(self, c: Dict[str, Any], path: str) -> str:
        return f"{c['base_url'].rstrip('/')}{path}"

    def params(self, c: Dict[str, Any], extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        return {"$format": "json", **({"sap-client": c["sap_client"]} if c.get("sap_client") else {}), **(extra or {})}

    async def csrf(self, c: Dict[str, Any], headers: Dict[str, str]) -> Tuple[Optional[Dict[str, str]], Optional[http.Reply]]:
        """The headers for a write: SAP hands out a CSRF token (and cookies) on a read that asks for one."""
        reply = await http.call("GET", self.url(c, f"{INVOICE}/"), headers={**headers, "x-csrf-token": "Fetch"}, params=self.params(c))
        if not reply.ok:
            return None, reply
        cookie = "; ".join(f"{k}={v}" for k, v in reply.cookies.items())
        return {**headers, "x-csrf-token": reply.headers.get("x-csrf-token", ""), "Content-Type": "application/json", **({"Cookie": cookie} if cookie else {})}, None

    async def test(self, config: Dict[str, Any], secrets: Dict[str, Any], destination_id: Optional[str] = None) -> TestResult:
        headers, problem = await self.sign_in(config, secrets)
        if problem or headers is None:
            return TestResult(False, problem or "Could not sign in.")
        write, reply = await self.csrf(config, headers)
        if write is None:
            return TestResult(False, http.failure_from(reply, "SAP")[0] if reply else "SAP did not answer.", http.excerpt(reply.text, 300) if reply else None)
        return TestResult(True, "Connected to SAP and allowed to post supplier invoices.")

    async def post_bill(self, ctx: SendContext, bill: Bill) -> SendResult:
        c, s = ctx.config, ctx.secrets
        headers, problem = await self.sign_in(c, s)
        if problem or headers is None:
            return fail(problem or "Could not sign in to SAP.")
        log = []
        supplier = bill.vendor_id
        if not supplier:
            found = await http.call("GET", self.url(c, f"{PARTNER}/A_Supplier"), headers=headers,
                                    params=self.params(c, {"$filter": f"SupplierName eq '{_s(bill.vendor)}'", "$select": "Supplier", "$top": "1"}))
            log.append(f"GET A_Supplier '{bill.vendor}' -> {found.status}")
            if not found.ok:
                return failed_reply(found, "SAP", "\n".join(log))
            rows = ((found.json() or {}).get("d") or {}).get("results") or []
            if not rows:
                return fail(f"The supplier '{bill.vendor}' is not in SAP. Create it there, or map the supplier’s SAP number so no lookup by name is needed.", request="\n".join(log))
            supplier = rows[0]["Supplier"]

        dup = await http.call("GET", self.url(c, f"{INVOICE}/A_SupplierInvoice"), headers=headers, params=self.params(
            c, {"$filter": f"SupplierInvoiceIDByInvcgParty eq '{_s(bill.number)}' and InvoicingParty eq '{_s(supplier)}'", "$select": "SupplierInvoice,FiscalYear", "$top": "1"}))
        if dup.ok:
            rows = ((dup.json() or {}).get("d") or {}).get("results") or []
            if rows:
                return done(f"{rows[0]['SupplierInvoice']}/{rows[0]['FiscalYear']}", "\n".join(log) + f"\nInvoice {bill.number} already exists", dup)

        write, reply = await self.csrf(c, headers)
        if write is None:
            return failed_reply(reply, "SAP", "\n".join(log)) if reply else fail("SAP did not answer.", retryable=True)
        currency = bill.currency or "EUR"
        total = sum((l.amount for l in bill.lines), Decimal(0))
        body = {
            "CompanyCode": c["company_code"], "DocumentDate": _stamp(bill.date), "PostingDate": _stamp(bill.date), "SupplierInvoiceIDByInvcgParty": bill.number[:16],
            "InvoicingParty": supplier, "DocumentCurrency": currency, "InvoiceGrossAmount": fmt(total), "DocumentHeaderText": (bill.memo or f"Invoice {bill.number}")[:25],
            "to_SuplrInvcItemGLAcct": [
                {"SupplierInvoiceItem": str(i), "CompanyCode": c["company_code"], "GLAccount": c["gl_account"], "DocumentCurrency": currency, "SupplierInvoiceItemAmount": fmt(l.amount),
                 "DebitCreditCode": "S", "SupplierInvoiceItemText": l.description[:50]}
                for i, l in enumerate(bill.lines, start=1)
            ],
        }
        reply = await http.call("POST", self.url(c, f"{INVOICE}/A_SupplierInvoice"), headers=write, params=self.params(c), json_body=body)
        shown = "\n".join(log) + f"\nPOST A_SupplierInvoice {bill.number} -> {reply.status}"
        if reply.ok:
            d = (reply.json() or {}).get("d") or {}
            return done(f"{d.get('SupplierInvoice', bill.number)}/{d.get('FiscalYear', '')}".rstrip("/"), shown, reply)
        return failed_reply(reply, "SAP", shown)


register(SapAdapter())
