"""
The accounting, storage and mail destinations. Each system is played by a mock written to behave the way that vendor's public documentation
says it does: these tests prove what Syruss sends and how it reacts to each answer. They do not prove the connectors against the vendors' real,
live systems, which needs a real account at each.
"""
import copy
import json
import re
from urllib.parse import parse_qs, quote, urlsplit

import httpx
import pytest
from sqlalchemy import select
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.secrets import unseal
from app.models.delivery import Delivery, Destination
from app.services import delivery_service, source_data
from app.services.delivery_service import delivery_worker
from app.services.destinations import oauth
from app.services.notifications import transport
from tests.conftest import agent, business_plan, create_template, join, person, upload, ws

API = "/api/v1"
AGENT = "/internal/agent/v1"

INVOICE_SCHEMA = {
    "document_type": "supplier_invoice",
    "description": "A supplier's invoice",
    "objects": [
        {"type": "Invoice", "multiple": False, "fields": [
            {"name": "vendor", "type": "string", "required": True}, {"name": "number", "type": "string", "required": True},
            {"name": "date", "type": "date", "required": True}, {"name": "due", "type": "date"}, {"name": "currency", "type": "string"},
            {"name": "total", "type": "number"}, {"name": "tax", "type": "number"},
        ]},
        {"type": "LineItem", "multiple": True, "fields": [
            {"name": "description", "type": "string", "required": True}, {"name": "quantity", "type": "number"},
            {"name": "unit_price", "type": "number"}, {"name": "amount", "type": "number"},
        ]},
    ],
    "validation_rules": [],
}

_numbers = iter(range(1001, 9999))


def invoice(vendor="Acme Ltd", total=None, tax=19.0, lines=None, variant=0):
    """An invoice of 100.00 plus 19.00 tax. Approving a second one in the same workspace needs a `variant`: the duplicate check refuses a look-alike."""
    net = 100.0 + variant * 7
    return {
        "Invoice": [{"vendor": vendor, "number": f"INV-{next(_numbers)}", "date": f"2026-03-{1 + variant:02d}", "due": f"2026-04-{1 + variant:02d}" if variant else "2026-03-31",
                     "currency": "EUR", "total": net + tax if total is None else total, "tax": tax}],
        "LineItem": lines or [{"description": "Widgets", "quantity": 10, "unit_price": net / 10, "amount": net}],
    }


class Vendor:
    """The other side: a handler that is told what each address answers, and remembers what it was asked."""

    def __init__(self):
        self.calls: list[httpx.Request] = []
        self.routes: list = []

    def when(self, method: str, pattern: str, answer):
        self.routes.append((method, re.compile(pattern), answer))
        return self

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        for method, pattern, answer in self.routes:
            if request.method == method and pattern.search(str(request.url)):
                return answer(request) if callable(answer) else answer
        return httpx.Response(404, json={"message": f"nothing at {request.method} {request.url}"})

    def made(self, method: str, pattern: str) -> list[httpx.Request]:
        return [c for c in self.calls if c.method == method and re.search(pattern, str(c.url))]


@pytest.fixture
def vendor(monkeypatch):
    fake = Vendor()
    monkeypatch.setattr(source_data, "HTTP_TRANSPORT", httpx.MockTransport(fake))
    monkeypatch.setattr(settings, "KNOWLEDGE_SOURCES_ALLOW_PRIVATE_HOSTS", True)
    return fake


@pytest.fixture(autouse=True)
async def clean_slate():
    async with AsyncSessionLocal() as db:
        for row in (await db.execute(select(Delivery).where(Delivery.status.in_(("held", "pending", "retrying", "sending"))))).scalars():
            row.status = "cancelled"
        await db.commit()
    delivery_service._told.clear()


async def workspace(client):
    tpl = await create_template(client, "Invoices", INVOICE_SCHEMA)
    await business_plan(tpl)
    return tpl


_variants = iter(range(1, 10_000))


async def approve(client, tpl: str, data=None) -> str:
    """A document the agent read and approved; the first one in a workspace is the plain invoice, later ones differ."""
    doc = await upload(client, tpl)
    res = await client.post(f"{AGENT}/documents/{doc}/record", headers=agent(tpl), json={"data": data or invoice(variant=0 if first_in(tpl) else next(_variants) % 20), "decision": "COMMIT", "confidence": 0.95})
    assert res.status_code == 200, res.text
    return doc


_seen: set = set()


def first_in(tpl: str) -> bool:
    if tpl in _seen:
        return False
    _seen.add(tpl)
    return True


async def connect(client, tpl: str, kind: str, config: dict, secrets: dict, **extra) -> dict:
    mapping = (await client.post(f"{API}/destinations/suggest", headers=ws(tpl), json={"kind": kind})).json()
    res = await client.post(f"{API}/destinations", headers=ws(tpl), json={"kind": kind, "name": f"{kind} test", "config": config, "secrets": secrets, "mapping": mapping, **extra})
    assert res.status_code == 201, res.text
    return res.json()


