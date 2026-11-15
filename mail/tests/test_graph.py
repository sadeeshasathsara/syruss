"""
Microsoft accounts against a fake Microsoft (httpx MockTransport): connecting one, the rotated refresh token being kept,
sending, reading with delta queries, and the provider apps set from the admin app. Real consent can't be tested here, so
every answer is the documented shape of Microsoft's.
"""
import base64
import json
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import select

from mail import apps, sealing
from mail import oauth
from mail.accounts import MemoryAccount
from mail.accounts.base import Capability, MailError
from mail.accounts.graph import GraphAccount, _tokens
from mail.commands import Commands, canonical
from mail.config import settings
from mail.directory import Directory
from mail.operations import Operations, OperationError
from mail.router import Router
from mail.store import SettingRow
from mail.tests.conftest import job

TENANT_TOKEN = "https://login.microsoftonline.com/common/oauth2/v2.0/token"


class FakeMicrosoft:
    def __init__(self):
        self.calls = []
        self.refresh_ok = True
        self.rotate_to = "rt-2"
        self.scope = "openid email profile Mail.Send Mail.Read User.Read"
        self.delta_status = 200
        self.sent = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls.append((request.method, url))
        if url.startswith(TENANT_TOKEN):
            form = parse_qs(request.content.decode())
            assert form["client_id"] == ["ms-client"] and form["client_secret"] == ["ms-secret"]
            if form["grant_type"] == ["refresh_token"]:
                if not self.refresh_ok:
                    return httpx.Response(400, json={"error": "invalid_grant"})
                return httpx.Response(200, json={"access_token": "at-1", "expires_in": 3600, "refresh_token": self.rotate_to})
            assert form["code_verifier"] and form["code"] == ["the-code"]
            return httpx.Response(200, json={"access_token": "at-0", "refresh_token": "rt-1", "scope": self.scope, "expires_in": 3600})
        if url.endswith("/sendMail"):
            self.sent.append(base64.b64decode(request.content))
            return httpx.Response(202)
        if url.startswith("https://graph.microsoft.com/v1.0/me?"):
            return httpx.Response(200, json={"mail": "Help@Example.com", "userPrincipalName": "help@example.onmicrosoft.com"})
        if url.startswith("https://graph.microsoft.com/v1.0/me/mailFolders/inbox/messages/delta"):
            return httpx.Response(200, json={"value": [{"id": "old-1"}], "@odata.deltaLink": "https://graph.microsoft.com/v1.0/me/delta?token=d1"})
        if url.startswith("https://graph.microsoft.com/v1.0/me/delta?token=d1"):
            if self.delta_status != 200:
                return httpx.Response(self.delta_status)
            return httpx.Response(200, json={"value": [{"id": "m1"}, {"id": "gone", "@removed": {"reason": "deleted"}}],
                                             "@odata.deltaLink": "https://graph.microsoft.com/v1.0/me/delta?token=d2"})
        if url.endswith("/messages/m1/$value"):
            return httpx.Response(200, content=b"Subject: hi\r\n\r\nhello")
        return httpx.Response(404)


@pytest.fixture
def fake(monkeypatch):
    apps._cache["microsoft"] = {"client_id": "ms-client", "client_secret": "ms-secret", "tenant": ""}
    _tokens.clear()
    yield FakeMicrosoft()
    apps._cache.clear()


def account(fake, granted=Capability.READ | Capability.SEND, lost=None, kept=None):
    async def keep(account_id, secret):
        kept.append((account_id, secret))
    return GraphAccount(id="microsoft-1", address="help@example.com", name="Syruss", granted=granted, refresh_token="rt-1",
                        on_lost=lost, on_secret=keep if kept is not None else None, transport=httpx.MockTransport(fake))


