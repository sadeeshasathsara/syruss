"""
Confirming an email address with a one-time code before an account exists: the code and who can use it, wrong tries
and expiry, throttling, the people who skip it (Google), and that nothing opens until it is done.
"""
import re
from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy import select, update
from app.core import ratelimit
from app.core.config import settings
from app.models.identity import EmailVerification, User
from app.services.notifications import transport
from app.services import notifications
from tests.conftest import AsyncSessionLocal

API = "/api/v1"


def newcomer(user_id: str, verified: bool = False) -> dict:
    """A person who has just signed up with a password: Keycloak knows the address but hasn't confirmed it."""
    return {"X-User-ID": user_id, "X-Username": "New Comer", "X-Email": f"{user_id}@example.com", "X-Email-Verified": "true" if verified else "false"}


def code_in(mailbox) -> str:
    return re.search(r"\b(\d{6})\b", mailbox[-1].text).group(1)


async def expire_cooldown(user_id: str) -> None:
    async with AsyncSessionLocal() as db:
        await db.execute(update(EmailVerification).where(EmailVerification.user_id == user_id).values(
            sent_at=datetime.now(timezone.utc) - timedelta(seconds=settings.OTP_RESEND_SECONDS + 1)))
        await db.commit()


async def test_a_new_account_must_confirm_its_address_with_a_code_before_anything_else(client, mailbox):
    me = newcomer("usr_otp_new")
    ctx = (await client.get(f"{API}/me/context", headers=me)).json()
    assert ctx["onboarded"] is False and ctx["identity"]["email_verified"] is False

    blocked = await client.post(f"{API}/onboarding", headers=me, json={"first_name": "New", "last_name": "Comer"})
    assert blocked.status_code == 403 and blocked.json()["detail"]["code"] == "email_unverified"

    assert (await client.get(f"{API}/me/email-verification", headers=me)).json()["code_sent"] is False
    sent = await client.post(f"{API}/me/email-verification/send", headers=me)
    assert sent.status_code == 200 and sent.json()["code_sent"] is True and sent.json()["resend_in"] > 0
    assert len(mailbox) == 1 and mailbox[0].to == "usr_otp_new@example.com" and "Syruss" in mailbox[0].subject
    code = code_in(mailbox)
    assert code in mailbox[0].subject and code in mailbox[0].html

    done = await client.post(f"{API}/me/email-verification/confirm", headers=me, json={"code": code})
    assert done.status_code == 200 and done.json()["verified"] is True
    assert (await client.get(f"{API}/me/context", headers=me)).json()["identity"]["email_verified"] is True

    created = await client.post(f"{API}/onboarding", headers=me, json={"first_name": "New", "last_name": "Comer"})
    assert created.status_code == 201
    async with AsyncSessionLocal() as db:
        assert (await db.get(User, "usr_otp_new")).email_verified is True


async def test_the_code_is_not_kept_in_the_clear_and_works_once(client, mailbox):
    me = newcomer("usr_otp_once")
    await client.post(f"{API}/me/email-verification/send", headers=me)
    code = code_in(mailbox)
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(EmailVerification).where(EmailVerification.user_id == "usr_otp_once"))).scalar_one()
        assert code not in row.code_hash and len(row.code_hash) == 64
    assert (await client.post(f"{API}/me/email-verification/confirm", headers=me, json={"code": code})).status_code == 200
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(EmailVerification).where(EmailVerification.user_id == "usr_otp_once"))).scalar_one()
        assert row.code_hash == "" and row.verified_at is not None


async def test_wrong_codes_count_and_five_of_them_lock_the_code(client, mailbox):
    me = newcomer("usr_otp_wrong")
    await client.post(f"{API}/me/email-verification/send", headers=me)
    code = code_in(mailbox)
    wrong = "000000" if code != "000000" else "111111"
    first = await client.post(f"{API}/me/email-verification/confirm", headers=me, json={"code": wrong})
    assert first.status_code == 400 and "4 tries left" in first.json()["detail"]
    for _ in range(4):
        last = await client.post(f"{API}/me/email-verification/confirm", headers=me, json={"code": wrong})
    assert last.status_code == 400 and "Ask for a new one" in last.json()["detail"]
    # Even the right code is refused now; a new one is needed.
    assert (await client.post(f"{API}/me/email-verification/confirm", headers=me, json={"code": code})).status_code == 400
    assert (await client.post(f"{API}/onboarding", headers=me, json={"first_name": "A", "last_name": "B"})).status_code == 403
    await expire_cooldown("usr_otp_wrong")
    assert (await client.post(f"{API}/me/email-verification/send", headers=me)).status_code == 200
    assert (await client.post(f"{API}/me/email-verification/confirm", headers=me, json={"code": code_in(mailbox)})).status_code == 200