async def rows(client, tpl: str) -> list:
    return (await client.get(f"{API}/deliveries", headers=ws(tpl))).json()["items"]


async def send_all():
    for _ in range(3):
        await delivery_worker.tick()


# --- the bill every accounting connector builds -----------------------------------------------------------

async def test_the_template_is_matched_to_what_an_accounting_system_needs(client):
    tpl = await workspace(client)
    mapping = (await client.post(f"{API}/destinations/suggest", headers=ws(tpl), json={"kind": "xero"})).json()
    got = {f["target"]: f["value"] for f in mapping["fields"]}
    assert got["vendor_name"] == "Invoice.vendor" and got["invoice_number"] == "Invoice.number" and got["invoice_date"] == "Invoice.date" and got["total"] == "Invoice.total"
    assert mapping["lines"]["from"] == "LineItem" and {f["target"] for f in mapping["lines"]["fields"]} == {"description", "quantity", "unit_price", "amount"}
    unmapped = await client.post(f"{API}/destinations", headers=ws(tpl), json={"kind": "xero", "name": "x", "config": {"client_id": "a", "account_code": "400"}, "secrets": {"client_secret": "b"}})
    assert unmapped.status_code == 422 and "needs its fields mapped" in unmapped.json()["detail"]


async def test_a_bill_that_does_not_add_up_is_stopped_with_a_sentence_before_it_reaches_the_ledger(client, vendor):
    tpl = await workspace(client)
    await connect(client, tpl, "odoo", {"url": "https://odoo.test", "database": "db", "login": "bot"}, {"api_key": "k"})
    await approve(client, tpl, invoice(total=500.0, tax=19.0))
    await send_all()
    [row] = await rows(client, tpl)
    assert row["status"] == "dead" and "lines add up to 119.00 but the invoice total is 500.00" in row["last_error"]
    assert vendor.calls == []


async def test_a_record_without_a_supplier_or_invoice_number_says_what_is_missing(client, vendor):
    tpl = await workspace(client)
    await connect(client, tpl, "odoo", {"url": "https://odoo.test", "database": "db", "login": "bot"}, {"api_key": "k"})
    data = invoice()
    data["Invoice"][0]["vendor"] = "  "
    await approve(client, tpl, data)
    await send_all()
    assert "supplier name is empty" in (await rows(client, tpl))[0]["last_error"]


# --- QuickBooks Online ---------------------------------------------------------------------------------------------

QBO = {"environment": "sandbox", "client_id": "qbo-client"}


def qbo_vendor() -> Vendor:
    v = Vendor()
    v.when("POST", r"oauth\.platform\.intuit\.com", lambda r: httpx.Response(200, json={"access_token": "qbo-access", "refresh_token": "qbo-refresh-1", "expires_in": 3600}))
    state = {"vendors": [], "bills": []}
    v.state = state

    def query(r):
        q = parse_qs(urlsplit(str(r.url)).query)["query"][0]
        if q.startswith("select Id from Vendor"):
            return httpx.Response(200, json={"QueryResponse": {"Vendor": state["vendors"]}})
        if q.startswith("select Id from Bill"):
            return httpx.Response(200, json={"QueryResponse": {"Bill": state["bills"]}})
        if "from Account" in q:
            return httpx.Response(200, json={"QueryResponse": {"Account": [{"Id": "7"}]}})
        return httpx.Response(400, json={"Fault": {"message": q}})

    def make_vendor(r):
        state["vendors"].append({"Id": "58"})
        return httpx.Response(200, json={"Vendor": {"Id": "58"}})

    def make_bill(r):
        state["bills"].append({"Id": "301"})
        return httpx.Response(200, json={"Bill": {"Id": "301"}})

    v.when("GET", r"sandbox-quickbooks\.api\.intuit\.com/v3/company/9130/query", query)
    v.when("POST", r"/vendor\?", make_vendor)
    v.when("POST", r"/bill\?", make_bill)
    v.when("GET", r"/companyinfo/9130", httpx.Response(200, json={"CompanyInfo": {"CompanyName": "Acme Books"}}))
    return v


async def connect_quickbooks(client, tpl, monkeypatch, v: Vendor) -> dict:
    monkeypatch.setattr(source_data, "HTTP_TRANSPORT", httpx.MockTransport(v))
    monkeypatch.setattr(settings, "KNOWLEDGE_SOURCES_ALLOW_PRIVATE_HOSTS", True)
    dest = await connect(client, tpl, "quickbooks", QBO, {"client_secret": "qbo-secret"})
    assert dest["status"] == "paused" and dest["paused_by"] == "setup" and dest["connected"] is False
    start = (await client.post(f"{API}/destinations/{dest['id']}/oauth/start", headers=ws(tpl))).json()["url"]
    assert start.startswith("https://appcenter.intuit.com/connect/oauth2?") and "client_id=qbo-client" in start and "scope=com.intuit.quickbooks.accounting" in start
    state = parse_qs(urlsplit(start).query)["state"][0]
    back = await client.get(f"{API}/destinations/oauth/callback", params={"code": "auth-code", "state": state, "realmId": "9130"}, follow_redirects=False)
    assert back.status_code == 303 and back.headers["location"].endswith(f"/connections/publish/{dest['id']}?connected=1")
    return {**dest, "state": state}


