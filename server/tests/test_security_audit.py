"""
What the security audit fixed, held in place: live events reach only their own channel, nobody acts above their own role,
sessions can't borrow someone else's chat, downloads and exports are safe to open, outbound calls stay off private
networks even when DNS changes its answer, and oversized or endless input is refused.
"""
import socket
import pytest
from app.core import ratelimit
from app.core.config import settings
from app.core.downloads import content_disposition, csv_cell
from app.services import source_data
from app.services.destinations import http as outbound
from app.services.destinations.email import subject_for
from app.services.live import broadcaster as live
from app.services.live.broadcaster import EventBroadcaster, broadcaster
from tests.conftest import TENANT, create_template, join, person, png_bytes, register, upload, ws

API = "/api/v1"


def as_(tpl: str, who: dict) -> dict:
    return {**ws(tpl), **who}


# --- live events ---------------------------------------------------------------------------------

async def test_there_is_no_organization_wide_live_channel(client):
    tpl = await create_template(client)
    member = person("usr_sec_member", "Mia Member")
    await join(client, tpl, member, "viewer")
    assert (await client.post(f"{API}/ws/ticket", headers=as_(tpl, member), json={"channel": f"tenant:{TENANT}"})).status_code == 403
    assert (await client.get(f"{API}/live/tenant:{TENANT}", headers=as_(tpl, member))).status_code == 403


async def test_an_event_reaches_only_its_own_channel():
    """A person's notifications (and a private chat's replies) used to be copied to tenant:{id} too."""
    wide = broadcaster.subscribe(f"tenant:{TENANT}", replay=False)
    other = broadcaster.subscribe("user:usr_someone_else", replay=False)
    mine = broadcaster.subscribe("user:usr_sec_owner", replay=False)
    try:
        await broadcaster.broadcast_to_channel("user:usr_sec_owner", {"event_type": "notification", "tenant_id": TENANT, "title": "Invoice paid"})
        assert mine.get_nowait()["title"] == "Invoice paid"
        assert wide.empty() and other.empty()
    finally:
        broadcaster.unsubscribe(f"tenant:{TENANT}", wide)
        broadcaster.unsubscribe("user:usr_someone_else", other)
        broadcaster.unsubscribe("user:usr_sec_owner", mine)


async def test_replay_history_is_kept_for_recent_channels_only(monkeypatch):
    monkeypatch.setattr(live, "HISTORY_CHANNELS", 3)
    monkeypatch.setattr(live, "HISTORY_PER_CHANNEL", 2)
    b = EventBroadcaster()
    for n in range(6):
        for i in range(5):
            await b.broadcast_to_channel(f"doc{n}", {"i": i})
    assert list(b._history) == ["doc3", "doc4", "doc5"]
    assert [e["i"] for e in b._history["doc5"]] == [3, 4]


# --- roles and sessions --------------------------------------------------------------------------

async def test_creating_a_template_is_creating_a_workspace(client):
    member = person("usr_sec_plain", "Pat Plain")
    await register(member)  # an organization member, without workspaces.create
    res = await client.post(f"{API}/templates", headers=member, json={"name": "Mine", "schema_definition": {"objects": []}})
    assert res.status_code == 403
    res = await client.post(f"{API}/templates/discover", headers=member, data={"user_notes": "x", "session_id": "tpl_secure1"},
                            files=[("files", ("a.png", png_bytes(), "image/png"))])
    assert res.status_code == 403


async def test_a_session_id_cannot_borrow_someone_elses_chat_or_a_document(client):
    tpl = await create_template(client)
    chat = (await client.post(f"{API}/conversations", headers=ws(tpl), json={"title": "Owner's own"})).json()
    doc = await upload(client, tpl)
    editor = person("usr_sec_editor", "Eli Editor")
    await join(client, tpl, editor, "editor")
    for session in (chat["id"], doc):
        res = await client.post(f"{API}/knowledge/ask", headers=as_(tpl, editor), json={"question": "hi", "session_id": session})
        assert res.status_code == 409, res.text
    assert (await client.post(f"{API}/knowledge/ask", headers=as_(tpl, editor), json={"question": "hi", "session_id": "user:usr_x"})).status_code == 422
    assert (await client.post(f"{API}/knowledge/ask", headers=as_(tpl, editor), json={"question": "hi", "session_id": "help_sec01"})).status_code == 200


async def test_nobody_moves_or_removes_someone_who_outranks_them(client):
    tpl = await create_template(client)
    role = (await client.post(f"{API}/settings/roles", headers=ws(tpl), json={"name": "People desk", "permissions": ["members.manage"]})).json()
    desk, editor = person("usr_sec_desk", "Dee Desk"), person("usr_sec_ed2", "Ed Two")
    await join(client, tpl, desk, role["id"])
    await join(client, tpl, editor, "editor")
    members = (await client.get(f"{API}/settings/members", headers=ws(tpl))).json()
    target = next(m for m in members if m["user_id"] == "usr_sec_ed2")
    assert (await client.patch(f"{API}/settings/members/{target['id']}", headers=as_(tpl, desk), json={"role": role["id"]})).status_code == 403
    assert (await client.delete(f"{API}/settings/members/{target['id']}", headers=as_(tpl, desk))).status_code == 403
    # The owner outranks everyone; and anyone may still leave.
    me = next(m for m in members if m["user_id"] == "usr_sec_desk")
    assert (await client.delete(f"{API}/settings/members/{me['id']}", headers=as_(tpl, desk))).status_code == 204
    assert (await client.patch(f"{API}/settings/members/{target['id']}", headers=ws(tpl), json={"role": "viewer"})).status_code == 200


