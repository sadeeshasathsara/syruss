"""
Trusted devices: the first browser is trusted outright, any other needs a code emailed to the account, nothing opens
until it is typed, and people can see and remove their browsers.
"""
import re
import secrets
from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy import select, update
from app.core.config import settings
from app.models.identity import SecurityEvent, TrustedDevice
from tests.conftest import AsyncSessionLocal, person, register

API = "/api/v1"
CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
PHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"


@pytest.fixture(autouse=True)
def strict(monkeypatch):
    monkeypatch.setattr(settings, "REQUIRE_TRUSTED_DEVICE", True)


def browser() -> str:
    return secrets.token_urlsafe(32)


def as_(who: dict, token: str) -> dict:
    return {**who, "X-Device-Token": token}


async def check(client, who, token, ua=CHROME):
    return (await client.post(f"{API}/me/device/check", headers=who, json={"token": token, "user_agent": ua, "ip": "203.0.113.7"})).json()


def code_in(mailbox) -> str:
    return re.search(r"\b(\d{6})\b", mailbox[-1].subject).group(1)


async def expire_cooldown(user_id: str):
    async with AsyncSessionLocal() as db:
        await db.execute(update(TrustedDevice).where(TrustedDevice.user_id == user_id).values(code_sent_at=datetime.now(timezone.utc) - timedelta(seconds=settings.OTP_RESEND_SECONDS + 1)))
        await db.commit()


async def test_the_first_browser_is_trusted_and_a_second_one_must_be_confirmed(client, mailbox):
    me = person("usr_dev_a", "Dee Vice")
    await register(me)
    laptop, phone = browser(), browser()

    first = await check(client, me, laptop)
    assert first["status"] == "trusted" and first["device"] == "Chrome on Windows" and not mailbox
    assert (await client.get(f"{API}/me/context", headers=as_(me, laptop))).status_code == 200

    second = await check(client, me, phone, ua=PHONE)
    assert second["status"] == "challenge" and second["device"] == "Safari on iOS" and second["email_sent"] is True and "@" in second["email"]
    assert len(mailbox) == 1 and mailbox[0].to == "usr_dev_a@example.com" and "Safari on iOS" in mailbox[0].text and "203.0.113.7" in mailbox[0].text

    # Until the code is typed the phone gets nothing, though the person is signed in with a valid token.
    blocked = await client.get(f"{API}/me/context", headers=as_(me, phone))
    assert blocked.status_code == 403 and blocked.json()["detail"]["code"] == "device_unverified"
    assert (await client.get(f"{API}/organization/overview", headers=as_(me, phone))).status_code == 403
    assert (await client.get(f"{API}/me/context", headers=me)).status_code == 403  # no browser token at all
    assert (await client.get(f"{API}/me/device", headers=as_(me, phone))).json()["status"] == "challenge"  # the confirming pages stay open

    done = await client.post(f"{API}/me/device/confirm", headers=as_(me, phone), json={"code": code_in(mailbox), "remember": True})
    assert done.status_code == 200 and done.json()["status"] == "trusted"
    assert (await client.get(f"{API}/me/context", headers=as_(me, phone))).status_code == 200

    # The person is told, in the app and in their history.
    bell = (await client.get(f"{API}/notifications", headers={**as_(me, laptop), "X-Org-Slug": "default"})).json()["items"]
    assert any(n["type"] == "security.device_added" and "Safari on iOS" in n["body"] for n in bell)
    log = (await client.get(f"{API}/me/security-events", headers=as_(me, laptop))).json()["items"]
    assert [e["kind"] for e in log][:3] == ["device_trusted", "code_sent", "first_device"]


async def test_a_wrong_code_counts_and_five_lock_it(client, mailbox):
    me = person("usr_dev_wrong", "Wren Gong")
    await register(me)
    await check(client, me, browser())
    odd = browser()
    await check(client, me, odd, ua=PHONE)
    code = code_in(mailbox)
    wrong = "000000" if code != "000000" else "111111"
    first = await client.post(f"{API}/me/device/confirm", headers=as_(me, odd), json={"code": wrong})
    assert first.status_code == 400 and "4 tries left" in first.json()["detail"]
    for _ in range(4):
        last = await client.post(f"{API}/me/device/confirm", headers=as_(me, odd), json={"code": wrong})
    assert "Ask for a new one" in last.json()["detail"]
    assert (await client.post(f"{API}/me/device/confirm", headers=as_(me, odd), json={"code": code})).status_code == 400  # even the right one now
    await expire_cooldown("usr_dev_wrong")
    assert (await client.post(f"{API}/me/device/send", headers=as_(me, odd))).status_code == 200
    assert (await client.post(f"{API}/me/device/confirm", headers=as_(me, odd), json={"code": code_in(mailbox)})).status_code == 200


async def test_codes_cannot_be_requested_too_fast_and_belong_to_one_browser(client, mailbox):
    me = person("usr_dev_fast", "Fay Stone")
    other = person("usr_dev_other", "Otto Mer")
    await register(me)
    await register(other)
    await check(client, me, browser())
    a = browser()
    await check(client, me, a, ua=PHONE)
    assert (await client.post(f"{API}/me/device/send", headers=as_(me, a))).status_code == 429
    code = code_in(mailbox)
    await check(client, other, browser())
    stranger = browser()
    await check(client, other, stranger, ua=PHONE)
    # Someone else's browser can't use my code, and mine can't use theirs.
    assert (await client.post(f"{API}/me/device/confirm", headers=as_(other, stranger), json={"code": code})).status_code == 400
    assert (await client.post(f"{API}/me/device/confirm", headers=as_(me, stranger), json={"code": code})).status_code == 404


