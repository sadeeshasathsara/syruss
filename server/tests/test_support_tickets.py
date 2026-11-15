"""
Support tickets: opened by a customer, by the agent for the person in its chat, or by email; answered by staff from the
admin app (and by email); threaded by the ticket tag or the email headers; held to service targets.
"""
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
import pytest
from sqlalchemy import select, update
from app.core.agent_token import mint_agent_token
from app.core.config import settings
from app.models.platform import PlatformSetting
from app.models.support import SupportMessage, SupportTicket
from app.services import platform_controls
from app.services.support import mailbox as support_mailbox, sla
from tests.conftest import TENANT, AsyncSessionLocal, create_template, make_staff, person, png_bytes, register, ws
from tests.test_workspace_authorization import chat_token

API = "/api/v1"
SYS = f"{API}/sysadmin/support"
AGENT = "/internal/agent/v1"
OWNER = {"X-Client-ID": "syruss-admin", "X-User-ID": "usr_platform_owner", "X-Username": "Pat Platform", "X-Email": "owner@syruss.test"}
SUPPORT = "help@syruss.test"


@pytest.fixture(autouse=True)
def support_address(monkeypatch):
    monkeypatch.setattr(settings, "SUPPORT_EMAIL_ADDRESS", SUPPORT)


def raw_email(sender: str, subject: str, body: str, **headers) -> bytes:
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = sender, SUPPORT, subject
    m["Message-ID"] = headers.pop("message_id", f"<{abs(hash((sender, subject, body)))}@mail.example>")
    for k, v in headers.items():
        m[k.replace("_", "-")] = v
    m.set_content(body)
    return bytes(m)


async def open_one(client, who=None, subject="Card payment failed", body="The card page said it was declined twice."):
    res = await client.post(f"{API}/support/tickets", headers=who or {}, data={"subject": subject, "body": body, "category": "billing"})
    assert res.status_code == 201, res.text
    return res.json()


async def test_a_customer_opens_a_ticket_and_staff_answer_it_by_email_and_in_the_app(client, mailbox):
    t = await open_one(client)
    assert t["number"].startswith("T-") and t["status"] == "new" and t["first_response_due"]
    confirm = [m for m in mailbox if m.to == "dev@syruss.test"][-1]
    assert confirm.subject == f"[#{t['number']}] Card payment failed" and confirm.purpose == "support" and confirm.reply_to == SUPPORT
    assert confirm.message_id.startswith(f"<{t['number'].lower()}.") and "declined twice" in confirm.text

    inbox = (await client.get(f"{SYS}/tickets", headers=OWNER)).json()["items"]
    assert any(i["id"] == t["id"] and i["organization"] for i in inbox)
    mailbox.clear()
    note = await client.post(f"{SYS}/tickets/{t['id']}/messages", headers=OWNER, data={"body": "Checked Stripe: a 3-D Secure failure.", "internal": "true"})
    assert note.status_code == 201 and mailbox == []                       # a note is never mailed
    answer = await client.post(f"{SYS}/tickets/{t['id']}/messages", headers=OWNER, data={"body": "Hi! Please try again, your bank now allows it."})
    assert answer.status_code == 201
    [mail] = mailbox
    assert mail.subject == f"Re: [#{t['number']}] Card payment failed" and mail.in_reply_to == confirm.message_id and mail.automatic is False
    seen = (await client.get(f"{API}/support/tickets/{t['id']}")).json()
    assert seen["status"] == "pending" and [m["author_kind"] for m in seen["messages"]] == ["customer", "staff"]   # no internal note
    assert (await client.get(f"{SYS}/tickets/{t['id']}", headers=OWNER)).json()["assignee_name"] == "Pat Platform"

    # The customer's reply by email lands on the ticket (quoted history left out) and reopens it.
    reply = raw_email("dev@syruss.test", f"Re: [#{t['number']}] Card payment failed",
                      "It worked, thanks!\n\nOn Mon, Syruss Support wrote:\n> Hi! Please try again")
    async with AsyncSessionLocal() as db:
        assert (await mailbox_handle(db, reply)).id == t["id"]
    seen = (await client.get(f"{API}/support/tickets/{t['id']}")).json()
    assert seen["status"] == "open" and seen["messages"][-1]["body"] == "It worked, thanks!" and seen["messages"][-1]["via"] == "email"