async def test_quickbooks_connects_through_the_vendors_own_sign_in_and_then_sends_bills(client, monkeypatch):
    tpl = await workspace(client)
    v = qbo_vendor()
    dest = await connect_quickbooks(client, tpl, monkeypatch, v)
    token_call = v.made("POST", "oauth.platform.intuit.com")[0]
    assert b"grant_type=authorization_code" in token_call.content and b"code=auth-code" in token_call.content and token_call.headers["authorization"].startswith("Basic ")

    shown = (await client.get(f"{API}/destinations/{dest['id']}", headers=ws(tpl))).json()
    assert shown["status"] == "active" and shown["connected"] is True and shown["config"]["realm_id"] == "9130"
    assert "qbo-access" not in json.dumps(shown) and "qbo-refresh-1" not in json.dumps(shown)
    assert (await client.post(f"{API}/destinations/try", headers=ws(tpl), json={"kind": "quickbooks", "config": shown["config"], "secrets": {}, "destination_id": dest["id"]})).json() == {"ok": True, "message": "Connected to Acme Books.", "detail": None}

    await approve(client, tpl)
    await send_all()
    [row] = await rows(client, tpl)
    assert row["status"] == "delivered" and row["external_ref"] == "301"
    [bill] = v.made("POST", r"/bill\?")
    body = json.loads(bill.content)
    assert bill.headers["authorization"] == "Bearer qbo-access" and "requestid=" in str(bill.url)
    assert body["VendorRef"] == {"value": "58"} and body["DocNumber"].startswith("INV-") and body["TxnDate"] == "2026-03-01" and body["DueDate"] == "2026-03-31"
    assert [(l["Description"], l["Amount"]) for l in body["Line"]] == [("Widgets", 100.0), ("Tax", 19.0)]
    assert body["Line"][0]["AccountBasedExpenseLineDetail"]["AccountRef"] == {"value": "7"} and body["CurrencyRef"] == {"value": "EUR"}
    assert len(v.made("POST", r"/vendor\?")) == 1                      # created the first time

    await approve(client, tpl)
    await send_all()
    assert len(v.made("POST", r"/vendor\?")) == 1                      # found the second time


async def test_a_replayed_quickbooks_delivery_does_not_make_a_second_bill(client, monkeypatch):
    tpl = await workspace(client)
    v = qbo_vendor()
    await connect_quickbooks(client, tpl, monkeypatch, v)
    await approve(client, tpl)
    await send_all()
    [row] = await rows(client, tpl)
    await client.post(f"{API}/deliveries/{row['id']}/replay", headers=ws(tpl))
    await send_all()
    assert len(v.made("POST", r"/bill\?")) == 1 and (await rows(client, tpl))[0]["status"] == "delivered"


async def test_a_quickbooks_sign_in_is_refreshed_once_even_when_several_bills_go_at_once(client, monkeypatch):
    tpl = await workspace(client)
    v = qbo_vendor()
    dest = await connect_quickbooks(client, tpl, monkeypatch, v)
    refreshed = {"n": 0}

    def token(r):
        if b"grant_type=refresh_token" in r.content:
            refreshed["n"] += 1
            return httpx.Response(200, json={"access_token": f"qbo-access-{refreshed['n'] + 1}", "refresh_token": f"qbo-refresh-{refreshed['n'] + 1}", "expires_in": 3600})
        return httpx.Response(200, json={"access_token": "qbo-access", "refresh_token": "qbo-refresh-1", "expires_in": 3600})

    v.routes.insert(0, ("POST", re.compile(r"oauth\.platform\.intuit\.com"), token))
    async with AsyncSessionLocal() as db:
        row = await db.get(Destination, dest["id"])
        from app.core.secrets import seal
        row.secrets = seal({**unseal(row.secrets), "expires_at": "2020-01-01T00:00:00+00:00"})
        await db.commit()
    for _ in range(3):
        await approve(client, tpl)
    await delivery_worker.tick()
    assert refreshed["n"] == 1 and {r["status"] for r in await rows(client, tpl)} == {"delivered"}
    async with AsyncSessionLocal() as db:
        kept = unseal((await db.get(Destination, dest["id"])).secrets)
    assert kept["refresh_token"] == "qbo-refresh-2"                    # the replacement refresh token was saved
    assert all(c.headers["authorization"] == "Bearer qbo-access-2" for c in v.made("POST", r"/bill\?"))


