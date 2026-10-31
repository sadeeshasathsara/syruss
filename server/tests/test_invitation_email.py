"""
Invitations by email: what is sent and to whom, what an inviter sees when it works or doesn't, resending with a
fresh link, and the guards (no double invitations, throttled resends, production mail settings).
"""
import re
from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy import update
from app.core.config import settings
from app.models.identity import OrgInvitation
from app.models.workspace_admin import WorkspaceInvitation
from app.services import notifications
from app.services.notifications import transport
from app.services.notifications.render import email_parts
from tests.conftest import AsyncSessionLocal, create_template, join, person, ws

API = "/api/v1"


async def invitations(client, tpl):
    return (await client.get(f"{API}/settings/invitations", headers=ws(tpl))).json()


async def test_a_workspace_invitation_is_emailed_with_its_link_role_and_inviter(client, mailbox):
    tpl = await create_template(client, "Rosters")
    role = (await client.post(f"{API}/settings/roles", headers=ws(tpl), json={
        "name": "Intake clerk", "description": "Scans and uploads the daily rosters.", "permissions": ["documents.upload"]})).json()
    res = await client.post(f"{API}/settings/invitations", headers=ws(tpl), json={
        "email": "New.Person@Example.com", "role": role["id"], "note": "Welcome aboard, we start Monday."})
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["invitation"]["email"] == "new.person@example.com"
    assert body["link"] == f"http://site.test/invite/{body['token']}"

    assert len(mailbox) == 1
    mail = mailbox[0]
    assert mail.to == "new.person@example.com"
    assert "Rosters" in mail.subject and "invited you" in mail.subject
    for text in (mail.text, mail.html):
        assert body["link"] in text
        assert "Intake clerk" in text and "Scans and uploads the daily rosters." in text
        assert "Welcome aboard, we start Monday." in text
    assert "usr_admin_default" not in mail.subject  # the inviter's name, not an id

    listed = (await invitations(client, tpl))[0]
    assert listed["email_status"] == "SENT" and listed["email_sent_at"] and listed["email_error"] is None


async def test_what_is_typed_into_an_email_is_escaped(client, mailbox):
    tpl = await create_template(client)
    await client.post(f"{API}/settings/invitations", headers=ws(tpl), json={
        "email": "x@example.com", "role": "viewer", "note": '<script>alert("hi")</script>'})
    assert "<script>" not in mailbox[0].html and "&lt;script&gt;" in mailbox[0].html


async def test_when_the_mail_server_fails_the_inviter_sees_it_and_still_has_the_link(client, mailbox, monkeypatch):
    async def broken(message):
        raise notifications.EmailError("The mail server couldn't be reached or didn't accept the message.")
    monkeypatch.setattr(transport, "send", broken)
    tpl = await create_template(client)
    res = await client.post(f"{API}/settings/invitations", headers=ws(tpl), json={"email": "late@example.com", "role": "viewer"})
    assert res.status_code == 201 and res.json()["link"]  # the invitation exists regardless
    listed = (await invitations(client, tpl))[0]
    assert listed["email_status"] == "FAILED" and "mail server" in listed["email_error"]


async def test_an_invitation_can_skip_the_email(client, mailbox):
    tpl = await create_template(client)
    res = await client.post(f"{API}/settings/invitations", headers=ws(tpl), json={"email": "quiet@example.com", "role": "viewer", "send_email": False})
    assert res.status_code == 201 and not mailbox
    assert (await invitations(client, tpl))[0]["email_status"] == "NOT_SENT"


async def test_no_second_invitation_to_the_same_address_nor_to_a_member(client, mailbox):
    tpl = await create_template(client)
    first = await client.post(f"{API}/settings/invitations", headers=ws(tpl), json={"email": "twice@example.com", "role": "viewer"})
    again = await client.post(f"{API}/settings/invitations", headers=ws(tpl), json={"email": "TWICE@example.com", "role": "editor"})
    assert first.status_code == 201 and again.status_code == 409 and "pending invitation" in again.json()["detail"]
    # Once it is revoked, a new one is fine.
    await client.delete(f"{API}/settings/invitations/{first.json()['invitation']['id']}", headers=ws(tpl))
    assert (await client.post(f"{API}/settings/invitations", headers=ws(tpl), json={"email": "twice@example.com", "role": "editor"})).status_code == 201
    assert len(mailbox) == 2