async def test_a_microsoft_account_sends_a_whole_message_and_keeps_the_new_refresh_token(fake):
    kept = []
    a = account(fake, kept=kept)
    await a.open(Capability.SEND).send(job())
    assert b"From: Syruss <help@example.com>" in fake.sent[0] and b"Your code is 123456" in fake.sent[0]
    assert kept == [("microsoft-1", {"refresh_token": "rt-2"})]           # Microsoft rotates it: the new one is saved
    await a.open(Capability.SEND).send(job("job-2"))
    assert len(kept) == 1 and sum(1 for _, u in fake.calls if u.startswith(TENANT_TOKEN)) == 1   # the access token is kept until it expires
    with pytest.raises(Exception, match="send"):
        account(fake, granted=Capability.READ).open(Capability.SEND)


async def test_reading_follows_the_delta_link_and_restarts_when_it_expires(fake):
    a = account(fake)
    cursor, got = await a.open(Capability.READ).fetch(None)
    assert got == [] and cursor == {"delta": "https://graph.microsoft.com/v1.0/me/delta?token=d1"}   # what was there is left alone
    cursor, got = await a.open(Capability.READ).fetch(cursor)
    assert got == [b"Subject: hi\r\n\r\nhello"] and cursor["delta"].endswith("token=d2")
    fake.delta_status = 410
    cursor, got = await a.open(Capability.READ).fetch({"delta": "https://graph.microsoft.com/v1.0/me/delta?token=d1"})
    assert got == [] and cursor["delta"].endswith("token=d1")                                       # started over from now


async def test_a_refused_sign_in_marks_the_account_lost(fake):
    fake.refresh_ok = False
    lost = []

    async def on_lost(account_id, why):
        lost.append((account_id, why))

    with pytest.raises(MailError, match="connect it again"):
        await account(fake, lost=on_lost).open(Capability.SEND).send(job())
    assert lost and lost[0][0] == "microsoft-1"


async def test_the_test_button_checks_who_signed_in(fake):
    assert (await account(fake).open(Capability.SEND).test()).ok
    other = GraphAccount(id="microsoft-2", address="someone@else.com", name="X", granted=Capability.SEND, refresh_token="rt-1",
                         transport=httpx.MockTransport(fake))
    assert "not someone@else.com" in (await other.open(Capability.SEND).test()).detail


async def test_connecting_microsoft_asks_for_only_what_is_needed_and_saves_what_was_granted(store, fake):
    url = oauth.start("microsoft", ["notifications"], "https://admin.example.com/email/connect/microsoft", by="usr_owner")
    q = parse_qs(urlparse(url).query)
    assert url.startswith("https://login.microsoftonline.com/common/oauth2/v2.0/authorize?")
    assert q["scope"] == ["openid email offline_access User.Read Mail.Send"] and q["code_challenge_method"] == ["S256"]
    state = q["state"][0]
    with pytest.raises(oauth.ConnectError, match="someone else"):
        await oauth.finish(store, provider="microsoft", code="the-code", state=state, redirect_uri="x", by="usr_other", transport=httpx.MockTransport(fake))
    apps._cache["google"] = {"client_id": "g", "client_secret": "s", "tenant": ""}
    with pytest.raises(oauth.ConnectError, match="another provider"):
        await oauth.finish(store, provider="google", code="the-code", state=state, redirect_uri="x", by="usr_owner", transport=httpx.MockTransport(fake))
    done = await oauth.finish(store, provider="microsoft", code="the-code", state=state, redirect_uri="x", by="usr_owner", transport=httpx.MockTransport(fake))
    assert done["address"] == "help@example.com" and set(done["granted"]) == {"send", "read"}
    row = await store.account(done["id"])
    assert row.kind == "graph" and "rt-1" not in row.secret
    state2 = parse_qs(urlparse(oauth.start("microsoft", ["support"], "x", by="usr_owner")).query)["state"][0]
    fake.scope = "openid email Mail.Send"
    with pytest.raises(oauth.ConnectError, match="read"):
        await oauth.finish(store, provider="microsoft", code="the-code", state=state2, redirect_uri="x", by="usr_owner", transport=httpx.MockTransport(fake))
    assert len([r for r in await store.accounts() if r.kind == "graph"]) == 1


async def test_a_tenant_of_its_own_is_used_in_the_sign_in_address(fake):
    apps._cache["microsoft"]["tenant"] = "contoso.com"
    assert oauth.start("microsoft", ["notifications"], "x", by="u").startswith("https://login.microsoftonline.com/contoso.com/oauth2/v2.0/authorize?")