async def test_a_sign_in_link_cannot_be_forged_replayed_or_used_by_someone_else(client, monkeypatch):
    tpl = await workspace(client)
    v = qbo_vendor()
    dest = await connect_quickbooks(client, tpl, monkeypatch, v)
    forged = await client.get(f"{API}/destinations/oauth/callback", params={"code": "x", "state": "not-a-real-state", "realmId": "1"}, follow_redirects=False)
    assert forged.status_code == 303 and "connected" not in forged.headers["location"]
    again = await client.get(f"{API}/destinations/oauth/callback", params={"code": "x", "state": dest["state"], "realmId": "666"}, follow_redirects=False)
    assert "connect_error" in again.headers["location"]                # the state works once
    assert (await client.get(f"{API}/destinations/{dest['id']}", headers=ws(tpl))).json()["config"]["realm_id"] == "9130"
    # Someone else's workspace cannot start a connection for this destination.
    other = await workspace(client)
    assert (await client.post(f"{API}/destinations/{dest['id']}/oauth/start", headers=ws(other))).status_code == 404


async def test_a_declined_sign_in_leaves_the_destination_waiting_to_be_connected(client, monkeypatch):
    tpl = await workspace(client)
    v = qbo_vendor()
    monkeypatch.setattr(source_data, "HTTP_TRANSPORT", httpx.MockTransport(v))
    monkeypatch.setattr(settings, "KNOWLEDGE_SOURCES_ALLOW_PRIVATE_HOSTS", True)
    dest = await connect(client, tpl, "quickbooks", QBO, {"client_secret": "s"})
    state = parse_qs(urlsplit((await client.post(f"{API}/destinations/{dest['id']}/oauth/start", headers=ws(tpl))).json()["url"]).query)["state"][0]
    back = await client.get(f"{API}/destinations/oauth/callback", params={"error": "access_denied", "state": state}, follow_redirects=False)
    assert "connect_error=Access%20was%20not%20given" in back.headers["location"]
    shown = (await client.get(f"{API}/destinations/{dest['id']}", headers=ws(tpl))).json()
    assert shown["status"] == "paused" and shown["connected"] is False
    await approve(client, tpl)
    await send_all()
    assert (await rows(client, tpl))[0]["status"] == "held" and v.made("POST", r"/bill\?") == []


# --- Xero --------------------------------------------------------------------------------------------------------------

async def test_xero_receives_a_draft_bill_with_the_organisation_and_an_idempotency_key(client, monkeypatch):
    tpl = await workspace(client)
    v = Vendor()
    v.when("POST", r"identity\.xero\.com/connect/token", httpx.Response(200, json={"access_token": "xero-access", "refresh_token": "xero-refresh", "expires_in": 1800}))
    v.when("GET", r"api\.xero\.com/connections", httpx.Response(200, json=[{"tenantId": "org-1", "tenantName": "Acme Pty"}]))
    v.when("GET", r"api\.xero\.com/api\.xro/2\.0/Invoices", httpx.Response(200, json={"Invoices": []}))
    v.when("PUT", r"api\.xero\.com/api\.xro/2\.0/Invoices", httpx.Response(200, json={"Invoices": [{"InvoiceID": "inv-9", "HasErrors": False}]}))
    monkeypatch.setattr(source_data, "HTTP_TRANSPORT", httpx.MockTransport(v))
    monkeypatch.setattr(settings, "KNOWLEDGE_SOURCES_ALLOW_PRIVATE_HOSTS", True)
    dest = await connect(client, tpl, "xero", {"client_id": "xc", "account_code": "400"}, {"client_secret": "xs"})
    url = (await client.post(f"{API}/destinations/{dest['id']}/oauth/start", headers=ws(tpl))).json()["url"]
    assert url.startswith("https://login.xero.com/identity/connect/authorize?") and "offline_access" in url
    await client.get(f"{API}/destinations/oauth/callback", params={"code": "c", "state": parse_qs(urlsplit(url).query)["state"][0]}, follow_redirects=False)
    shown = (await client.get(f"{API}/destinations/{dest['id']}", headers=ws(tpl))).json()
    assert shown["status"] == "active" and shown["config"]["tenant_id"] == "org-1"
    assert (await client.post(f"{API}/destinations/try", headers=ws(tpl), json={"kind": "xero", "config": shown["config"], "secrets": {}, "destination_id": dest["id"]})).json()["message"] == "Connected to Acme Pty."

    await approve(client, tpl)
    await send_all()
    [row] = await rows(client, tpl)
    assert row["status"] == "delivered" and row["external_ref"] == "inv-9"
    [put] = v.made("PUT", "Invoices")
    body = json.loads(put.content)["Invoices"][0]
    assert put.headers["xero-tenant-id"] == "org-1" and put.headers["authorization"] == "Bearer xero-access"
    assert put.headers["idempotency-key"].count(":") == 2 and put.headers["idempotency-key"].endswith(":1")
    assert body["Type"] == "ACCPAY" and body["Contact"] == {"Name": "Acme Ltd"} and body["Status"] == "DRAFT" and body["Date"] == "2026-03-01" and body["CurrencyCode"] == "EUR"
    assert [(l["Description"], l["UnitAmount"], l["AccountCode"]) for l in body["LineItems"]] == [("Widgets", 10.0, "400"), ("Tax", 19.0, "400")]

    v.routes.insert(0, ("PUT", re.compile("Invoices"), httpx.Response(200, json={"Invoices": [{"HasErrors": True, "ValidationErrors": [{"Message": "Account code '400' is not a valid code"}]}]})))
    await approve(client, tpl)
    await send_all()
    failed = [r for r in await rows(client, tpl) if r["status"] == "dead"]
    assert failed and "Account code '400' is not a valid code" in failed[0]["last_error"]