# --- downloads and exports -----------------------------------------------------------------------

async def test_a_file_named_in_another_script_downloads(client):
    tpl = await create_template(client)
    res = await client.post(f"{API}/documents/upload", headers=ws(tpl), data={"process": "false"},
                            files={"file": ('රෝස්ටරය "1".png', png_bytes(), "image/png")})
    doc = res.json()["id"]
    got = await client.get(f"{API}/documents/{doc}/file", headers=ws(tpl))
    assert got.status_code == 200
    assert got.headers["content-disposition"].startswith('inline; filename="') and "filename*=UTF-8''" in got.headers["content-disposition"]


def test_content_disposition_keeps_the_name_and_drops_what_breaks_the_header():
    header = content_disposition("attachment", 'a"b\r\nc\\d රූ.pdf')
    assert header == "attachment; filename=\"abcd __.pdf\"; filename*=UTF-8''abcd%20%E0%B6%BB%E0%B7%96.pdf"


def test_csv_cells_that_would_run_as_formulas_are_written_as_text():
    assert csv_cell('=HYPERLINK("https://evil.example","x")').startswith("'=")
    assert csv_cell("+94 77 123 4567") == "'+94 77 123 4567"
    assert csv_cell("@SUM(A1)") == "'@SUM(A1)"
    assert csv_cell("-12.50") == "-12.50" and csv_cell("+3") == "+3" and csv_cell(-4) == -4
    assert csv_cell("Perera") == "Perera" and csv_cell(None) is None


def test_an_email_subject_fills_plain_placeholders_only():
    doc = {"filename": "roster.png", "external_id": "R-1"}
    assert subject_for("Approved {filename} ({external_id}) v{revision}", doc, 2) == "Approved roster.png (R-1) v2"
    assert subject_for("{filename.__class__.__mro__}", doc, 1) == "{filename.__class__.__mro__}"


# --- outbound calls ------------------------------------------------------------------------------

async def test_a_name_that_turns_private_after_the_check_is_still_refused(monkeypatch):
    """DNS rebinding: public when checked, loopback when connecting."""
    real = socket.getaddrinfo
    answers = iter(["93.184.216.34"])

    def lookup(host, port, *args, **kwargs):
        if host != "rebind.test":
            return real(host, port, *args, **kwargs)
        address = next(answers, "127.0.0.1")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]

    monkeypatch.setattr(source_data.socket, "getaddrinfo", lookup)
    monkeypatch.setattr(source_data, "HTTP_TRANSPORT", None)
    monkeypatch.setattr(settings, "KNOWLEDGE_SOURCES_ALLOW_PRIVATE_HOSTS", False)
    reply = await outbound.call("GET", "http://rebind.test/hook")
    assert reply.status is None and reply.error and "Could not reach" in reply.error


@pytest.mark.parametrize("address", ["127.0.0.1", "10.1.2.3", "169.254.169.254", "100.64.0.1", "::1", "::ffff:10.0.0.1", "fe80::1%2", "0.0.0.0"])
def test_private_and_special_addresses_are_not_public(address):
    assert source_data.not_public(address)


def test_public_addresses_are():
    assert not source_data.not_public("93.184.216.34") and not source_data.not_public("2606:4700:4700::1111")


# --- size and rate limits ------------------------------------------------------------------------

async def test_a_body_without_a_length_is_still_held_to_the_size_limit(client, monkeypatch):
    monkeypatch.setattr(settings, "MAX_REQUEST_MB", 1)

    async def endless():  # a well-formed upload that just keeps going, with no Content-Length
        yield b'--b\r\nContent-Disposition: form-data; name="file"; filename="a.png"\r\nContent-Type: image/png\r\n\r\n'
        for _ in range(3):
            yield b"x" * 600_000

    res = await client.post(f"{API}/documents/upload", content=endless(), headers={"Content-Type": "multipart/form-data; boundary=b"})
    assert res.status_code == 413


def test_the_rate_limiter_forgets_keys_whose_window_has_passed():
    ratelimit.reset()
    ratelimit.limit("sec-test:a", 5, 60)
    ratelimit.limit("sec-test:b", 5, 3600)
    now = ratelimit._hits["sec-test:a"][-1]
    ratelimit._sweep(now + 120)
    assert "sec-test:a" not in ratelimit._hits and "sec-test:b" in ratelimit._hits
    ratelimit.reset()