async def mailbox_handle(db, raw):
    return await support_mailbox.handle(db, raw)


async def test_who_sees_a_ticket(client):
    member = person("usr_support_member", "Mia Member")
    await register(member, "member")
    mine = await open_one(client, member, subject="I can't upload")
    owners = await open_one(client, subject="Owner's question")
    seen_by_member = {i["id"] for i in (await client.get(f"{API}/support/tickets", headers=member)).json()["items"]}
    assert mine["id"] in seen_by_member and owners["id"] not in seen_by_member
    assert (await client.get(f"{API}/support/tickets/{owners['id']}", headers=member)).status_code == 404
    seen_by_owner = {i["id"] for i in (await client.get(f"{API}/support/tickets")).json()["items"]}
    assert {mine["id"], owners["id"]} <= seen_by_owner                       # the owner sees the organization's tickets
    bad = await client.post(f"{API}/support/tickets", data={"subject": "A file", "body": "Here is the file you asked for."},
                            files={"files": ("notes.txt", b"just text", "text/plain")})
    assert bad.status_code == 422


async def test_emails_thread_by_headers_and_strangers_and_robots_are_kept_apart(client, mailbox):
    t = await open_one(client, subject="Export question")
    confirm = [m for m in mailbox if m.to == "dev@syruss.test"][-1]
    async with AsyncSessionLocal() as db:
        # No tag in the subject, but it answers our email
        threaded = await support_mailbox.handle(db, raw_email("dev@syruss.test", "Re: Export question", "One more thing.", In_Reply_To=confirm.message_id))
        assert threaded.id == t["id"]
        # The same email twice is taken in once
        again = raw_email("dev@syruss.test", "Re: Export question", "One more thing.", In_Reply_To=confirm.message_id)
        assert await support_mailbox.handle(db, again) is None
        # Out-of-office replies and our own address are dropped
        assert await support_mailbox.handle(db, raw_email("dev@syruss.test", "Auto: away", "I'm away", Auto_Submitted="auto-replied")) is None
        assert await support_mailbox.handle(db, raw_email(SUPPORT, f"[#{t['number']}] loop", "ours")) is None
        # A stranger can't add to someone's ticket: theirs is a new, unverified ticket, and they get no email back
        mailbox.clear()
        stranger = await support_mailbox.handle(db, raw_email("someone@else.example", f"Re: [#{t['number']}] Export question", "Please send me the data"))
        assert stranger.id != t["id"] and stranger.unverified and stranger.tenant_id is None
        assert [m for m in mailbox if m.to == "someone@else.example"] == []
        # Someone forging the requester's address (their mail server says DMARC failed) is a stranger too, even if
        # they add a "pass" of their own
        forged = await support_mailbox.handle(db, raw_email("dev@syruss.test", f"Re: [#{t['number']}] Export question", "Send it to me",
                                                            Authentication_Results="mx.example; spf=fail; dmarc=fail header.from=syruss.test"))
        assert forged.id != t["id"] and forged.unverified and forged.tenant_id is None
        assert [m for m in mailbox if m.to == "dev@syruss.test"] == []
        signed = raw_email("dev@syruss.test", f"Re: [#{t['number']}] Export question", "Signed and fine",
                           Authentication_Results="mx.example; spf=pass; dkim=pass; dmarc=pass header.from=syruss.test")
        assert (await support_mailbox.handle(db, signed)).id == t["id"]
        # A known customer writing fresh gets a ticket in their organization, and the confirmation
        fresh = await support_mailbox.handle(db, raw_email("dev@syruss.test", "New problem", "Something new broke."))
        assert fresh.tenant_id == TENANT and not fresh.unverified and fresh.channel == "email"
    assert any(m.to == "dev@syruss.test" and fresh.number in m.subject for m in mailbox)


class FakeMailbox:
    def __init__(self, messages):
        self.messages = messages

    async def fetch(self, after_uid):
        return 7, [(uid, raw) for uid, raw in self.messages if uid > after_uid]


