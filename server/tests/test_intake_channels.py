"""Documents arriving by e-mail (read from the documents mailbox by the mail service) and SFTP, and files scanned for viruses on the way in:
held to the same rules as the API."""
import asyncio
import struct
from email.message import EmailMessage

import asyncssh
import pytest
from sqlalchemy import select
from app.core import ratelimit
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.document import Document
from app.models.intake import IntakeEvent
from app.services.intake import email as intake_email
from app.services.notifications import mail_relay
from app.services.intake.sftp import sftp_receiver
from tests.conftest import business_plan, create_template, png_bytes, ws
from tests.mail_fakes import FakeMail, account, directory, job

API = "/api/v1"


@pytest.fixture(autouse=True)
def fresh_limits():
    ratelimit.reset()


@pytest.fixture
async def mail_port(monkeypatch):
    """A mail service whose Document inbound job reads docs@in.syruss.test."""
    entries = directory()
    entries["account:docs-1"] = account("docs-1", "docs@in.syruss.test", ["read"], ["documents"])
    entries["job:documents"] = job("documents", "Document inbound", "read", "docs-1", "docs@in.syruss.test", True, True)
    return await FakeMail.install(monkeypatch, entries)


@pytest.fixture
async def sftp_port(monkeypatch):
    monkeypatch.setattr(settings, "INTAKE_SFTP_PORT", 2222)
    port = await sftp_receiver.listen("127.0.0.1", 0, host_key=asyncssh.generate_private_key("ssh-ed25519"))
    yield port
    await sftp_receiver.stop()


async def new_channel(client, kind: str, **config):
    tpl = await create_template(client)
    await business_plan(tpl)
    res = await client.post(f"{API}/channels", headers=ws(tpl), json={"name": f"{kind} in", "kind": kind, "config": config})
    assert res.status_code == 201, res.text
    return tpl, res.json()


async def documents(tpl: str):
    async with AsyncSessionLocal() as db:
        return list((await db.execute(select(Document).where(Document.template_id == tpl).order_by(Document.created_at))).scalars().all())


async def events(channel_id: str):
    async with AsyncSessionLocal() as db:
        return list((await db.execute(select(IntakeEvent).where(IntakeEvent.channel_id == channel_id).order_by(IntakeEvent.id))).scalars().all())


def mail(to: str, *, sender="supplier@acme.test", subject="Invoice 1001", attach=(("invoice.png", None),), message_id="<m1@acme.test>", inline=False, headers=None) -> EmailMessage:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"], msg["Message-ID"] = sender, to, subject, message_id
    for name, value in (headers or {}).items():
        msg[name] = value
    msg.set_content("Please find the invoice attached.")
    for name, data in attach:
        data = data if data is not None else png_bytes()
        kind = "pdf" if name.endswith(".pdf") else "png"
        msg.add_attachment(data, maintype="application" if kind == "pdf" else "image", subtype=kind, filename=name, disposition="inline" if inline else "attachment",
                           **({"cid": "<logo@acme>"} if inline else {}))
    return msg


async def send_mail(_mail, msg: EmailMessage) -> str:
    """The message arrives in the documents mailbox: the mail service reads it and the server takes it in."""
    return await intake_email.take_in(msg.as_bytes())


# --- e-mail ------------------------------------------------------------------------------------------------

async def test_a_channel_gets_its_own_address_and_attachments_become_documents(client, mail_port):
    tpl, ch = await new_channel(client, "email")
    assert ch["address"].startswith("docs+") and ch["address"].endswith("@in.syruss.test") and ch["receiver_ready"] and ch["endpoint"] is None
    msg = mail(ch["address"], attach=[("one.png", None), ("two.png", None)])
    assert await send_mail(mail_port, msg) == "accepted"
    docs = await documents(tpl)
    assert [d.original_filename for d in docs] == ["one.png", "two.png"]
    assert all(d.channel_id == ch["id"] and d.source["via"] == "email" and d.source["sender"] == "supplier@acme.test" for d in docs)
    assert docs[0].sender_metadata == {"subject": "Invoice 1001", "from": "supplier@acme.test"} and docs[0].external_id == "m1@acme.test#1"
    log = await events(ch["id"])
    assert [(e.outcome, e.via) for e in log] == [("accepted", "email"), ("accepted", "email")]


