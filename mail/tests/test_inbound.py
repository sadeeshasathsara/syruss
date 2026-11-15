"""
Inbound mail goes to the server whole, stays here sealed until the server says it took it in, is published again if that
never comes, and a message too big to carry is reported rather than lost.
"""
import json

from mail.accounts import MemoryAccount
from mail.accounts.base import Capability
from mail.config import settings
from mail.inbound import Reader
from mail.router import Router
from mail.store import InboundRow


def reader_with(store, announced, account=None):
    async def announce(event, raw):
        announced.append((event, raw))

    box = account or MemoryAccount(id="env", granted=Capability.READ | Capability.SEND)
    return box, Reader(store, Router(store, box), announce)


async def test_a_message_stays_until_the_server_acknowledges_it(store):
    announced = []
    box, reader = reader_with(store, announced)
    box.inbox.append(b"old")
    await reader.read("documents")                                  # notes where the mailbox is
    box.inbox.append(b"Subject: invoice\r\n\r\nattached")
    assert await reader.read("documents") == 1
    event, raw = announced[0]
    assert event["purpose"] == "documents" and raw == b"Subject: invoice\r\n\r\nattached"

    async with store.session() as s:
        assert await s.get(InboundRow, event["id"]) is not None     # kept: the server hasn't said yet
    await reader.acknowledged(event["id"])
    async with store.session() as s:
        assert await s.get(InboundRow, event["id"]) is None         # taken in: gone
    await reader.acknowledged(event["id"])                          # a second acknowledgement does no harm


async def test_what_the_server_has_not_acknowledged_is_published_again_but_not_every_pass(store):
    announced = []
    box, reader = reader_with(store, announced)
    await reader.read("support")
    box.inbox.append(b"Subject: help\r\n\r\nplease")
    await reader.read("support")
    sent = {}
    assert await reader.republish_stale(sent, every=0) == 1        # nothing was acknowledged: again
    assert announced[-1][1] == b"Subject: help\r\n\r\nplease"
    assert await reader.republish_stale(sent, every=3600) == 0      # too soon since the last time
    await reader.acknowledged(announced[0][0]["id"])
    assert await reader.republish_stale(sent, every=0) == 0         # acknowledged: never again


async def test_a_message_too_big_to_carry_is_reported_and_not_kept(store, monkeypatch):
    monkeypatch.setattr(settings, "MAIL_MAX_MESSAGE_BYTES", 100)
    announced = []
    box, reader = reader_with(store, announced)
    await reader.read("documents")
    box.inbox.append(b"Subject: huge\r\n\r\n" + b"x" * 500)
    assert await reader.read("documents") == 1
    event, raw = announced[0]
    assert event["too_large"] is True and event["size"] > 500 and raw == b""      # the server is told it came, with no body
    async with store.session() as s:
        assert await s.get(InboundRow, event["id"]) is None


async def test_the_bus_publishes_the_email_itself_with_its_facts_in_the_headers(monkeypatch):
    from mail.bus import Bus

    class Producer:
        sent = []

        async def send_and_wait(self, topic, key, value, headers=None):
            self.sent.append((topic, key, value, dict(headers or [])))

    producer = Producer()
    await Bus(producer).inbound({"id": "i-1", "purpose": "support", "account_id": "env", "size": 12}, b"Subject: hi")
    await Bus(producer).inbound({"id": "i-2", "purpose": "documents", "account_id": "env", "size": 99, "too_large": True}, b"")
    (topic, key, value, headers), (_, _, big, big_headers) = producer.sent
    assert topic == settings.MAIL_INBOUND_TOPIC and key == b"i-1" and value == b"Subject: hi"       # the bytes, not a JSON copy of them
    assert headers == {"id": b"i-1", "job": b"support", "account": b"env", "size": b"12"} and json.dumps(list(headers)) is not None
    assert big == b"" and big_headers["too_large"] == b"1"
