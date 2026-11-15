"""
The server's side of the mail service (a fake mail service answers on the bus): owners set mail up from the admin app's Email page,
every change is audited and sent as a signed command, passwords are sealed before they leave, and mail the mail service read from
the support mailbox becomes a ticket, once.
"""
import json

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.platform import PlatformAuditEntry
from app.models.support import SupportTicket
from app.services import mail_identity
from app.services.mail_bus import bus
from app.services.notifications.mail_relay import take_in
from tests.conftest import AsyncSessionLocal, make_staff, person
from tests.mail_fakes import FakeMail

SYS = "/api/v1/sysadmin/mail"
OWNER = {"X-Client-ID": "syruss-admin", "X-User-ID": "usr_platform_owner", "X-Username": "Pat Platform", "X-Email": "owner@syruss.test"}
RAW = (b"From: Dana <dana@customer.example>\r\nTo: help@syruss.test\r\nSubject: Can't export\r\nMessage-ID: <x1@customer.example>\r\n\r\n"
       b"The export button does nothing.")
SECRET = "s3cret-Pa55word"


@pytest.fixture
async def mail(monkeypatch):
    mail_identity.forget()
    fake = await FakeMail.install(monkeypatch)
    yield fake
    FakeMail.forget()
    mail_identity.forget()


async def audited(prefix="mail."):
    async with AsyncSessionLocal() as db:
        return [e for e in (await db.execute(select(PlatformAuditEntry).where(PlatformAuditEntry.action.like(prefix + "%")))).scalars()]


async def test_owners_see_what_the_mail_service_says_and_only_owners(client, mail):
    support = {**person("usr_mail_support", "Sam Support"), "X-Client-ID": "syruss-admin"}
    await make_staff(client, support, "support")
    assert (await client.get(f"{SYS}/overview", headers=support)).status_code == 403          # owners only
    overview = (await client.get(f"{SYS}/overview", headers=OWNER)).json()
    assert overview["google"] and overview["redirect_uri"].endswith("/email/google")
    assert overview["key"]["fingerprint"] == "A1B2-C3D4-E5F6-7788-99AA"                         # what an owner compares
    assert [a["id"] for a in overview["accounts"]] == ["env", "google-1"] and overview["accounts"][0]["from_environment"]
    assert overview["purposes"]["support"] == {**overview["purposes"]["support"], "address": "help@syruss.test", "routed": True, "reads": True,
                                               "label": "Help", "direction": "both"}
    assert "password" not in json.dumps(overview).lower()


async def test_every_change_is_a_command_for_the_owner_and_audited(client, mail):
    mail.answers["oauth.google.start"] = {"url": "https://accounts.google.com/o/oauth2/v2/auth?state=sealed"}
    mail.answers["oauth.google.finish"] = {"id": "google-1", "address": "help@syruss.test", "granted": ["send", "read"], "purposes": ["support"]}
    started = await client.post(f"{SYS}/google/start", headers=OWNER, json={"purposes": ["support"]})
    assert started.json()["url"].startswith("https://accounts.google.com/")
    kind, payload, by = mail.calls[-1]
    assert kind == "oauth.google.start" and by == "usr_platform_owner" and payload["jobs"] == ["support"] and payload["redirect_uri"].endswith("/email/google")

    done = await client.post(f"{SYS}/google/finish", headers=OWNER, json={"code": "the-one-time-code", "state": "sealed"})
    assert done.status_code == 200 and done.json()["address"] == "help@syruss.test"
    finish = mail.payloads("oauth.google.finish")[0]
    assert "the-one-time-code" not in json.dumps(finish) and finish["sealed"]                  # the code goes sealed

    mail.refuse("job.route", "The account no-reply@syruss.test wasn't given read.")
    refused = await client.put(f"{SYS}/routes/support", headers=OWNER, json={"account_id": "env"})
    assert refused.status_code == 422 and "wasn't given read" in refused.json()["detail"]      # the mail service's refusals come through as they are
    mail.refusals.clear()
    assert (await client.put(f"{SYS}/routes/documents", headers=OWNER, json={"account_id": "google-1"})).status_code == 200
    assert (await client.post(f"{SYS}/accounts/google-1/test", headers=OWNER, params={"purpose": "support"})).status_code == 200
    assert (await client.delete(f"{SYS}/accounts/google-1", headers=OWNER)).status_code == 204
    assert [k for k, _, _ in mail.calls][-4:] == ["job.route", "job.route", "account.test", "account.remove"]

    actions = {e.action for e in await audited()}
    assert {"mail.google_connected", "mail.route_changed", "mail.account_removed"} <= actions
    mailbox = (await client.get("/api/v1/sysadmin/support/settings", headers=OWNER)).json()["mailbox"]
    assert mailbox == {"address": "help@syruss.test", "reading": True, "connected": True}      # from the directory