# --- NetSuite --------------------------------------------------------------------------------------------------------------

NS = {"account_id": "1234567_SB1", "expense_account_id": "54", "subsidiary_id": "2"}
NS_SECRETS = {"consumer_key": "ck", "consumer_secret": "cs", "token_id": "ti", "token_secret": "ts"}


def test_the_netsuite_signature_matches_an_independent_oauth_implementation():
    oauthlib = pytest.importorskip("oauthlib.oauth1")
    from app.services.destinations.netsuite import authorization
    url = "https://1234567-sb1.suitetalk.api.netsuite.com/services/rest/record/v1/vendorBill?expandSubResources=true"
    ours = authorization("POST", url, "1234567_SB1", NS_SECRETS, nonce="abc123nonce", stamp="1700000000")
    client = oauthlib.Client("ck", client_secret="cs", resource_owner_key="ti", resource_owner_secret="ts", signature_method=oauthlib.SIGNATURE_HMAC_SHA256,
                             realm="1234567_SB1", timestamp="1700000000", nonce="abc123nonce")
    _, theirs, _ = client.sign(url, http_method="POST")
    pick = lambda header: re.search(r'oauth_signature="([^"]+)"', header["Authorization"] if isinstance(header, dict) else header).group(1)
    assert pick(ours) == pick(theirs)
    assert 'realm="1234567_SB1"' in ours and 'oauth_signature_method="HMAC-SHA256"' in ours


async def test_netsuite_finds_or_creates_the_supplier_and_files_a_vendor_bill(client, vendor):
    tpl = await workspace(client)
    state = {"vendors": [], "bills": []}

    def suiteql(r):
        q = json.loads(r.content)["q"]
        if "FROM vendor" in q:
            return httpx.Response(200, json={"items": state["vendors"]})
        if "FROM transaction" in q:
            return httpx.Response(200, json={"items": state["bills"]})
        return httpx.Response(400)

    def new_vendor(r):
        state["vendors"].append({"id": "812"})
        return httpx.Response(204, headers={"Location": "https://1234567-sb1.suitetalk.api.netsuite.com/services/rest/record/v1/vendor/812"})

    def new_bill(r):
        state["bills"].append({"id": "9001"})
        return httpx.Response(204, headers={"Location": "https://1234567-sb1.suitetalk.api.netsuite.com/services/rest/record/v1/vendorBill/9001"})

    vendor.when("POST", r"suiteql", suiteql).when("POST", r"record/v1/vendor$", new_vendor).when("POST", r"record/v1/vendorBill$", new_bill)
    await connect(client, tpl, "netsuite", NS, NS_SECRETS)
    await approve(client, tpl)
    await send_all()
    [row] = await rows(client, tpl)
    assert row["status"] == "delivered" and row["external_ref"] == "9001"
    bill = json.loads(vendor.made("POST", r"vendorBill$")[0].content)
    assert bill["entity"] == {"id": "812"} and bill["tranDate"] == "2026-03-01" and bill["subsidiary"] == {"id": "2"} and bill["currency"] == {"refName": "EUR"}
    assert [(i["amount"], i["memo"], i["account"]) for i in bill["expense"]["items"]] == [(100.0, "Widgets", {"id": "54"}), (19.0, "Tax", {"id": "54"})]
    assert vendor.calls[0].headers["authorization"].startswith("OAuth realm=\"1234567_SB1\"") and vendor.calls[0].headers["prefer"] == "transient"
    await client.post(f"{API}/deliveries/{row['id']}/replay", headers=ws(tpl))
    await send_all()
    assert len(vendor.made("POST", r"vendorBill$")) == 1                  # filed once


async def test_a_quote_in_a_supplier_name_cannot_change_the_query(client, vendor):
    tpl = await workspace(client)
    seen = []
    vendor.when("POST", r"suiteql", lambda r: (seen.append(json.loads(r.content)["q"]), httpx.Response(200, json={"items": [{"id": "5"}]}))[1])
    vendor.when("POST", r"record/v1/vendorBill$", httpx.Response(204, headers={"Location": "x/vendorBill/1"}))
    await connect(client, tpl, "netsuite", NS, NS_SECRETS)
    await approve(client, tpl, invoice(vendor="O'Brien' OR 1=1 --"))
    await send_all()
    assert "companyName = 'O''Brien'' OR 1=1 --'" in seen[0]


# --- SAP S/4HANA ---------------------------------------------------------------------------------------------------------------

SAP = {"base_url": "https://sap.test", "auth_type": "basic", "username": "SYRUSS", "company_code": "1710", "gl_account": "0000400000"}


