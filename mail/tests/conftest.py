"""
Mail service tests: a test database of its own, and the in-memory account instead of a mail server.
"""
import os

os.environ.setdefault("MAIL_DB", "syruss_mail_test")
os.environ["MAIL_BACKEND"] = "memory"
os.environ["RETRY_BASE_SECONDS"] = "30"

import pytest
from sqlalchemy import delete, update
from mail.accounts import MemoryAccount
from mail.accounts.base import Capability, Outbound
from mail.delivery import Delivery
from mail.router import Router
from mail import apps
from mail.store import AccountRow, CommandRow, CursorRow, InboundRow, JobRow, PurposeRow, SettingRow, Store


@pytest.fixture(scope="session")
async def store():
    s = Store()
    await s.init()
    yield s
    await s.close()


@pytest.fixture(autouse=True)
async def clean(store):
    async with store.session() as s:
        for table in (JobRow, AccountRow, CursorRow, InboundRow, CommandRow):
            await s.execute(delete(table))
        await s.execute(delete(PurposeRow).where(PurposeRow.system.is_(False)))   # jobs a test added
        await s.execute(update(PurposeRow).values(account_id=None))                  # and the accounts they were given
        await s.execute(delete(SettingRow).where(SettingRow.key.like("app:%")))   # provider apps a test set
        apps._cache.clear()
        await s.commit()


@pytest.fixture
def fallback():
    return MemoryAccount(id="env", address="no-reply@syruss.test", name="Syruss", granted=Capability.SEND)


@pytest.fixture
def delivery(store, fallback):
    return Delivery(store, Router(store, fallback))


def job(id="job-1", purpose="notifications", **extra) -> Outbound:
    return Outbound(id=id, purpose=purpose, to="pat@example.com", subject="Your code", text="Your code is 123456",
                    html="<p>Your code is 123456</p>", **extra)