async def test_without_an_app_nothing_can_be_connected(store):
    apps._cache.clear()
    with pytest.raises(oauth.ConnectError, match="Provider apps"):
        oauth.start("microsoft", ["notifications"], "x", by="u")


# ---- provider apps ---------------------------------------------------------------------------------

async def test_a_provider_app_is_kept_sealed_and_never_shown_again(store, fallback):
    ops = Operations(store, Router(store, fallback))
    await ops.app_set("microsoft", "client-123", "super-secret-value", "contoso.com")
    async with store.session() as s:
        raw = (await s.execute(select(SettingRow).where(SettingRow.key == "app:microsoft"))).scalar_one().value
    assert "super-secret-value" not in raw and "client-123" not in raw
    assert apps.describe("microsoft") == {"configured": True, "from_environment": False, "tenant": "contoso.com"}   # never the client id or secret
    assert "super-secret-value" not in json.dumps(apps.describe("microsoft")) and "client-123" not in json.dumps(apps.describe("microsoft"))
    await ops.app_set("microsoft", "", "", "contoso.com")        # a blank client id and secret keep both
    assert apps.get("microsoft")["client_id"] == "client-123" and apps.get("microsoft")["client_secret"] == "super-secret-value"
    await ops.app_set("microsoft", "client-456", "", "")          # a blank secret keeps the one set
    assert apps.get("microsoft")["client_secret"] == "super-secret-value" and apps.get("microsoft")["client_id"] == "client-456"
    apps._cache.clear()
    await apps.load(store)                                          # and it survives a restart
    assert apps.get("microsoft")["client_id"] == "client-456"
    await ops.app_clear("microsoft")
    assert apps.get("microsoft") is None
    with pytest.raises(OperationError, match="client secret"):
        await ops.app_set("microsoft", "client-1", "")
    with pytest.raises(OperationError, match="tenant"):
        await ops.app_set("microsoft", "client-1", "x", "bad tenant!")
    with pytest.raises(OperationError, match="Only Google and Microsoft"):
        await ops.app_set("yahoo", "a", "b")


async def test_google_still_works_from_the_environment(monkeypatch):
    apps._cache.clear()
    assert not apps.configured("google")
    monkeypatch.setattr(settings, "GOOGLE_MAIL_CLIENT_ID", "env-id")
    monkeypatch.setattr(settings, "GOOGLE_MAIL_CLIENT_SECRET", "env-secret")
    assert apps.describe("google") == {"configured": True, "from_environment": True}


async def test_the_server_sets_an_app_through_a_signed_command_with_the_secret_sealed(store, fallback, monkeypatch):
    private, public = sealing.new_signing_keypair()
    monkeypatch.setattr(settings, "MAIL_TRUSTED_SERVER_KEY", base64.b64encode(public).decode())
    replies = []

    async def reply(r):
        replies.append(r)

    commands = Commands(store, Operations(store, Router(store, fallback)), reply, None)
    _, box_public = await store.box()
    sealed = base64.b64encode(sealing.box_seal(box_public, json.dumps({"client_secret": "from-the-browser"}).encode())).decode()
    envelope = {"id": "cmd-app-1", "type": "provider_app.set", "ts": time.time(), "by": "usr_owner",
                "payload": {"provider": "microsoft", "client_id": "cid", "tenant": "common", "sealed": sealed}}
    envelope["sig"] = base64.b64encode(sealing.sign(private, canonical(envelope))).decode()
    reply_sent = await commands.handle(json.dumps(envelope).encode())
    assert reply_sent["ok"] and "from-the-browser" not in json.dumps(replies)
    assert apps.get("microsoft")["client_secret"] == "from-the-browser"
    snapshot = await Directory(store, Router(store, fallback), lambda *a: None).snapshot()
    assert snapshot["meta"]["providers"]["microsoft"]["configured"] and "from-the-browser" not in json.dumps(snapshot)
    await apps.clear(store, "microsoft")