def sap_vendor(v: Vendor, suppliers=True, duplicate=False):
    seen = {"posts": 0}

    def service_root(r):
        assert r.headers["x-csrf-token"] == "Fetch"
        return httpx.Response(200, headers={"x-csrf-token": "tok-77", "set-cookie": "sap-usercontext=abc; Path=/"}, json={})

    def supplier(r):
        return httpx.Response(200, json={"d": {"results": [{"Supplier": "100234"}] if suppliers else []}})

    def invoices_get(r):
        return httpx.Response(200, json={"d": {"results": [{"SupplierInvoice": "5105600001", "FiscalYear": "2026"}] if duplicate else []}})

    def post(r):
        assert r.headers["x-csrf-token"] == "tok-77" and "sap-usercontext=abc" in r.headers["cookie"]
        seen["posts"] += 1
        return httpx.Response(201, json={"d": {"SupplierInvoice": "5105600002", "FiscalYear": "2026"}})

    v.when("GET", r"API_SUPPLIERINVOICE_PROCESS_SRV/\?", service_root)
    v.when("GET", r"A_Supplier\?", supplier)
    v.when("GET", r"A_SupplierInvoice\?", invoices_get)
    v.when("POST", r"A_SupplierInvoice\?", post)
    return seen


async def test_sap_posts_a_supplier_invoice_with_its_csrf_token_and_cookies(client, vendor):
    tpl = await workspace(client)
    seen = sap_vendor(vendor)
    await connect(client, tpl, "sap", SAP, {"password": "pw"})
    await approve(client, tpl)
    await send_all()
    [row] = await rows(client, tpl)
    assert row["status"] == "delivered" and row["external_ref"] == "5105600002/2026" and seen["posts"] == 1
    post = vendor.made("POST", "A_SupplierInvoice")[0]
    body = json.loads(post.content)
    assert post.headers["authorization"].startswith("Basic ")
    assert body["CompanyCode"] == "1710" and body["InvoicingParty"] == "100234" and body["SupplierInvoiceIDByInvcgParty"].startswith("INV-") and body["InvoiceGrossAmount"] == "119.00"
    assert [(i["GLAccount"], i["SupplierInvoiceItemAmount"], i["DebitCreditCode"]) for i in body["to_SuplrInvcItemGLAcct"]] == [("0000400000", "100.00", "S"), ("0000400000", "19.00", "S")]


async def test_sap_never_invents_a_supplier_and_does_not_post_the_same_invoice_twice(client, vendor):
    tpl = await workspace(client)
    sap_vendor(vendor, suppliers=False)
    await connect(client, tpl, "sap", SAP, {"password": "pw"})
    await approve(client, tpl)
    await send_all()
    [row] = await rows(client, tpl)
    assert row["status"] == "dead" and "is not in SAP" in row["last_error"] and vendor.made("POST", "A_SupplierInvoice") == []

    vendor.calls.clear()
    vendor.routes.clear()
    seen = sap_vendor(vendor, duplicate=True)
    await client.post(f"{API}/deliveries/{row['id']}/replay", headers=ws(tpl))
    await send_all()
    assert (await rows(client, tpl))[0]["status"] == "delivered" and seen["posts"] == 0


# --- Dynamics 365 Business Central --------------------------------------------------------------------------------------------------

BC = {"tenant_id": "tenant-guid", "environment": "Production", "company_id": "co-guid", "client_id": "app-id", "gl_account": "6100"}


def bc_vendor(v: Vendor, line_fails=False):
    state = {"deleted": 0, "lines": 0}
    v.when("POST", r"login\.microsoftonline\.com/tenant-guid/oauth2/v2\.0/token", httpx.Response(200, json={"access_token": "bc-token"}))
    v.when("GET", r"/vendors\?", httpx.Response(200, json={"value": []}))
    v.when("POST", r"/vendors$", httpx.Response(201, json={"id": "v-1", "number": "V00010"}))
    v.when("GET", r"/purchaseInvoices\?", httpx.Response(200, json={"value": []}))
    v.when("POST", r"/purchaseInvoices$", httpx.Response(201, json={"id": "pi-1", "number": "PI000042"}))

    def line(r):
        state["lines"] += 1
        return httpx.Response(400, json={"error": {"message": "Account does not exist"}}) if line_fails and state["lines"] == 2 else httpx.Response(201, json={})

    def delete(r):
        state["deleted"] += 1
        return httpx.Response(204)

    v.when("POST", r"purchaseInvoiceLines$", line)
    v.when("DELETE", r"/purchaseInvoices\(pi-1\)", delete)
    return state