async def test_the_address_may_be_on_any_of_the_headers_a_mail_server_leaves(client, mail_port):
    tpl, ch = await new_channel(client, "email")
    msg = mail("someone-else@acme.test", headers={"Delivered-To": ch["address"].upper()})   # a bcc or a list: only Delivered-To names us
    assert await send_mail(mail_port, msg) == "accepted"
    assert len(await documents(tpl)) == 1


async def test_the_same_message_sent_again_does_not_make_a_second_document(client, mail_port):
    tpl, ch = await new_channel(client, "email")
    msg = mail(ch["address"], attach=[("one.png", png_bytes())])
    await send_mail(mail_port, msg)
    await send_mail(mail_port, msg)           # the mail service offering it again after a lost acknowledgement
    assert len(await documents(tpl)) == 1


async def test_a_message_is_acknowledged_once_it_is_taken_in(client, mail_port):
    tpl, ch = await new_channel(client, "email")
    assert await mail_relay.take_in("item-1", "documents", mail(ch["address"]).as_bytes()) is True
    assert [(topic, key) for topic, key, _ in mail_port.published] == [(settings.MAIL_INBOUND_ACKS_TOPIC, "item-1")]
    assert len(await documents(tpl)) == 1
    with pytest.raises(Exception):                                      # a message that can't be taken in now is not acknowledged: it comes again
        await mail_relay.take_in("item-2", "documents", None)           # type: ignore[arg-type]
    assert len(mail_port.published) == 1


async def test_mail_is_only_taken_for_live_channel_addresses(client, mail_port):
    tpl, ch = await new_channel(client, "email")
    for wrong in ("docs@in.syruss.test", "docs+nobody@in.syruss.test", "docs+x@gmail.com", "other+" + ch["address"].split("+")[1]):
        assert await send_mail(mail_port, mail(wrong)) == "ignored"
    await client.patch(f"{API}/channels/{ch['id']}", headers=ws(tpl), json={"status": "paused"})
    assert await send_mail(mail_port, mail(ch["address"])) == "ignored"
    assert await documents(tpl) == []


async def test_only_the_allowed_senders_are_accepted(client, mail_port):
    tpl, ch = await new_channel(client, "email", sender_allowlist=["billing@acme.test", "@trusted.test"])
    assert await send_mail(mail_port, mail(ch["address"], sender="stranger@evil.test")) == "refused"
    assert await send_mail(mail_port, mail(ch["address"], sender="billing@acme.test", message_id="<a@x>")) == "accepted"
    assert await send_mail(mail_port, mail(ch["address"], sender="anyone@trusted.test", message_id="<b@x>")) == "accepted"
    assert len(await documents(tpl)) == 2
    refusals = [e for e in await events(ch["id"]) if e.outcome == "rejected"]
    assert refusals and "stranger@evil.test" in refusals[0].reason


async def test_a_channel_can_require_that_the_provider_vouched_for_the_sender(client, mail_port):
    tpl, ch = await new_channel(client, "email", require_authentication=True)
    assert await send_mail(mail_port, mail(ch["address"])) == "refused"
    assert "SPF or DKIM" in [e for e in await events(ch["id"]) if e.outcome == "rejected"][0].reason
    assert await send_mail(mail_port, mail(ch["address"], headers={"Authentication-Results": "mx.example; dkim=pass header.d=acme.test; spf=pass"})) == "accepted"
    assert len(await documents(tpl)) == 1


async def test_mail_with_nothing_readable_attached_is_refused_with_the_reason(client, mail_port):
    tpl, ch = await new_channel(client, "email")
    assert await send_mail(mail_port, mail(ch["address"], attach=[])) == "refused"
    assert await send_mail(mail_port, mail(ch["address"], attach=[("invoice.pdf", b"%PDF-1.7 " + b"x" * 400)], message_id="<pdf@x>")) == "refused"
    assert await send_mail(mail_port, mail(ch["address"], attach=[("invoice.png", b"MZ\x90\x00" + b"x" * 500)], message_id="<exe@x>")) == "refused"
    reasons = [e.reason for e in await events(ch["id"]) if e.outcome == "rejected"]
    assert len(reasons) == 3 and "no attached file" in reasons[0].lower() and "PDF" in reasons[1]
    assert await documents(tpl) == []


async def test_pictures_in_the_signature_are_not_documents(client, mail_port):
    tpl, ch = await new_channel(client, "email")
    msg = mail(ch["address"], attach=[("logo.png", None)], inline=True)
    assert await send_mail(mail_port, msg) == "refused"
    assert await documents(tpl) == []