async def test_the_mailbox_reads_only_what_arrives_after_it_is_switched_on(client):
    async with AsyncSessionLocal() as db:
        old = raw_email("dev@syruss.test", "Old mail", "Sent before support was set up.")
        box = FakeMailbox([(1, old)])
        assert await support_mailbox.poll(db, now=datetime.now(timezone.utc), mailbox=box) == 0          # the first look only notes where it is
        box.messages.append((2, raw_email("dev@syruss.test", "Fresh mail", "Sent after.")))
        assert await support_mailbox.poll(db, now=datetime.now(timezone.utc), mailbox=box) == 1
        assert await support_mailbox.poll(db, now=datetime.now(timezone.utc), mailbox=box) == 0          # once only
        subjects = (await db.execute(select(SupportTicket.subject).where(SupportTicket.subject.in_(("Old mail", "Fresh mail"))))).scalars().all()
        assert subjects == ["Fresh mail"]
        row = await db.get(PlatformSetting, support_mailbox.STATE)
        await db.delete(row)
        await db.commit()
    platform_controls.forget()


async def test_the_agent_opens_a_ticket_only_for_the_person_in_its_chat(client, mailbox):
    tpl = await create_template(client, "Support chat")
    await client.get(f"{API}/settings", headers=ws(tpl))
    chat, token = await chat_token(client, tpl, {})
    made = await client.post(f"{AGENT}/support/tickets", headers=token, json={
        "subject": "Payment slip rejected", "summary": "They uploaded a slip on Monday; it was rejected and they don't know why.", "category": "billing"})
    assert made.status_code == 201, made.text
    number = made.json()["number"]
    async with AsyncSessionLocal() as db:
        t = (await db.execute(select(SupportTicket).where(SupportTicket.number == number))).scalar_one()
        first = (await db.execute(select(SupportMessage).where(SupportMessage.ticket_id == t.id))).scalar_one()
    assert t.channel == "agent" and t.opened_by == "usr_admin_default" and t.related["conversation_id"] == chat["id"]
    assert first.author_kind == "agent" and "Syruss assistant" in first.author_name
    assert any(m.to == "dev@syruss.test" and number in m.subject for m in mailbox)
    work = {"Authorization": f"Bearer {mint_agent_token(TENANT, tpl, 'evt_support_work')}"}
    refused = await client.post(f"{AGENT}/support/tickets", headers=work, json={"subject": "From a work run", "summary": "Nobody asked for this ticket at all."})
    assert refused.status_code == 403


async def test_answering_takes_the_support_role_and_late_tickets_are_flagged(client, mailbox):
    t = await open_one(client, subject="Slow answer please")
    viewer = {**person("usr_support_viewer", "Val Viewer"), "X-Client-ID": "syruss-admin"}
    await make_staff(client, viewer, "viewer")
    assert (await client.get(f"{SYS}/tickets/{t['id']}", headers=viewer)).status_code == 200
    assert (await client.post(f"{SYS}/tickets/{t['id']}/messages", headers=viewer, data={"body": "hi"})).status_code == 403
    helper = {**person("usr_support_helper", "Hal Helper"), "X-Client-ID": "syruss-admin"}
    await make_staff(client, helper, "support")
    assign = await client.patch(f"{SYS}/tickets/{t['id']}", headers=OWNER, json={"assignee_id": "usr_support_helper", "priority": "urgent"})
    assert assign.json()["assignee_name"] == "Hal Helper"
    async with AsyncSessionLocal() as db:
        await db.execute(update(SupportTicket).where(SupportTicket.id == t["id"]).values(first_response_due=datetime.now(timezone.utc) - timedelta(minutes=1)))
        await db.commit()
        mailbox.clear()
        await sla.run(db)
        await sla.run(db)                                                    # told once
        assert (await db.get(SupportTicket, t["id"])).breached_at is not None
    assert [m.to for m in mailbox if "past its target" in m.subject] == ["usr_support_helper@example.com"]
    assert (await client.get(f"{SYS}/summary", headers=OWNER)).json()["breached"] >= 1
    # Saved replies
    saved = await client.post(f"{SYS}/saved-replies", headers=helper, json={"title": "Retry payment", "body": "Hi {{first_name}}, please try again.", "shortcut": "retry"})
    assert saved.status_code == 201
    assert (await client.post(f"{SYS}/saved-replies", headers=helper, json={"title": "Dup", "body": "x" * 5, "shortcut": "retry"})).status_code == 409
