"""
The notification module: one `notify()` call, kinds that declare their channels and templates, invitations that reach a
person's bell as well as their inbox, and a template folder that stays complete.
"""
import pytest
from sqlalchemy import select
from app.models.identity import OrgMember
from app.models.notification import Notification
from app.models.tenant import Tenant
from app.services import notifications
from app.services.notifications import EMAIL_CODE, INVITATION, KINDS, Recipient, notify
from app.services.notifications.render import email_parts, line
from tests.conftest import AsyncSessionLocal, create_template, person, register, ws

API = "/api/v1"

VARS = dict(inviter="Ann", organization="Acme", role="Viewer", url="http://site.test/invite/tok", expires_on="01 November 2026")


async def bell(client, who: dict, org: str = "default") -> list:
    return (await client.get(f"{API}/notifications", headers={**who, "X-Org-Slug": org})).json()["items"]


async def second_org_for(who: dict) -> str:
    """The person also belongs to another organization, which is the one they are looking at."""
    async with AsyncSessionLocal() as db:
        db.add(Tenant(id="tenant_other_" + who["X-User-ID"], name="Other", slug="other-" + who["X-User-ID"].replace("_", "-"), is_active=True))
        await db.flush()
        db.add(OrgMember(tenant_id="tenant_other_" + who["X-User-ID"], user_id=who["X-User-ID"], role="member"))
        await db.commit()
    return "other-" + who["X-User-ID"].replace("_", "-")


async def test_an_invitation_to_someone_with_an_account_reaches_their_bell_and_their_inbox(client, mailbox):
    tpl = await create_template(client, "Rosters")
    sam = person("usr_bell_sam", "Sam Bell")
    await register(sam)
    elsewhere = await second_org_for(sam)

    res = await client.post(f"{API}/settings/invitations", headers=ws(tpl), json={"email": sam["X-Email"], "role": "viewer"})
    assert res.status_code == 201
    assert len(mailbox) == 1  # the inbox

    for org in ("default", elsewhere):  # the bell, whichever organization they are looking at
        items = await bell(client, sam, org)
        assert len(items) == 1 and items[0]["type"] == "invitation.received"
        assert "Rosters" in items[0]["title"] and "Viewer" in items[0]["body"]
        assert items[0]["link"] == res.json()["link"]


async def test_someone_without_an_account_only_gets_the_email(client, mailbox):
    tpl = await create_template(client)

    async def stored() -> int:
        async with AsyncSessionLocal() as db:
            return len((await db.execute(select(Notification).where(Notification.type == "invitation.received"))).all())
    before = await stored()
    await client.post(f"{API}/settings/invitations", headers=ws(tpl), json={"email": "stranger@example.com", "role": "viewer"})
    assert len(mailbox) == 1 and await stored() == before


async def test_resending_replaces_the_bell_entry_and_accepting_or_revoking_clears_it(client, mailbox):
    tpl = await create_template(client)
    sam = person("usr_bell_two", "Sam Two")
    await register(sam)
    first = (await client.post(f"{API}/settings/invitations", headers=ws(tpl), json={"email": sam["X-Email"], "role": "viewer"})).json()
    renewed = (await client.post(f"{API}/settings/invitations/{first['invitation']['id']}/renew", headers=ws(tpl))).json()
    items = await bell(client, sam)
    assert len(items) == 1 and items[0]["link"] == renewed["link"]  # one entry, with the live link

    accepted = await client.post(f"{API}/invitations/{renewed['token']}/accept", headers=sam)
    assert accepted.status_code == 200
    assert await bell(client, sam) == []

    other = person("usr_bell_three", "Sam Three")
    await register(other)
    again = (await client.post(f"{API}/settings/invitations", headers=ws(tpl), json={"email": other["X-Email"], "role": "viewer"})).json()
    assert len(await bell(client, other)) == 1
    await client.delete(f"{API}/settings/invitations/{again['invitation']['id']}", headers=ws(tpl))
    assert await bell(client, other) == []


async def test_the_bell_alone_when_the_email_is_switched_off(client, mailbox):
    tpl = await create_template(client)
    sam = person("usr_bell_quiet", "Sam Quiet")
    await register(sam)
    res = await client.post(f"{API}/settings/invitations", headers=ws(tpl), json={"email": sam["X-Email"], "role": "viewer", "send_email": False})
    assert res.status_code == 201 and not mailbox
    assert len(await bell(client, sam)) == 1


async def test_a_notification_follows_the_person_not_the_organization_and_stays_theirs(client):
    tpl = await create_template(client)
    sam = person("usr_bell_mine", "Sam Mine")
    await register(sam)
    nosy = person("usr_bell_nosy", "Nosy Parker")
    await register(nosy)
    await client.post(f"{API}/settings/invitations", headers=ws(tpl), json={"email": sam["X-Email"], "role": "viewer"})
    assert await bell(client, nosy) == []
    note = (await bell(client, sam))[0]
    assert (await client.post(f"{API}/notifications/{note['id']}/read", headers={**nosy, "X-Org-Slug": "default"})).status_code == 404
    assert (await client.post(f"{API}/notifications/{note['id']}/read", headers={**sam, "X-Org-Slug": "default"})).status_code == 200


async def test_notify_uses_only_the_channels_a_kind_declares_and_a_recipient_can_use(client, mailbox):
    # A kind with no bell never makes one, even when asked.
    delivery = await notify(EMAIL_CODE, Recipient(email="a@example.com", user_id="usr_x"), {"code": "123456", "minutes": 10}, channels=("email", "in_app"))
    assert delivery.email == "sent" and delivery.in_app == "skipped"
    # No address, no email; no account, no bell.
    assert (await notify(INVITATION, Recipient(), {**VARS})).email == "skipped"
    only_bell = await notify(INVITATION, Recipient(email="b@example.com"), {**VARS}, channels=("in_app",))
    assert only_bell.email == "skipped" and only_bell.in_app == "skipped" and len(mailbox) == 1


def test_a_kind_refuses_missing_and_unknown_variables():
    with pytest.raises(ValueError, match="missing role"):
        INVITATION.prepare({k: v for k, v in VARS.items() if k != "role"})
    with pytest.raises(ValueError, match="unknown colour"):
        INVITATION.prepare({**VARS, "colour": "red"})
    assert INVITATION.prepare(VARS)["workspace"] is None  # optional ones default to nothing


@pytest.mark.parametrize("key", sorted(KINDS))
def test_every_kind_has_its_template_files_and_they_render(key):
    """Adding a kind without its files, or a variable the template doesn't know, fails here."""
    kind = KINDS[key]
    variables = {name: f"{name}-value" for name in (*kind.variables, *kind.optional)}
    if kind.email:
        parts = email_parts(kind.email.template, {**kind.prepare(variables), "to": "a@example.com"})
        assert parts["html"].startswith("<!doctype html>") and parts["text"].strip()
        assert line(kind.email.subject, kind.prepare(variables))
    if kind.in_app:
        assert line(kind.in_app.title, kind.prepare(variables))


def test_values_are_escaped_in_the_html_and_left_alone_in_the_text():
    mail = email_parts("invitation", {**VARS, "to": "a@example.com", "workspace": "<b>Q&A</b>", "role_description": None, "note": None, "invitation_id": None})
    assert "<b>Q&A</b>" not in mail["html"] and "&lt;b&gt;Q&amp;A&lt;/b&gt;" in mail["html"]
    assert "<b>Q&A</b>" in mail["text"]
