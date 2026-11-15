"""
The SMTP door for Keycloak takes mail only with Keycloak's login, only From the configured address, and turns each
message into one "accounts" job (the same message handed over twice is one job).
"""
import asyncio
import smtplib
from email.message import EmailMessage
import pytest
from mail.accounts.base import Outbound
from mail.config import settings
from mail.relay import Relay

PASSWORD = "a-long-random-relay-password"


@pytest.fixture
async def relay(monkeypatch):
    monkeypatch.setattr(settings, "MAIL_RELAY_USERNAME", "keycloak")
    monkeypatch.setattr(settings, "MAIL_RELAY_PASSWORD", PASSWORD)
    monkeypatch.setattr(settings, "MAIL_FROM", "no-reply@syruss.test")
    taken = []

    async def take(job: Outbound):
        taken.append(job)

    r = Relay(take)
    port = await r.start(host="127.0.0.1", port=0)
    yield port, taken
    await r.stop()


def keycloak_mail(sender="Syruss <no-reply@syruss.test>", message_id="<kc-1@keycloak>") -> EmailMessage:
    m = EmailMessage()
    m["From"], m["To"], m["Subject"], m["Message-ID"] = sender, "pat@example.com", "Reset your password", message_id
    m.set_content("Open this link to reset your password.")
    m.add_alternative("<p>Open this link to reset your password.</p>", subtype="html")
    return m


def send(port, message, login=("keycloak", PASSWORD), envelope_from="no-reply@syruss.test"):
    with smtplib.SMTP("127.0.0.1", port, timeout=10) as s:
        if login:
            s.login(*login)
        s.send_message(message, from_addr=envelope_from)


async def test_keycloak_mail_becomes_an_accounts_job(relay):
    port, taken = relay
    await asyncio.to_thread(send, port, keycloak_mail())
    await asyncio.to_thread(send, port, keycloak_mail())  # handed over again: the same job id
    assert len(taken) == 2 and taken[0].id == taken[1].id and taken[0].id.startswith("kc-")
    job = taken[0]
    assert job.purpose == "accounts" and job.to == "pat@example.com" and job.subject == "Reset your password"
    assert "reset your password" in job.text and "<p>" in job.html and job.sender_name == "Syruss"


async def test_strangers_and_other_senders_are_refused(relay):
    port, taken = relay
    with pytest.raises(smtplib.SMTPException):  # no login
        await asyncio.to_thread(send, port, keycloak_mail(), None)
    with pytest.raises(smtplib.SMTPAuthenticationError):
        await asyncio.to_thread(send, port, keycloak_mail(), ("keycloak", "wrong"))
    with pytest.raises(smtplib.SMTPSenderRefused):
        await asyncio.to_thread(send, port, keycloak_mail(), envelope_from="ceo@example.com")
    with pytest.raises(smtplib.SMTPDataError):  # the envelope says us, the message says someone else
        await asyncio.to_thread(send, port, keycloak_mail(sender="CEO <ceo@example.com>"))
    assert taken == []