async def test_a_password_is_sealed_before_it_leaves_and_never_in_a_command(client, mail):
    mail.answers["account.add_imap"] = {"id": "imap-1", "address": "help@example.com", "granted": ["send", "read"]}
    res = await client.post(f"{SYS}/accounts/imap", headers=OWNER, json={
        "address": "help@example.com", "smtp_host": "smtp.example.com", "smtp_username": "help@example.com", "smtp_password": SECRET,
        "imap_host": "imap.example.com", "imap_password": SECRET, "jobs": ["support"]})
    assert res.status_code == 201 and res.json()["id"] == "imap-1"
    sent = mail.payloads("account.add_imap")[0]
    assert SECRET not in json.dumps(sent) and sent["sealed"] and sent["jobs"] == ["support"] and sent["imap_host"] == "imap.example.com"
    assert "smtp_password" not in sent and "imap_password" not in sent
    assert {e.action for e in await audited()} >= {"mail.account_added"}
    audit = json.dumps([e.details for e in await audited()], default=str)
    assert SECRET not in audit

    # A caller that sealed it already (the admin app, in the browser) is sent through as it is
    await client.post(f"{SYS}/accounts/imap", headers=OWNER, json={"address": "x@example.com", "smtp_host": "smtp.example.com", "sealed": "ALREADY-SEALED"})
    assert mail.payloads("account.add_imap")[1]["sealed"] == "ALREADY-SEALED"


async def test_owners_add_change_and_remove_their_own_jobs(client, mail):
    created = await client.post(f"{SYS}/purposes", headers=OWNER, json={"key": "partners", "label": "Partner mail", "direction": "both",
                                                                         "settings": {"sender_name": "Partners"}})
    assert created.status_code == 201
    assert (await client.put(f"{SYS}/purposes/partners", headers=OWNER, json={"label": "Partner mail (EU)"})).status_code == 200
    assert (await client.delete(f"{SYS}/purposes/partners", headers=OWNER)).status_code == 204
    kinds = [k for k, _, _ in mail.calls]
    assert kinds == ["job.create", "job.update", "job.delete"] and mail.payloads("job.update")[0] == {"key": "partners", "label": "Partner mail (EU)"}
    assert {"mail.job_created", "mail.job_changed", "mail.job_removed"} <= {e.action for e in await audited()}
    assert (await client.post(f"{SYS}/purposes", headers=OWNER, json={"key": "x", "label": "X", "direction": "sideways"})).status_code == 422


async def test_the_delivery_log_and_a_retry(client, mail):
    mail.answers["jobs.log"] = {"jobs": [{"id": "j-1", "purpose": "notifications", "status": "dead", "to": "pat@example.com", "subject": "Your code"}]}
    log = (await client.get(f"{SYS}/jobs", headers=OWNER, params={"status": "dead"})).json()
    assert [j["id"] for j in log] == ["j-1"] and mail.payloads("jobs.log")[0] == {"status": "dead", "limit": 100}
    assert (await client.post(f"{SYS}/jobs/j-1/retry", headers=OWNER)).status_code == 200
    assert "mail.retried" in {e.action for e in await audited()}


async def test_mail_read_from_the_support_mailbox_becomes_a_ticket_once_and_is_acknowledged(client, mail):
    assert await take_in("in-1", "support", RAW) is True
    assert mail.published == [(settings.MAIL_INBOUND_ACKS_TOPIC, "in-1", {"id": "in-1"})]         # so the mail service lets it go
    assert await take_in("in-2", "support", RAW) is True                                          # published again: the same ticket, not a second
    async with AsyncSessionLocal() as db:
        tickets = (await db.execute(select(SupportTicket).where(SupportTicket.subject == "Can't export"))).scalars().all()
    assert len(tickets) == 1 and tickets[0].channel == "email" and tickets[0].unverified


async def test_mail_nothing_here_handles_yet_waits_at_the_mail_service(client, mail):
    assert await take_in("in-9", "documents", RAW) is False and mail.published == []              # not acknowledged: it is offered again later
    assert await take_in("in-10", "support", b"", too_large=True) is True                           # too big: reported, then acknowledged
    assert mail.published[-1][1] == "in-10"


async def test_without_the_mail_service_the_page_says_so(client, monkeypatch):
    monkeypatch.setattr(settings, "MAIL_BACKEND", "service")
    bus.state.clear()
    bus.loaded.clear()

    async def nobody(timeout: float = 0.0) -> bool:
        return False

    monkeypatch.setattr(bus, "ready", nobody)
    res = await client.get(f"{SYS}/overview", headers=OWNER)
    assert res.status_code == 503 and "hasn't been heard from" in res.json()["detail"]
    assert (await client.get("/api/v1/sysadmin/support/settings", headers=OWNER)).json()["mailbox"]["reading"] is None