async def test_remembering_a_browser_lasts_days_and_not_remembering_lasts_hours(client, mailbox):
    me = person("usr_dev_remember", "Remy Ember")
    await register(me)
    await check(client, me, browser())
    short, long = browser(), browser()
    for token, remember in ((short, False), (long, True)):
        await check(client, me, token, ua=PHONE)
        await client.post(f"{API}/me/device/confirm", headers=as_(me, token), json={"code": code_in(mailbox), "remember": remember})
        await expire_cooldown("usr_dev_remember")
    async with AsyncSessionLocal() as db:
        rows = {d.remember: d for d in (await db.execute(select(TrustedDevice).where(TrustedDevice.user_id == "usr_dev_remember", TrustedDevice.status == "trusted", TrustedDevice.trusted_until.is_not(None)))).scalars()
                if d.token_hash != ""}
    now = datetime.now(timezone.utc)
    assert rows[True].trusted_until - now > timedelta(days=settings.DEVICE_TRUST_DAYS - 1)
    assert rows[False].trusted_until - now < timedelta(hours=settings.DEVICE_SESSION_HOURS + 1)


async def test_trust_expires_and_the_browser_has_to_confirm_again(client, mailbox):
    me = person("usr_dev_expiry", "Ella Expiry")
    await register(me)
    laptop = browser()
    await check(client, me, laptop)
    async with AsyncSessionLocal() as db:
        await db.execute(update(TrustedDevice).where(TrustedDevice.user_id == "usr_dev_expiry").values(trusted_until=datetime.now(timezone.utc) - timedelta(minutes=1)))
        await db.commit()
    assert (await client.get(f"{API}/me/context", headers=as_(me, laptop))).status_code == 403
    again = await check(client, me, laptop)
    assert again["status"] == "challenge" and len(mailbox) == 1  # even its only browser: trust is not forever
    assert (await client.post(f"{API}/me/device/confirm", headers=as_(me, laptop), json={"code": code_in(mailbox)})).status_code == 200
    assert (await client.get(f"{API}/me/context", headers=as_(me, laptop))).status_code == 200


async def test_people_see_their_browsers_and_can_remove_them(client, mailbox):
    me = person("usr_dev_manage", "Mona Ger")
    await register(me)
    laptop, phone = browser(), browser()
    await check(client, me, laptop)
    await check(client, me, phone, ua=PHONE)
    await client.post(f"{API}/me/device/confirm", headers=as_(me, phone), json={"code": code_in(mailbox)})

    listed = (await client.get(f"{API}/me/devices", headers=as_(me, laptop))).json()
    assert {d["label"] for d in listed} == {"Chrome on Windows", "Safari on iOS"}
    assert [d["label"] for d in listed if d["current"]] == ["Chrome on Windows"]
    assert "token" not in str(listed) and "hash" not in str(listed)

    removed = await client.post(f"{API}/me/devices/revoke-others", headers=as_(me, laptop))
    assert removed.json() == {"removed": 1}
    assert (await client.get(f"{API}/me/context", headers=as_(me, phone))).status_code == 403  # gone: it has to start over
    assert [d["label"] for d in (await client.get(f"{API}/me/devices", headers=as_(me, laptop))).json()] == ["Chrome on Windows"]

    only = (await client.get(f"{API}/me/devices", headers=as_(me, laptop))).json()[0]
    stranger = person("usr_dev_stranger", "Sam Stranger")
    await register(stranger)
    await check(client, stranger, browser())
    assert (await client.delete(f"{API}/me/devices/{only['id']}", headers=as_(stranger, browser()))).status_code in (403, 404)
    assert (await client.delete(f"{API}/me/devices/{only['id']}", headers=as_(me, laptop))).status_code == 204


async def test_a_new_account_is_not_asked_for_a_code_and_the_switch_turns_it_all_off(client, monkeypatch):
    fresh = {"X-User-ID": "usr_dev_fresh", "X-Username": "Fresh Face", "X-Email": "usr_dev_fresh@example.com"}
    assert (await client.get(f"{API}/me/context", headers=fresh)).status_code == 200  # no account yet: nothing to protect
    token = browser()
    assert (await check(client, fresh, token))["status"] == "trusted"
    await register(fresh)
    assert (await client.get(f"{API}/me/context", headers=as_(fresh, token))).status_code == 200
    assert (await client.get(f"{API}/me/context", headers=fresh)).status_code == 403
    monkeypatch.setattr(settings, "REQUIRE_TRUSTED_DEVICE", False)
    assert (await client.get(f"{API}/me/context", headers=fresh)).status_code == 200


async def test_a_token_that_is_not_a_browser_token_is_refused(client):
    me = person("usr_dev_bad", "Bad Token")
    await register(me)
    assert (await client.post(f"{API}/me/device/check", headers=me, json={"token": "short", "user_agent": CHROME})).status_code == 400
    assert (await client.get(f"{API}/me/context", headers={**me, "X-Device-Token": "x" * 10})).status_code == 403