async def test_a_channel_asks_for_a_mailbox_and_the_receiver_to_be_switched_on(client, monkeypatch):
    await FakeMail.install(monkeypatch)                      # the documents job has no mailbox that can read
    monkeypatch.setattr(settings, "INTAKE_SFTP_PORT", 0)
    tpl = await create_template(client)
    for kind in ("email", "sftp"):
        res = await client.post(f"{API}/channels", headers=ws(tpl), json={"name": kind, "kind": kind})
        assert res.status_code == 409 and "not switched on" in res.json()["detail"]
    info = (await client.get(f"{API}/channels/developer-info", headers=ws(tpl))).json()
    assert info["email"] == {"enabled": False, "mailbox": None} and info["sftp"]["enabled"] is False


# --- SFTP ---------------------------------------------------------------------------------------------------

async def connect(port: int, **credentials):
    return await asyncssh.connect("127.0.0.1", port, known_hosts=None, **credentials)


async def test_a_file_written_over_sftp_becomes_a_document_of_the_channel(client, sftp_port):
    tpl, ch = await new_channel(client, "sftp")
    password, info = ch["sftp_password"], ch["sftp"]
    assert info["username"].startswith("sftp-in") and info["port"] == 2222 and "sftp_secret_hash" not in str(ch)
    async with await connect(sftp_port, username=info["username"], password=password) as conn:
        async with conn.start_sftp_client() as sftp:
            async with sftp.open("scan-001.png", "wb") as f:
                await f.write(png_bytes())
    [doc] = await documents(tpl)
    assert doc.original_filename == "scan-001.png" and doc.channel_id == ch["id"] and doc.source["via"] == "sftp" and doc.source["sender"] == info["username"]
    # (the password is never kept: only its hash)
    again = (await client.get(f"{API}/channels", headers=ws(tpl))).json()[0]
    assert "sftp_password" not in again and "password" not in str(again["sftp"])


async def test_a_refused_file_makes_the_upload_fail_with_the_reason(client, sftp_port):
    tpl, ch = await new_channel(client, "sftp")
    async with await connect(sftp_port, username=ch["sftp"]["username"], password=ch["sftp_password"]) as conn:
        async with conn.start_sftp_client() as sftp:
            with pytest.raises(asyncssh.SFTPFailure) as refused:
                async with sftp.open("notes.png", "wb") as f:
                    await f.write(b"MZ\x90\x00" + b"x" * 500)
            assert "image" in str(refused.value).lower()
    assert await documents(tpl) == []
    log = [e for e in await events(ch["id"]) if e.outcome == "rejected"]
    assert log and log[0].via == "sftp" and log[0].filename == "notes.png"


async def test_the_sftp_server_only_receives_files(client, sftp_port):
    tpl, ch = await new_channel(client, "sftp")
    async with await connect(sftp_port, username=ch["sftp"]["username"], password=ch["sftp_password"]) as conn:
        async with conn.start_sftp_client() as sftp:
            with pytest.raises(asyncssh.SFTPPermissionDenied):
                await sftp.open("anything.png", "rb")
            with pytest.raises(asyncssh.SFTPPermissionDenied):
                await sftp.mkdir("folder")
            assert await sftp.listdir(".") == []
            async with sftp.open("../../etc/evil.png", "wb") as f:       # a path is flattened to its file name
                await f.write(png_bytes())
        with pytest.raises((asyncssh.ProcessError, asyncssh.ChannelOpenError)):
            await conn.run("ls /", check=True)                              # no shell
    assert [d.original_filename for d in await documents(tpl)] == ["evil.png"]


async def test_sftp_sign_in_is_checked_and_limited(client, sftp_port):
    tpl, ch = await new_channel(client, "sftp")
    user = ch["sftp"]["username"]
    with pytest.raises(asyncssh.PermissionDenied):
        await connect(sftp_port, username=user, password="wrong", preferred_auth="password")
    with pytest.raises(asyncssh.PermissionDenied):
        await connect(sftp_port, username="nobody", password=ch["sftp_password"], preferred_auth="password")
    await client.patch(f"{API}/channels/{ch['id']}", headers=ws(tpl), json={"status": "paused"})
    with pytest.raises(asyncssh.PermissionDenied):
        await connect(sftp_port, username=user, password=ch["sftp_password"], preferred_auth="password")
    await client.patch(f"{API}/channels/{ch['id']}", headers=ws(tpl), json={"status": "active"})
    ratelimit.reset()
    for _ in range(10):
        with pytest.raises(asyncssh.PermissionDenied):
            await connect(sftp_port, username=user, password="guess", preferred_auth="password")
    with pytest.raises(asyncssh.PermissionDenied):                          # locked out, even with the right password
        await connect(sftp_port, username=user, password=ch["sftp_password"], preferred_auth="password")