async def test_business_central_creates_the_invoice_then_its_lines(client, vendor):
    tpl = await workspace(client)
    bc_vendor(vendor)
    await connect(client, tpl, "dynamics", BC, {"client_secret": "shh"})
    await approve(client, tpl)
    await send_all()
    [row] = await rows(client, tpl)
    assert row["status"] == "delivered" and row["external_ref"] == "PI000042"
    assert b"scope=https%3A%2F%2Fapi.businesscentral.dynamics.com%2F.default" in vendor.made("POST", "oauth2/v2.0/token")[0].content
    header = json.loads(vendor.made("POST", r"/purchaseInvoices$")[0].content)
    assert header["vendorNumber"] == "V00010" and header["vendorInvoiceNumber"].startswith("INV-") and header["invoiceDate"] == "2026-03-01"
    lines = [json.loads(c.content) for c in vendor.made("POST", "purchaseInvoiceLines$")]
    assert [(l["lineObjectNumber"], l["description"]) for l in lines] == [("6100", "Widgets"), ("6100", "Tax")]
    assert "companies(co-guid)" in str(vendor.calls[1].url)


async def test_a_half_made_business_central_invoice_is_deleted_so_a_retry_starts_clean(client, vendor):
    tpl = await workspace(client)
    state = bc_vendor(vendor, line_fails=True)
    await connect(client, tpl, "dynamics", BC, {"client_secret": "shh"})
    await approve(client, tpl)
    await send_all()
    [row] = await rows(client, tpl)
    assert row["status"] == "dead" and "Account does not exist" in row["last_error"] and state["deleted"] == 1
    assert vendor.made("DELETE", "pi-1")[0].headers["if-match"] == "*"


# --- Odoo ---------------------------------------------------------------------------------------------------------------------------------

ODOO = {"url": "https://odoo.test", "database": "prod", "login": "bot@acme.test"}


def odoo_vendor(v: Vendor, partners=None, bills=None, refuse_bill=None):
    calls = []

    def rpc(r):
        p = json.loads(r.content)["params"]
        args = p["args"]
        if p["service"] == "common":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": 7 if args[2] == "good-key" else False})
        model, method = args[3], args[4]
        calls.append((model, method, args[5]))
        if model == "res.partner" and method == "search":
            return httpx.Response(200, json={"result": partners or []})
        if model == "res.partner" and method == "create":
            return httpx.Response(200, json={"result": 41})
        if model == "account.move" and method == "search":
            return httpx.Response(200, json={"result": bills or []})
        if model == "res.currency":
            return httpx.Response(200, json={"result": [3]})
        if model == "account.move" and method == "create":
            if refuse_bill:
                return httpx.Response(200, json={"error": {"message": "Odoo Server Error", "data": {"message": refuse_bill}}})
            return httpx.Response(200, json={"result": 88})
        return httpx.Response(200, json={"result": None})

    v.when("POST", r"odoo\.test/jsonrpc", rpc)
    return calls


async def test_odoo_signs_in_with_an_api_key_and_creates_a_vendor_bill(client, vendor):
    tpl = await workspace(client)
    calls = odoo_vendor(vendor)
    await connect(client, tpl, "odoo", ODOO, {"api_key": "good-key"})
    await approve(client, tpl)
    await send_all()
    [row] = await rows(client, tpl)
    assert row["status"] == "delivered" and row["external_ref"] == "88"
    bill = next(a for m, meth, a in calls if m == "account.move" and meth == "create")[0]
    assert bill["move_type"] == "in_invoice" and bill["partner_id"] == 41 and bill["ref"].startswith("INV-") and bill["invoice_date"] == "2026-03-01" and bill["currency_id"] == 3
    assert [(l[2]["name"], l[2]["price_unit"]) for l in bill["invoice_line_ids"]] == [("Widgets", 10.0), ("Tax", 19.0)]


async def test_odoo_reports_a_wrong_key_and_the_reason_it_gives_for_a_refused_bill(client, vendor):
    tpl = await workspace(client)
    odoo_vendor(vendor)
    bad = await client.post(f"{API}/destinations/try", headers=ws(tpl), json={"kind": "odoo", "config": ODOO, "secrets": {"api_key": "wrong"}})
    assert bad.json() == {"ok": False, "message": "Odoo refused the database, user or API key.", "detail": None}
    vendor.routes.clear()
    odoo_vendor(vendor, partners=[41], refuse_bill="Fiscal period is closed")
    await connect(client, tpl, "odoo", ODOO, {"api_key": "good-key"})
    await approve(client, tpl)
    await send_all()
    [row] = await rows(client, tpl)
    assert row["status"] == "dead" and "Fiscal period is closed" in row["last_error"]


# --- S3-compatible storage (against the real test MinIO) ---------------------------------------------------------------------------------------

def s3_config(prefix: str, **extra):
    return {"endpoint": settings.MINIO_ENDPOINT, "bucket": settings.MINIO_BUCKET_NAME, "prefix": prefix, "secure": settings.MINIO_SECURE, **extra}


S3_SECRETS = lambda: {"access_key": settings.MINIO_ROOT_USER, "secret_key": settings.MINIO_ROOT_PASSWORD}  # noqa: E731


