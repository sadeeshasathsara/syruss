"""
Google accounts against a fake Google (httpx MockTransport), connecting one, reading mailboxes, and the server's API.
"""
import base64
import json
import time
from urllib.parse import parse_qs, urlparse
import httpx
import pytest
from mail import google
from mail.accounts import MemoryAccount
from mail.accounts.base import Capability, MailError
from mail.accounts.gmail import SCOPES, GmailAccount, _tokens
from mail.api import build_api
from mail.config import settings
from mail.inbound import Reader
from mail.router import Router
from mail.store import AccountRow, InboundRow, JobRow, seal
from mail.tests.conftest import job

TOKEN = "service-token-for-tests-1234567890"


class FakeGoogle:
    """Answers like Google's token, userinfo and Gmail endpoints, and records what was asked."""

    def __init__(self):
        self.calls = []
        self.refresh_ok = True
        self.scope = f"openid email {SCOPES[Capability.SEND]} {SCOPES[Capability.READ]}"
        self.history = {"historyId": "200", "history": [{"messagesAdded": [{"message": {"id": "m1"}}]}]}
        self.history_status = 200

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url, self.calls = str(request.url), self.calls + [(request.method, str(request.url))]
        if url.startswith("https://oauth2.googleapis.com/token"):
            form = parse_qs(request.content.decode())
            if form["grant_type"] == ["refresh_token"]:
                if not self.refresh_ok:
                    return httpx.Response(400, json={"error": "invalid_grant"})
                return httpx.Response(200, json={"access_token": "at-1", "expires_in": 3600})
            assert form["code_verifier"] and form["code"] == ["the-code"]
            return httpx.Response(200, json={"access_token": "at-0", "refresh_token": "rt-1", "scope": self.scope, "expires_in": 3600})
        if url.startswith("https://openidconnect.googleapis.com/v1/userinfo"):
            return httpx.Response(200, json={"email": "Help@Example.com", "email_verified": True})
        if url.endswith("/messages/send"):
            raw = base64.urlsafe_b64decode(json.loads(request.content)["raw"] + "==")
            assert b"From: Syruss <help@example.com>" in raw
            return httpx.Response(200, json={"id": "gm-1"})
        if "/profile" in url:
            return httpx.Response(200, json={"emailAddress": "help@example.com", "historyId": "100"})
        if "/history" in url:
            return httpx.Response(self.history_status, json=self.history)
        if "/messages/m1" in url:
            return httpx.Response(200, json={"raw": base64.urlsafe_b64encode(b"Subject: hi\r\n\r\nhello").decode()})
        return httpx.Response(404)


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_MAIL_CLIENT_ID", "client-id")
    monkeypatch.setattr(settings, "GOOGLE_MAIL_CLIENT_SECRET", "client-secret")
    _tokens.clear()
    return FakeGoogle()


def gmail(fake, granted=Capability.READ | Capability.SEND, lost=None):
    return GmailAccount(id="google-1", address="help@example.com", name="Syruss", granted=granted, refresh_token="rt-1",
                        on_lost=lost, transport=httpx.MockTransport(fake))


async def test_a_google_account_sends_and_reads_with_what_it_was_given(fake):
    account = gmail(fake)
    receipt = await account.open(Capability.SEND).send(job())
    assert receipt.provider_id == "gm-1"
    await account.open(Capability.SEND).send(job(id="job-2"))
    assert sum(1 for _, u in fake.calls if "oauth2" in u) == 1  # one access token, kept until it expires
    reader = account.open(Capability.READ)
    cursor, first = await reader.fetch(None)
    assert first == [] and cursor == {"history_id": "100"}  # starts from now
    cursor, mail = await reader.fetch(cursor)
    assert mail == [b"Subject: hi\r\n\r\nhello"] and cursor == {"history_id": "200"}
    fake.history_status = 404  # a history id too old for Google starts over from now
    assert await reader.fetch(cursor) == ({"history_id": "100"}, [])
    send_only = gmail(fake, granted=Capability.SEND)
    with pytest.raises(Exception):
        send_only.open(Capability.READ)


async def test_a_refused_sign_in_marks_the_account_for_reconnecting(fake):
    fake.refresh_ok = False
    lost = []

    async def on_lost(account_id, why):
        lost.append((account_id, why))

    with pytest.raises(MailError, match="connect it again"):
        await gmail(fake, lost=on_lost).open(Capability.SEND).send(job())
    assert lost and lost[0][0] == "google-1"


async def test_connecting_google_asks_for_only_what_is_needed_and_saves_what_was_granted(store, fake):
    url = google.start(["notifications"], "https://admin.example.com/platform/email/google", by="usr_owner")
    q = parse_qs(urlparse(url).query)
    assert q["scope"] == [f"openid email {SCOPES[Capability.SEND]}"] and q["access_type"] == ["offline"]
    assert q["code_challenge_method"] == ["S256"] and "usr_owner" not in url  # the state is sealed
    state = q["state"][0]
    with pytest.raises(google.ConnectError, match="someone else"):
        await google.finish(store, code="the-code", state=state, redirect_uri="x", by="usr_other", transport=httpx.MockTransport(fake))
    done = await google.finish(store, code="the-code", state=state, redirect_uri="x", by="usr_owner", transport=httpx.MockTransport(fake))
    assert done["address"] == "help@example.com" and set(done["granted"]) == {"send", "read"}
    row = await store.account(done["id"])
    assert row.kind == "gmail" and "rt-1" not in row.secret  # sealed
    # The same address again updates the account; unticking a permission the purpose needs is refused
    state2 = parse_qs(urlparse(google.start(["support"], "x", by="usr_owner")).query)["state"][0]
    fake.scope = f"openid email {SCOPES[Capability.SEND]}"
    with pytest.raises(google.ConnectError, match="read"):
        await google.finish(store, code="the-code", state=state2, redirect_uri="x", by="usr_owner", transport=httpx.MockTransport(fake))
    assert len([r for r in await store.accounts() if r.kind == "gmail"]) == 1
    with pytest.raises(google.ConnectError, match="Choose what it is for"):
        google.start(["marketing"], "x", by="usr_owner")


