"""
Oracle NetSuite: approved records become Vendor Bills through the REST Record API. Signing in is Token-Based Authentication (OAuth 1.0a,
HMAC-SHA256), the method NetSuite documents for server-to-server calls. The supplier and any bill already filed under the same
number are looked up with SuiteQL. Written from Oracle's public REST Web Services documentation and tested against a mock.
"""
import base64
import hashlib
import hmac
import secrets as token
import time
from typing import Any, Dict, Optional
from urllib.parse import parse_qsl, quote, urlsplit
from app.services.destinations import http
from app.services.destinations.accounting import Bill, BillAdapter, create_vendors_field, done, fail, failed_reply, fmt
from app.services.destinations.base import Field, SendContext, TestResult, register


def _enc(value: str) -> str:
    return quote(str(value), safe="~")


def authorization(method: str, url: str, account: str, secrets: Dict[str, Any], *, nonce: Optional[str] = None, stamp: Optional[str] = None) -> str:
    """The OAuth 1.0a header NetSuite expects: the request's method, address and query are signed with the consumer and token secrets."""
    parts = urlsplit(url)
    oauth = {
        "oauth_consumer_key": secrets.get("consumer_key", ""), "oauth_token": secrets.get("token_id", ""), "oauth_signature_method": "HMAC-SHA256",
        "oauth_timestamp": stamp or str(int(time.time())), "oauth_nonce": nonce or token.token_hex(10), "oauth_version": "1.0",
    }
    pairs = [*parse_qsl(parts.query, keep_blank_values=True), *oauth.items()]
    normalised = "&".join(f"{_enc(k)}={_enc(v)}" for k, v in sorted(pairs, key=lambda kv: (_enc(kv[0]), _enc(kv[1]))))
    base = f"{method.upper()}&{_enc(f'{parts.scheme}://{parts.netloc}{parts.path}')}&{_enc(normalised)}"
    key = f"{_enc(secrets.get('consumer_secret', ''))}&{_enc(secrets.get('token_secret', ''))}"
    oauth["oauth_signature"] = base64.b64encode(hmac.new(key.encode(), base.encode(), hashlib.sha256).digest()).decode()
    return "OAuth " + ", ".join([f'realm="{account.upper()}"', *(f'{k}="{_enc(v)}"' for k, v in oauth.items())])


def host_for(account: str) -> str:
    return f"https://{account.strip().lower().replace('_', '-')}.suitetalk.api.netsuite.com"


def _s(value: str) -> str:
    return value.replace("'", "''")


