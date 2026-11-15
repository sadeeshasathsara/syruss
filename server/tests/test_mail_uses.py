"""
The server's functions that use mail (services/mail_uses.py) and the job each one uses: a function uses its default job until an owner
chooses another in the admin app, mail is addressed to the function, and mail read from a job is handled by the function that job is for.
"""
import json

from app.core.config import settings
from app.services import mail_identity, mail_uses
from app.services.destinations.email import EmailAdapter
from app.services.intake import email as intake_email
from app.services.mail_bus import bus
from app.services.notifications import transport
from app.services.notifications.mail_relay import take_in
from tests.mail_fakes import FakeMail, account, directory, job
from tests.test_mail_admin import OWNER, RAW, SYS, audited, mail  # noqa: F401  (the `mail` fixture)


def use_entry(key, job_key, default, direction="send", assigned=True):
    return {"key": key, "label": key, "description": "", "direction": direction, "needs": {"send": ["send"], "read": ["read"], "both": ["read", "send"]}[direction],
            "default_job": default, "job": job_key, "assigned": assigned}


async def test_a_function_uses_its_default_job_until_one_is_chosen(mail):
    assert mail_uses.job_of(mail_uses.DESTINATION_EMAIL) == "notifications"
    assert mail_uses.use_for_job("support") == "support.mail" and mail_uses.use_for_job("documents") == "documents.inbound"
    assert mail_uses.use_for_job("notifications") is None                     # sending jobs don't hand mail over
    await bus._directory("use:destinations.email", json.dumps(use_entry("destinations.email", "records", "notifications")).encode())
    assert mail_uses.job_of(mail_uses.DESTINATION_EMAIL) == "records" and mail_uses.job_of(mail_uses.APP_NOTIFICATIONS) == "notifications"


async def test_the_server_registers_its_functions_with_the_mail_service(mail):
    await mail_uses.registrar.register()
    kind, payload, by = mail.calls[-1]
    assert kind == "use.register" and by == "system:server"
    assert {u["key"] for u in payload["uses"]} == {"app.notifications", "destinations.email", "support.mail", "documents.inbound"}
    assert all(u["default_job"] and u["direction"] in ("send", "read", "both") for u in payload["uses"])


async def test_email_destinations_name_their_function(monkeypatch):
    monkeypatch.setattr(settings, "MAIL_BACKEND", "memory")
    transport.outbox.clear()
    result = await EmailAdapter().test({"to": "finance@acme.test"}, {})
    assert result.ok and transport.outbox[-1].purpose == "destinations.email"
    assert transport.outbox[-1].purpose != transport.EmailMessage(to="a", subject="b", text="c", html="d").purpose   # not the default one


async def test_document_mail_goes_to_the_job_chosen_for_document_inbound(client, mail, monkeypatch):
    entries = directory()
    entries["account:docs-2"] = account("docs-2", "partners@in.syruss.test", ["read"], ["partner_docs"])
    entries["job:partner_docs"] = job("partner_docs", "Partner documents", "read", "docs-2", "partners@in.syruss.test", True, True, system=False)
    entries["use:documents.inbound"] = use_entry("documents.inbound", "partner_docs", "documents", "read")
    for key in ("account:docs-2", "job:partner_docs", "use:documents.inbound"):
        await bus._directory(key, json.dumps(entries[key]).encode())
    assert intake_email.mailbox() == "partners@in.syruss.test"                 # channel addresses are made from the chosen job's mailbox
    assert mail_uses.use_for_job("partner_docs") == "documents.inbound" and mail_uses.use_for_job("documents") is None
    taken = []

    async def fake_take_in(raw):
        taken.append(raw)
        return "accepted"

    monkeypatch.setattr(intake_email, "take_in", fake_take_in)
    assert await take_in("doc-1", "partner_docs", RAW) is True and taken == [RAW]
    assert mail.published[-1][1] == "doc-1"                                    # acknowledged
    assert await take_in("doc-2", "documents", RAW) is False                   # the old job no longer feeds that function


async def test_an_owner_chooses_the_job_for_a_function(client, mail):
    mail_overview = (await client.get(f"{SYS}/overview", headers=OWNER)).json()
    assert "uses" in mail_overview
    saved = await client.put(f"{SYS}/uses/destinations.email", headers=OWNER, json={"job": "records"})
    assert saved.status_code == 200
    kind, payload, by = mail.calls[-1]
    assert kind == "use.assign" and payload == {"key": "destinations.email", "job": "records"} and by == "usr_platform_owner"
    mail.refuse("use.assign", "Outbound email destinations needs a job that can send; Help desk can't.")
    refused = await client.put(f"{SYS}/uses/destinations.email", headers=OWNER, json={"job": "x"})
    assert refused.status_code == 422 and "needs a job that can send" in refused.json()["detail"]
    assert "mail.use_assigned" in {e.action for e in await audited()}