async def test_an_sftp_channel_can_sign_in_with_a_key_and_the_password_can_be_replaced(client, sftp_port):
    tpl, ch = await new_channel(client, "sftp")
    user = ch["sftp"]["username"]
    key = asyncssh.generate_private_key("ssh-ed25519")
    bad = await client.put(f"{API}/channels/{ch['id']}/sftp/public-key", headers=ws(tpl), json={"public_key": "not a key"})
    assert bad.status_code == 422
    ok = await client.put(f"{API}/channels/{ch['id']}/sftp/public-key", headers=ws(tpl), json={"public_key": key.export_public_key().decode()})
    assert ok.status_code == 200 and ok.json()["sftp"]["has_public_key"] is True
    async with await connect(sftp_port, username=user, client_keys=[key], preferred_auth="publickey") as conn:
        async with conn.start_sftp_client() as sftp:
            async with sftp.open("keyed.png", "wb") as f:
                await f.write(png_bytes())
    other = asyncssh.generate_private_key("ssh-ed25519")
    with pytest.raises(asyncssh.PermissionDenied):
        await connect(sftp_port, username=user, client_keys=[other], preferred_auth="publickey")
    fresh = (await client.post(f"{API}/channels/{ch['id']}/sftp/password", headers=ws(tpl))).json()["password"]
    with pytest.raises(asyncssh.PermissionDenied):
        await connect(sftp_port, username=user, password=ch["sftp_password"], preferred_auth="password")
    ratelimit.reset()
    async with await connect(sftp_port, username=user, password=fresh, preferred_auth="password"):
        pass
    assert len(await documents(tpl)) == 1


async def test_keys_belong_to_api_channels_only(client, mail_port):
    tpl, ch = await new_channel(client, "email")
    res = await client.post(f"{API}/channels/{ch['id']}/keys", headers=ws(tpl), json={"name": "x"})
    assert res.status_code == 409
    assert (await client.put(f"{API}/channels/{ch['id']}/sftp/public-key", headers=ws(tpl), json={"public_key": ""})).status_code == 409


# --- virus scanning -----------------------------------------------------------------------------------------------

async def fake_clamd(verdict_for):
    """A scanner that speaks clamd's INSTREAM protocol and calls a file infected when `verdict_for(bytes)` says so."""
    async def handle(reader, writer):
        assert await reader.readexactly(10) == b"zINSTREAM\0"
        data = b""
        while True:
            (size,) = struct.unpack(">I", await reader.readexactly(4))
            if size == 0:
                break
            data += await reader.readexactly(size)
        writer.write(b"stream: Eicar-Test-Signature FOUND\0" if verdict_for(data) else b"stream: OK\0")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


async def test_files_are_scanned_and_a_scanner_that_cannot_be_reached_lets_nothing_through(client, monkeypatch):
    tpl, ch = await new_channel(client, "api")
    key = (await client.post(f"{API}/channels/{ch['id']}/keys", headers=ws(tpl), json={"name": "scanner test"})).json()["secret"]
    send = lambda content: client.post(f"{API}/intake/documents", headers={"Authorization": f"Bearer {key}"}, data={"process": "false"},  # noqa: E731
                                        files={"file": ("a.png", content, "image/png")})
    server, port = await fake_clamd(lambda data: b"EICAR" in data)
    monkeypatch.setattr(settings, "CLAMAV_HOST", "127.0.0.1")
    monkeypatch.setattr(settings, "CLAMAV_PORT", port)
    try:
        assert (await send(png_bytes())).status_code == 202
        infected = await send(png_bytes() + b"EICAR")
        assert infected.status_code == 422 and "harmful" in infected.json()["detail"]["message"]
    finally:
        server.close()
        await server.wait_closed()
    down = await send(png_bytes())
    assert down.status_code == 503 and down.json()["detail"]["code"] == "unavailable"
    assert len(await documents(tpl)) == 1


async def test_an_email_channels_address_is_not_guessable(client, mail_port):
    tpl, ch = await new_channel(client, "email")
    token = ch["address"].split("+", 1)[1].split("@")[0]
    assert len(token.rsplit("-", 1)[1]) == 16                  # 64 random bits after the channel's name