async def test_resending_is_throttled_then_sends_a_fresh_link_and_retires_the_old_one(client, mailbox):
    tpl = await create_template(client)
    first = (await client.post(f"{API}/settings/invitations", headers=ws(tpl), json={"email": "resend@example.com", "role": "viewer"})).json()
    invitation_id = first["invitation"]["id"]
    too_soon = await client.post(f"{API}/settings/invitations/{invitation_id}/resend", headers=ws(tpl))
    assert too_soon.status_code == 429 and "retry-after" in too_soon.headers

    async with AsyncSessionLocal() as db:  # a minute and a bit later
        await db.execute(update(WorkspaceInvitation).where(WorkspaceInvitation.id == invitation_id).values(
            email_sent_at=datetime.now(timezone.utc) - timedelta(seconds=settings.MAIL_RESEND_SECONDS + 5)))
        await db.commit()
    again = await client.post(f"{API}/settings/invitations/{invitation_id}/resend", headers=ws(tpl))
    assert again.status_code == 200 and again.json()["token"] != first["token"]
    assert len(mailbox) == 2 and again.json()["link"] in mailbox[1].text
    assert (await client.get(f"{API}/invitations/{first['token']}")).status_code == 404  # the old link is dead
    assert (await client.get(f"{API}/invitations/{again.json()['token']}")).status_code == 200


async def test_only_people_who_manage_members_can_invite(client, mailbox):
    tpl = await create_template(client)
    other = person("usr_mail_viewer", "Vic Viewer")
    await join(client, tpl, other, "viewer")
    mailbox.clear()
    headers = {**ws(tpl), **other}
    assert (await client.post(f"{API}/settings/invitations", headers=headers, json={"email": "n@example.com", "role": "viewer"})).status_code == 403
    assert not mailbox


async def test_organization_invitations_are_emailed_and_can_be_resent(client, mailbox):
    res = await client.post(f"{API}/organization/invitations", json={"email": "orgguest@example.com", "role": "admin", "note": "Join us."})
    assert res.status_code == 201, res.text
    body = res.json()
    mail = mailbox[0]
    assert mail.to == "orgguest@example.com" and body["link"] in mail.text and "Admin" in mail.text and "Join us." in mail.text
    assert "workspace" not in mail.subject.lower()
    listed = (await client.get(f"{API}/organization/invitations")).json()[0]
    assert listed["email_status"] == "SENT"
    assert (await client.post(f"{API}/organization/invitations", json={"email": "orgguest@example.com"})).status_code == 409
    assert (await client.post(f"{API}/organization/invitations/{listed['id']}/resend")).status_code == 429
    async with AsyncSessionLocal() as db:
        await db.execute(update(OrgInvitation).where(OrgInvitation.id == listed["id"]).values(
            email_sent_at=datetime.now(timezone.utc) - timedelta(minutes=5)))
        await db.commit()
    assert (await client.post(f"{API}/organization/invitations/{listed['id']}/resend")).status_code == 200
    assert len(mailbox) == 2


def test_the_email_has_a_plain_text_part_and_a_link_that_survives_escaping():
    variables = dict(to="a@example.com", inviter="Ann", organization="Acme & Sons", workspace="Q&A", role="Viewer", role_description=None,
                     note=None, url="http://site.test/invite/abc_DEF-123", expires_on="01 November 2026", invitation_id="x")
    mail = email_parts("invitation", variables)
    assert "http://site.test/invite/abc_DEF-123" in mail["text"] and 'href="http://site.test/invite/abc_DEF-123"' in mail["html"]
    assert "Acme &amp; Sons" in mail["html"] and "Acme & Sons" in mail["text"]
    assert re.search(r"01 November 2026", mail["text"])


def test_production_refuses_mail_settings_that_would_send_nowhere(monkeypatch):
    monkeypatch.setattr(settings, "ENV", "production")
    for backend, host in (("console", "smtp.example.com"), ("smtp", "localhost")):
        monkeypatch.setattr(settings, "MAIL_BACKEND", backend)
        monkeypatch.setattr(settings, "SMTP_HOST", host)
        with pytest.raises(RuntimeError):
            notifications.check_mail_config()
    monkeypatch.setattr(settings, "MAIL_BACKEND", "smtp")
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com")
    notifications.check_mail_config()