async def test_an_expired_code_is_refused(client, mailbox):
    me = newcomer("usr_otp_old")
    await client.post(f"{API}/me/email-verification/send", headers=me)
    async with AsyncSessionLocal() as db:
        await db.execute(update(EmailVerification).where(EmailVerification.user_id == "usr_otp_old").values(
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
        await db.commit()
    res = await client.post(f"{API}/me/email-verification/confirm", headers=me, json={"code": code_in(mailbox)})
    assert res.status_code == 400 and "expired" in res.json()["detail"]


async def test_codes_cannot_be_asked_for_too_fast_or_too_often(client, mailbox):
    me = newcomer("usr_otp_fast")
    assert (await client.post(f"{API}/me/email-verification/send", headers=me)).status_code == 200
    again = await client.post(f"{API}/me/email-verification/send", headers=me)
    assert again.status_code == 429 and "retry-after" in again.headers
    for _ in range(settings.OTP_SENDS_PER_HOUR - 1):
        await expire_cooldown("usr_otp_fast")
        assert (await client.post(f"{API}/me/email-verification/send", headers=me)).status_code == 200
    await expire_cooldown("usr_otp_fast")
    assert (await client.post(f"{API}/me/email-verification/send", headers=me)).status_code == 429  # the hourly limit
    assert len(mailbox) == settings.OTP_SENDS_PER_HOUR


async def test_a_code_belongs_to_one_person_and_one_address(client, mailbox):
    a, b = newcomer("usr_otp_a"), newcomer("usr_otp_b")
    await client.post(f"{API}/me/email-verification/send", headers=a)
    code = code_in(mailbox)
    assert (await client.post(f"{API}/me/email-verification/confirm", headers=b, json={"code": code})).status_code == 400
    assert (await client.post(f"{API}/onboarding", headers=b, json={"first_name": "B", "last_name": "B"})).status_code == 403
    # Someone else's confirmation doesn't carry over if the address changes.
    changed = {**a, "X-Email": "other@example.com"}
    assert (await client.post(f"{API}/me/email-verification/confirm", headers=a, json={"code": code})).status_code == 200
    assert (await client.get(f"{API}/me/email-verification", headers=changed)).json()["verified"] is False


async def test_an_address_the_provider_already_vouches_for_skips_the_step(client, mailbox):
    me = newcomer("usr_otp_google", verified=True)
    assert (await client.get(f"{API}/me/email-verification", headers=me)).json()["verified"] is True
    assert (await client.post(f"{API}/me/email-verification/send", headers=me)).json()["verified"] is True
    assert not mailbox  # nothing to send
    assert (await client.post(f"{API}/onboarding", headers=me, json={"first_name": "G", "last_name": "User"})).status_code == 201


async def test_a_failing_mail_server_is_reported_and_does_not_burn_the_cooldown(client, mailbox, monkeypatch):
    real = transport.send

    async def broken(message):
        raise notifications.EmailError("down")
    monkeypatch.setattr(transport, "send", broken)
    me = newcomer("usr_otp_down")
    res = await client.post(f"{API}/me/email-verification/send", headers=me)
    assert res.status_code == 502 and "couldn't send" in res.json()["detail"]
    monkeypatch.setattr(transport, "send", real)
    assert (await client.post(f"{API}/me/email-verification/send", headers=me)).status_code == 200  # no wait


async def test_the_step_can_be_switched_off_for_places_without_mail(client, monkeypatch):
    monkeypatch.setattr(settings, "REQUIRE_EMAIL_VERIFICATION", False)
    me = newcomer("usr_otp_off")
    assert (await client.post(f"{API}/onboarding", headers=me, json={"first_name": "O", "last_name": "Ff"})).status_code == 201


async def test_accepting_an_invitation_needs_a_confirmed_address(client, mailbox):
    from tests.conftest import create_template, ws
    tpl = await create_template(client)
    me = newcomer("usr_otp_invited")
    me["X-Email"] = "invited.person@example.com"
    # Confirm and make the account, as the site does, then accept.
    await client.post(f"{API}/me/email-verification/send", headers=me)
    await client.post(f"{API}/me/email-verification/confirm", headers=me, json={"code": code_in(mailbox)})
    await client.post(f"{API}/onboarding", headers=me, json={"first_name": "Inv", "last_name": "Ited"})
    res = await client.post(f"{API}/settings/invitations", headers=ws(tpl), json={"email": "invited.person@example.com", "role": "viewer"})
    accepted = await client.post(f"{API}/invitations/{res.json()['token']}/accept", headers=me)
    assert accepted.status_code == 200, accepted.text