class NetSuiteAdapter(BillAdapter):
    kind = "netsuite"
    label = "Oracle NetSuite"
    blurb = "Create a vendor bill in NetSuite for each approved invoice."
    fields = [
        Field("account_id", "Account ID", "text", required=True, placeholder="1234567 or 1234567_SB1", help="Setup > Company > Company Information. Sandboxes end in _SB1."),
        Field("consumer_key", "Consumer key", "password", secret=True, required=True, help="From the integration record (Setup > Integration > Manage Integrations)."),
        Field("consumer_secret", "Consumer secret", "password", secret=True, required=True),
        Field("token_id", "Token ID", "password", secret=True, required=True, help="From an access token for a role that may create vendor bills."),
        Field("token_secret", "Token secret", "password", secret=True, required=True),
        Field("expense_account_id", "Expense account (internal ID)", "text", required=True, help="The account the bill’s lines are booked to."),
        Field("subsidiary_id", "Subsidiary (internal ID)", "text", help="Needed in NetSuite OneWorld when a supplier has to be created."),
        create_vendors_field(),
    ]

    async def call(self, ctx_config: Dict[str, Any], secrets: Dict[str, Any], method: str, path: str, body: Any = None, extra: Optional[Dict[str, str]] = None) -> http.Reply:
        url = f"{host_for(ctx_config['account_id'])}{path}"
        headers = {"Authorization": authorization(method, url, ctx_config["account_id"], secrets), "Content-Type": "application/json", "Accept": "application/json", "User-Agent": "Syruss-Delivery/1", **(extra or {})}
        return await http.call(method, url, headers=headers, json_body=body)

    async def suiteql(self, config: Dict[str, Any], secrets: Dict[str, Any], sql: str) -> http.Reply:
        return await self.call(config, secrets, "POST", "/services/rest/query/v1/suiteql", {"q": sql}, {"Prefer": "transient"})

    async def test(self, config: Dict[str, Any], secrets: Dict[str, Any], destination_id: Optional[str] = None) -> TestResult:
        reply = await self.suiteql(config, secrets, "SELECT id FROM vendor FETCH FIRST 1 ROWS ONLY")
        if reply.ok:
            return TestResult(True, "Connected to NetSuite and allowed to read suppliers.")
        return TestResult(False, http.failure_from(reply, "NetSuite")[0], http.excerpt(reply.text, 300) or None)

    async def post_bill(self, ctx: SendContext, bill: Bill) -> SendResult:
        c, s = ctx.config, ctx.secrets
        log = []
        vendor_id = bill.vendor_id
        if not vendor_id:
            found = await self.suiteql(c, s, f"SELECT id FROM vendor WHERE companyName = '{_s(bill.vendor)}' FETCH FIRST 1 ROWS ONLY")
            log.append(f"SuiteQL vendor '{bill.vendor}' -> {found.status}")
            if not found.ok:
                return failed_reply(found, "NetSuite", "\n".join(log))
            items = (found.json() or {}).get("items") or []
            if items:
                vendor_id = str(items[0]["id"])
            elif c.get("create_suppliers", True):
                body: Dict[str, Any] = {"companyName": bill.vendor[:83], "isPerson": False}
                if c.get("subsidiary_id"):
                    body["subsidiary"] = {"id": str(c["subsidiary_id"])}
                made = await self.call(c, s, "POST", "/services/rest/record/v1/vendor", body)
                log.append(f"POST vendor -> {made.status}")
                location = made.headers.get("location", "")
                if not made.ok or not location:
                    return failed_reply(made, "NetSuite", "\n".join(log)) if not made.ok else fail("NetSuite created the supplier but did not say where.", retryable=True, request="\n".join(log), reply=made)
                vendor_id = location.rstrip("/").rsplit("/", 1)[-1]
            else:
                return fail(f"The supplier '{bill.vendor}' is not in NetSuite, and creating suppliers is turned off.", request="\n".join(log))

        dup = await self.suiteql(c, s, f"SELECT id FROM transaction WHERE type = 'VendBill' AND tranId = '{_s(bill.number)}' AND entity = {int(vendor_id)} FETCH FIRST 1 ROWS ONLY")
        if dup.ok and (dup.json() or {}).get("items"):
            return done(str(dup.json()["items"][0]["id"]), "\n".join(log) + f"\nBill {bill.number} already exists", dup)

        record: Dict[str, Any] = {
            "entity": {"id": str(vendor_id)}, "tranId": bill.number[:45], "tranDate": bill.date,
            "expense": {"items": [{"account": {"id": str(c["expense_account_id"])}, "amount": float(fmt(l.amount)), "memo": l.description[:999]} for l in bill.lines]},
        }
        if bill.due:
            record["dueDate"] = bill.due
        if bill.memo:
            record["memo"] = bill.memo[:999]
        if bill.currency:
            record["currency"] = {"refName": bill.currency}
        if c.get("subsidiary_id"):
            record["subsidiary"] = {"id": str(c["subsidiary_id"])}
        reply = await self.call(c, s, "POST", "/services/rest/record/v1/vendorBill", record)
        shown = "\n".join(log) + f"\nPOST vendorBill {bill.number} -> {reply.status}"
        if reply.ok:
            location = reply.headers.get("location", "")
            return done(location.rstrip("/").rsplit("/", 1)[-1] or bill.number, shown, reply)
        return failed_reply(reply, "NetSuite", shown)


register(NetSuiteAdapter())
