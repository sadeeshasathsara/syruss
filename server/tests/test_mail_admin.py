"""
The server's side of the mail service's API (a fake mail service answers): owners set mail up from the admin app's
Email page, every change is audited, connecting Google is bound to the owner who started it, and mail the mail service
read from the support mailbox becomes a ticket, once.
"""
import json
import httpx
import pytest
from sqlalchemy import select
from app.core.config import settings
from app.models.platform import PlatformAuditEntry
from app.models.support import SupportTicket
from app.services import mail_identity, mail_service
from app.services.notifications.mail_relay import collect_waiting
from tests.conftest import AsyncSessionLocal, make_staff, person

SYS = "/api/v1/sysadmin/mail"
OWNER = {"X-Client-ID": "syruss-admin", "X-User-ID": "usr_platform_owner", "X-Username": "Pat Platform", "X-Email": "owner@syruss.test"}
RAW = (b"From: Dana <dana@customer.example>\r\nTo: help@syruss.test\r\nSubject: Can't export\r\nMessage-ID: <x1@customer.example>\r\n\r\n"
       b"The export button does nothing.")


class FakeMailService:
    def __init__(self):
        self.calls, self.inbound = [], {"in-1": RAW}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == f"Bearer {settings.MAIL_SERVICE_TOKEN}"
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, body))
        path = request.url.path
        if path == "/identity":
            return httpx.Response(200, json={"from_address": "no-reply@syruss.test", "support_address": "help@syruss.test"})
        if path == "/status":
            return httpx.Response(200, json={"google": True, "purposes": {
                "support": {"account_id": "google-1", "address": "help@syruss.test", "routed": True, "needs": ["read", "send"], "reads": True}}})
        if path == "/accounts":
            return httpx.Response(200, json=[{"id": "env", "kind": "smtp_imap", "address": "no-reply@syruss.test", "from_environment": True}])
        if path == "/google/start":
            return httpx.Response(200, json={"url": "https://accounts.google.com/o/oauth2/v2/auth?state=sealed"})
        if path == "/google/finish":
            if body["by"] != "usr_platform_owner":
                return httpx.Response(422, json={"detail": "This Google sign-in was started by someone else."})
            return httpx.Response(200, json={"id": "google-1", "address": "help@syruss.test", "granted": ["send", "read"], "purposes": ["support"]})
        if path == "/routes/support":
            return httpx.Response(422, json={"detail": "The account no-reply@syruss.test wasn't given read."})
        if path == "/inbound":
            return httpx.Response(200, json=[{"id": k, "purpose": "support", "account_id": "google-1"} for k in self.inbound])
        if path.startswith("/inbound/"):
            key = path.rsplit("/", 1)[1]
            if request.method == "DELETE":
                self.inbound.pop(key, None)
                return httpx.Response(204)
            return httpx.Response(200, content=self.inbound[key]) if key in self.inbound else httpx.Response(404, json={"detail": "Not waiting here."})
        return httpx.Response(404, json={"detail": "?"})


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr(settings, "MAIL_SERVICE_TOKEN", "server-token-for-tests-123456")
    monkeypatch.setattr(settings, "MAIL_BACKEND", "service")
    service = FakeMailService()
    monkeypatch.setattr(mail_service, "transport", httpx.MockTransport(service))
    mail_identity.forget()
    yield service
    mail_identity.forget()


async def test_owners_set_mail_up_and_every_change_is_audited(client, fake):
    support = {**person("usr_mail_support", "Sam Support"), "X-Client-ID": "syruss-admin"}
    await make_staff(client, support, "support")
    assert (await client.get(f"{SYS}/overview", headers=support)).status_code == 403  # owners only
    overview = (await client.get(f"{SYS}/overview", headers=OWNER)).json()
    assert overview["google"] and overview["redirect_uri"].endswith("/email/google") and overview["accounts"][0]["from_environment"]
    started = await client.post(f"{SYS}/google/start", headers=OWNER, json={"purposes": ["support"]})
    assert started.json()["url"].startswith("https://accounts.google.com/")
    sent = next(b for m, p, b in fake.calls if p == "/google/start")
    assert sent["by"] == "usr_platform_owner" and sent["redirect_uri"].endswith("/email/google") and sent["purposes"] == ["support"]
    done = await client.post(f"{SYS}/google/finish", headers=OWNER, json={"code": "c", "state": "sealed"})
    assert done.status_code == 200 and done.json()["address"] == "help@syruss.test"
    # The mail service's refusals come through as they are
    refused = await client.put(f"{SYS}/routes/support", headers=OWNER, json={"account_id": "env"})
    assert refused.status_code == 422 and "wasn't given read" in refused.json()["detail"]
    async with AsyncSessionLocal() as db:
        actions = [e.action for e in (await db.execute(select(PlatformAuditEntry).where(PlatformAuditEntry.action.like("mail.%")))).scalars()]
    assert "mail.google_connected" in actions
    # The support settings page shows the mailbox the mail service reads
    mailbox = (await client.get("/api/v1/sysadmin/support/settings", headers=OWNER)).json()["mailbox"]
    assert mailbox == {"address": "help@syruss.test", "reading": True, "connected": True}


async def test_mail_read_from_the_support_mailbox_becomes_a_ticket_once(client, fake):
    assert await collect_waiting() == 1
    assert fake.inbound == {}  # taken in, so the mail service lets it go
    assert await collect_waiting() == 0
    async with AsyncSessionLocal() as db:
        tickets = (await db.execute(select(SupportTicket).where(SupportTicket.subject == "Can't export"))).scalars().all()
    assert len(tickets) == 1 and tickets[0].channel == "email" and tickets[0].unverified


async def test_without_the_mail_service_the_page_says_so(client, monkeypatch):
    monkeypatch.setattr(settings, "MAIL_SERVICE_URL", "http://127.0.0.1:9")  # nothing listens there
    monkeypatch.setattr(mail_service, "transport", None)
    res = await client.get(f"{SYS}/overview", headers=OWNER)
    assert res.status_code == 502 and "can't be reached" in res.json()["detail"]
    assert (await client.get("/api/v1/sysadmin/support/settings", headers=OWNER)).json()["mailbox"]["reading"] is None
