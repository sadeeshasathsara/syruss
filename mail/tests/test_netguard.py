"""On a real server a mailbox's mail servers must be on the public internet: the Email page can't be used to probe the inside."""
import pytest

from mail.config import settings
from mail.operations import OperationError, Operations
from mail.router import Router


@pytest.fixture
def ops(store, fallback):
    return Operations(store, Router(store, fallback))


async def add(ops, host):
    return await ops.add_imap(by="usr_owner", address="box@example.com", name="Box", config={"smtp_host": host}, secret={"smtp_password": "x"}, jobs=[])


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "10.0.0.5", "192.168.1.20", "172.16.4.4", "169.254.169.254", "100.64.0.1", "[::1]", "::ffff:127.0.0.1", "0.0.0.0"])
async def test_a_real_server_refuses_servers_that_are_not_public(ops, monkeypatch, host):
    monkeypatch.setattr(settings, "ENV", "production")
    with pytest.raises(OperationError, match="public mail server"):
        await add(ops, host)


async def test_a_real_server_takes_a_public_one_and_development_takes_localhost(ops, monkeypatch):
    monkeypatch.setattr(settings, "ENV", "production")
    assert (await add(ops, "8.8.8.8")).address == "box@example.com"
    monkeypatch.setattr(settings, "ENV", "development")
    assert (await add(ops, "127.0.0.1")).address == "box@example.com"            # Mailpit on this machine