async def test_mailboxes_are_read_from_where_they_left_off(store):
    box = MemoryAccount(id="env")
    announced = []

    async def announce(event):
        announced.append(event)

    reader = Reader(store, Router(store, box), announce)
    box.inbox.append(b"already there")
    assert await reader.read("support") == 0          # the first read notes where the mailbox is
    box.inbox.append(b"Subject: new\r\n\r\nnew")
    assert await reader.read("support") == 1 and await reader.read("support") == 0
    assert announced[0]["purpose"] == "support" and announced[0]["size"] > 0
    async with store.session() as s:
        assert (await s.get(InboundRow, announced[0]["id"])).raw == b"Subject: new\r\n\r\nnew"
    # A fallback that can't read reads nothing
    assert await Reader(store, Router(store, MemoryAccount(id="env", granted=Capability.SEND))).read("support") == 0


async def test_the_api_tells_the_server_its_addresses(store, monkeypatch):
    monkeypatch.setattr(settings, "MAIL_SERVICE_TOKEN", TOKEN)
    monkeypatch.setattr(settings, "MAIL_FROM", "no-reply@example.com")
    app = build_api(store, Router(store, MemoryAccount(id="env", granted=Capability.SEND)))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://mail") as client:
        assert (await client.get("/identity")).status_code == 401
        auth = {"Authorization": f"Bearer {TOKEN}"}
        monkeypatch.setattr(settings, "SUPPORT_EMAIL_ADDRESS", "")
        assert (await client.get("/identity", headers=auth)).json() == {"from_address": "no-reply@example.com", "support_address": "no-reply@example.com"}
        monkeypatch.setattr(settings, "SUPPORT_EMAIL_ADDRESS", "help@example.com")
        assert (await client.get("/identity", headers=auth)).json() == {"from_address": "no-reply@example.com", "support_address": "help@example.com"}


async def test_the_api_is_only_for_the_server_and_never_shows_secrets(store, monkeypatch):
    monkeypatch.setattr(settings, "MAIL_SERVICE_TOKEN", TOKEN)
    app = build_api(store, Router(store, MemoryAccount(id="env", granted=Capability.SEND)))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://mail") as client:
        assert (await client.get("/accounts")).status_code == 401
        assert (await client.get("/accounts", headers={"Authorization": "Bearer wrong"})).status_code == 401
        auth = {"Authorization": f"Bearer {TOKEN}"}
        made = await client.post("/accounts/imap", headers=auth, json={
            "address": "Help@Example.com", "smtp_host": "smtp.example.com", "smtp_password": "s3cret", "imap_host": "imap.example.com",
            "imap_password": "s3cret-too", "by": "usr_owner"})
        assert made.status_code == 201 and set(made.json()["granted"]) == {"send", "read"}
        listing = await client.get("/accounts", headers=auth)
        assert "s3cret" not in listing.text and [a["from_environment"] for a in listing.json()] == [True, False]
        # A route the account can't serve is refused when it is set
        send_only = await client.post("/accounts/imap", headers=auth, json={"address": "no-reply@example.com", "smtp_host": "smtp.example.com"})
        refused = await client.put("/routes/support", headers=auth, json={"account_id": send_only.json()["id"]})
        assert refused.status_code == 422 and "read" in refused.json()["detail"]
        ok = await client.put("/routes/support", headers=auth, json={"account_id": made.json()["id"]})
        assert ok.json()["purposes"]["support"]["routed"] and ok.json()["purposes"]["support"]["reads"]
        # Mail read from a mailbox waits until the server takes it
        async with store.session() as s:
            s.add(InboundRow(id="in-1", purpose="support", account_id="env", raw=b"Subject: x\r\n\r\ny"))
            await s.commit()
        assert [i["id"] for i in (await client.get("/inbound", headers=auth)).json()] == ["in-1"]
        assert (await client.get("/inbound/in-1", headers=auth)).content == b"Subject: x\r\n\r\ny"
        assert (await client.delete("/inbound/in-1", headers=auth)).status_code == 204
        assert (await client.get("/inbound/in-1", headers=auth)).status_code == 404
        # The delivery log, and sending again mail that was given up (while its message is still kept)
        async with store.session() as s:
            s.add(JobRow(id="j-dead", purpose="notifications", to="a@b.c", subject="s", status="dead", attempts=6, payload=seal(job().model_dump()), meta={}))
            await s.commit()
        assert (await client.get("/jobs?status=dead", headers=auth)).json()[0]["retryable"]
        assert (await client.post("/jobs/j-dead/retry", headers=auth)).json()["status"] == "retrying"
        assert (await client.delete("/accounts/env", headers=auth)).status_code == 409
        assert (await client.delete(f"/accounts/{made.json()['id']}", headers=auth)).status_code == 204