async def test_records_are_written_to_a_bucket_as_json_with_a_fixed_name(client, monkeypatch):
    from app.services.storage_service import storage_service
    monkeypatch.setattr(settings, "KNOWLEDGE_SOURCES_ALLOW_PRIVATE_HOSTS", True)
    tpl = await workspace(client)
    config = s3_config(f"deliveries/{tpl}", include_document=True)
    tried = await client.post(f"{API}/destinations/try", headers=ws(tpl), json={"kind": "s3", "config": config, "secrets": S3_SECRETS()})
    assert tried.json()["ok"] is True, tried.json()
    await connect(client, tpl, "s3", config, S3_SECRETS(), mapping={"mode": "record"})
    doc = await approve(client, tpl)
    await send_all()
    [row] = await rows(client, tpl)
    assert row["status"] == "delivered" and row["external_ref"] == f"deliveries/{tpl}/{doc}/revision-1.json"
    stored = json.loads(storage_service.download_file(row["external_ref"]))
    assert stored["type"] == "document.completed" and stored["record"]["data"]["Invoice"][0]["vendor"] == "Acme Ltd" and "storage_key" not in json.dumps(stored)
    assert storage_service.object_size(f"deliveries/{tpl}/{doc}/roster.png") is not None       # the original image beside it
    await client.post(f"{API}/deliveries/{row['id']}/replay", headers=ws(tpl))
    await send_all()
    assert (await rows(client, tpl))[0]["status"] == "delivered"                                  # the same name is written again, not a second file


async def test_wrong_storage_keys_are_a_permanent_failure_that_says_so(client, monkeypatch):
    monkeypatch.setattr(settings, "KNOWLEDGE_SOURCES_ALLOW_PRIVATE_HOSTS", True)
    tpl = await workspace(client)
    await connect(client, tpl, "s3", s3_config(f"deliveries/{tpl}"), {"access_key": "nobody", "secret_key": "nothing"}, mapping={"mode": "record"})
    await approve(client, tpl)
    await send_all()
    [row] = await rows(client, tpl)
    assert row["status"] == "dead" and row["attempts"] == 1 and ("access key" in row["last_error"] or "refused access" in row["last_error"])


# --- Email ---------------------------------------------------------------------------------------------------------------------------------------------

async def test_an_approved_record_is_emailed_with_the_record_attached(client, mailbox):
    tpl = await workspace(client)
    await connect(client, tpl, "email", {"to": "ap@acme.test, boss@acme.test", "subject": "Approved: {filename} ({external_id})", "attach": "both"}, {}, mapping={"mode": "record"})
    await approve(client, tpl)
    await send_all()
    [row] = await rows(client, tpl)
    assert row["status"] == "delivered"
    [mail] = [m for m in mailbox if m.subject.startswith("Approved:")]
    assert mail.to == "ap@acme.test, boss@acme.test" and "Invoice › vendor: Acme Ltd" in mail.text
    assert [(n, k) for n, _, k in mail.attachments] == [("roster.png.json", "application/json"), ("roster.png.csv", "text/csv")]
    csv_body = mail.attachments[1][1].decode()
    assert csv_body.splitlines()[0] == "object,entry,field,value" and "Invoice,1,vendor,Acme Ltd" in csv_body


async def test_email_destinations_are_limited_and_headers_cannot_be_injected(client, mailbox):
    tpl = await workspace(client)
    many = ",".join(f"p{i}@acme.test" for i in range(6))
    res = await client.post(f"{API}/destinations", headers=ws(tpl), json={"kind": "email", "name": "x", "config": {"to": many}})
    assert res.status_code == 422 and "At most 5" in json.dumps(res.json())
    bad = await client.post(f"{API}/destinations", headers=ws(tpl), json={"kind": "email", "name": "x", "config": {"to": "not-an-address"}})
    assert bad.status_code == 422
    await connect(client, tpl, "email", {"to": "ap@acme.test", "subject": "Hi\r\nBcc: spam@evil.test", "attach": "json"}, {}, mapping={"mode": "record"})
    await approve(client, tpl)
    await send_all()
    [mail] = [m for m in mailbox if m.to == "ap@acme.test" and "Bcc" in m.subject]
    assert "\n" not in mail.subject and "\r" not in mail.subject


# --- the catalogue ---------------------------------------------------------------------------------------------------------------------------------------------

async def test_the_catalogue_lists_every_system_with_what_connecting_asks_for(client):
    tpl = await workspace(client)
    catalogue = (await client.get(f"{API}/destinations/catalogue", headers=ws(tpl))).json()
    kinds = {k["kind"]: k for k in catalogue}
    assert set(kinds) == {"webhook", "rest", "s3", "email", "quickbooks", "xero", "netsuite", "sap", "dynamics", "odoo"}
    assert kinds["quickbooks"]["uses_oauth"] and kinds["xero"]["uses_oauth"] and not kinds["sap"]["uses_oauth"]
    assert all(k["mapping_required"] for k in catalogue if k["category"] == "accounting") and not kinds["webhook"]["mapping_required"]
    assert any(f["key"] == "client_secret" and f["secret"] for f in kinds["xero"]["fields"])
    viewer = person("usr_cat_viewer", "Vi Viewer")
    await join(client, tpl, viewer, "viewer")
    assert (await client.get(f"{API}/destinations/catalogue", headers={**viewer, **ws(tpl)})).status_code == 403
