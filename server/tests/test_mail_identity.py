"""
The server has no sender or support address of its own: it asks the mail service (GET /identity) and keeps the answer.
Without an answer, the code that must not guess refuses (a ticket email isn't sent, inbound mail waits).
"""
import httpx
import pytest

from app.core.config import settings
from app.services import mail_identity, mail_service
from app.services.support import mailbox


@pytest.fixture(autouse=True)
def service_backend(monkeypatch):
    monkeypatch.setattr(settings, "MAIL_BACKEND", "service")
    mail_identity.forget()
    yield
    mail_service.transport = None
    mail_identity.forget()


def answering(from_address="no-reply@example.com", support="help@example.com"):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        assert request.headers["Authorization"] == f"Bearer {settings.MAIL_SERVICE_TOKEN}"
        return httpx.Response(200, json={"from_address": from_address, "support_address": support})

    mail_service.transport = httpx.MockTransport(handler)
    return calls


def silent():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    mail_service.transport = httpx.MockTransport(handler)


async def test_it_asks_once_and_keeps_the_answer():
    calls = answering()
    assert not mail_identity.loaded() and mail_identity.current() == mail_identity.DEVELOPMENT
    got = await mail_identity.ensure(required=True)
    assert (got.from_address, got.support_address) == ("no-reply@example.com", "help@example.com") and mail_identity.loaded()
    await mail_identity.ensure(required=True)
    assert calls == ["/identity"]          # not asked again while the answer is fresh


async def test_an_old_answer_stands_when_the_mail_service_is_down(monkeypatch):
    answering()
    await mail_identity.ensure()
    monkeypatch.setattr(mail_identity, "_asked_at", 0.0)   # long ago
    silent()
    assert (await mail_identity.ensure(required=True)).support_address == "help@example.com"


async def test_without_any_answer_the_code_that_must_not_guess_raises():
    silent()
    assert await mail_identity.ensure() == mail_identity.DEVELOPMENT           # a reader that can cope gets the development address
    with pytest.raises(mail_service.MailServiceError):
        await mail_identity.ensure(required=True)
    assert not mail_identity.loaded()


async def test_inbound_support_mail_waits_until_the_addresses_are_known():
    silent()
    with pytest.raises(mail_service.MailServiceError):
        await mailbox.handle(None, b"From: pat@example.com\r\nSubject: hi\r\n\r\nhi")      # never reaches the database
    answering(from_address="no-reply@example.com", support="help@example.com")
    # Our own mail is dropped, before anything is read from the database
    assert await mailbox.handle(None, b"From: help@example.com\r\nSubject: hi\r\n\r\nhi") is None
    assert await mailbox.handle(None, b"From: No-Reply@example.com\r\nSubject: hi\r\n\r\nhi") is None


async def test_a_server_without_a_mail_service_uses_the_development_address(monkeypatch):
    monkeypatch.setattr(settings, "MAIL_BACKEND", "console")
    assert await mail_identity.ensure(required=True) == mail_identity.DEVELOPMENT
