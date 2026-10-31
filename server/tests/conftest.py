"""
Tests run against a separate database and bucket so development data is never touched.
Kafka is not needed: events land in the outbox table, UI events go to the local broadcaster.
"""
import io
import os
import random

os.environ["POSTGRES_DB"] = "doc_intelligence_test"
os.environ["MINIO_BUCKET_NAME"] = "documents-test"
os.environ["PLATFORM_ADMIN_USER_IDS"] = "usr_admin_default"
os.environ["MAIL_BACKEND"] = "memory"
os.environ["REQUIRE_TRUSTED_DEVICE"] = "false"  # device tests switch it on for themselves
os.environ["PUBLIC_SITE_URL"] = "http://site.test"

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine
from app.core.agent_token import mint_agent_token
from app.core.auth import set_iam_provider
from app.core.config import settings
from app.core.database import AsyncSessionLocal, Base, engine
from app.core.permissions import ensure_system_roles
from app.main import app
from app.models.outbox import AgentEventOutbox
from app.models.tenant import Tenant
from app.services.storage_service import storage_service
from tests.mock_iam import DEV_EMAIL, DEV_USER_ID, DEV_USER_NAME, MockIAMProvider

TENANT = "tenant_default"


async def ensure_dev_organization(db) -> None:
    """The test owner and the test organization (slug "default"), which every request without headers acts in."""
    from app.models.identity import OrgMember, User
    if not await db.get(Tenant, TENANT):
        db.add(Tenant(id=TENANT, name="Test Org", slug="default", is_active=True))
        await db.flush()
    if not await db.get(User, DEV_USER_ID):
        first, _, last = DEV_USER_NAME.partition(" ")
        db.add(User(id=DEV_USER_ID, email=DEV_EMAIL, email_verified=True, first_name=first, last_name=last))
        await db.flush()
    if not (await db.execute(select(OrgMember.id).where(OrgMember.tenant_id == TENANT, OrgMember.user_id == DEV_USER_ID))).first():
        db.add(OrgMember(tenant_id=TENANT, user_id=DEV_USER_ID, role="owner"))
    await db.commit()

SCHEMA = {
    "document_type": "security_roster",
    "description": "Daily guard roster",
    "objects": [
        {"type": "Roster", "multiple": False, "fields": [
            {"name": "site_id", "type": "string", "required": True},
            {"name": "date", "type": "date", "required": True},
        ]},
        {"type": "Guard", "multiple": True, "fields": [
            {"name": "employee_id", "type": "string", "required": True},
            {"name": "name", "type": "string", "required": True},
            {"name": "in_time", "type": "time"},
            {"name": "out_time", "type": "time"},
            {"name": "hours", "type": "number"},
        ]},
    ],
    "validation_rules": [
        {"id": "shift_hours", "rule_type": "time_diff", "object": "Guard", "start_field": "in_time",
         "end_field": "out_time", "hours_field": "hours", "tolerance_hours": 0.25},
    ],
}

VALID_RECORD = {
    "Roster": [{"site_id": "S12", "date": "2026-07-22"}],
    "Guard": [{"employee_id": "75599", "name": "Perera", "in_time": "19:00", "out_time": "07:00", "hours": 12}],
}


async def _ensure_test_database() -> None:
    admin = create_async_engine(engine.url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        if not await conn.scalar(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": engine.url.database}):
            await conn.execute(text(f'CREATE DATABASE "{engine.url.database}"'))
    await admin.dispose()


@pytest.fixture(autouse=True, scope="session")
async def database():
    await _ensure_test_database()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    # Dispose and reopen the pool so asyncpg discards OID-cached prepared
    # statements for enum types that were just recreated (drop_all invalidates
    # the PostgreSQL OID for e.g. templatelifecyclestatus; stale connections
    # keep the old OID and raise "cache lookup failed for type" mid-suite).
    await engine.dispose()
    set_iam_provider(MockIAMProvider())
    async with AsyncSessionLocal() as s:
        await ensure_system_roles(s)
        await ensure_dev_organization(s)  # the test owner owns the test organization
    storage_service.ensure_bucket_exists()
    yield
    await engine.dispose()


@pytest.fixture(autouse=True)
def mailbox():
    """The messages the memory mail backend "sent" during this test."""
    from app.services import notifications
    notifications.outbox.clear()
    return notifications.outbox


@pytest.fixture
async def client():
    # Every request names its organization, as the web app does; a test overrides it with its own header.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test", headers={"X-Org-Slug": "default"}) as c:
        yield c


def png_bytes(color=None, size=(400, 300)) -> bytes:
    """A page image; each one different unless a colour is given (identical files count as duplicates)."""
    buf = io.BytesIO()
    Image.new("RGB", size, color or tuple(random.randrange(256) for _ in range(3))).save(buf, format="PNG")
    return buf.getvalue()


async def create_template(client: AsyncClient, name: str = "Roster", schema=None) -> str:
    res = await client.post("/api/v1/templates", json={"name": name, "schema_definition": schema or SCHEMA})
    assert res.status_code == 201, res.text
    return res.json()["id"]


def ws(template_id: str) -> dict:
    """Headers for a human request inside a template workspace."""
    return {"X-Template-ID": template_id}


def agent(template_id: str) -> dict:
    """Headers for an agent request scoped to a template workspace."""
    return {"Authorization": f"Bearer {mint_agent_token(TENANT, template_id, 'evt_test')}"}


async def upload(client: AsyncClient, template_id: str, name: str = "roster.png") -> str:
    res = await client.post("/api/v1/documents/upload", headers=ws(template_id), data={"process": "false"},
                            files={"file": (name, png_bytes(), "image/png")})
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def outbox_events(template_id: str, event_type: str):
    async with AsyncSessionLocal() as s:
        rows = (await s.execute(select(AgentEventOutbox).where(
            AgentEventOutbox.template_id == template_id, AgentEventOutbox.event_type == event_type,
        ).order_by(AgentEventOutbox.created_at))).scalars().all()
        return list(rows)


def person(user_id: str, name: str = "Person") -> dict:
    """Headers for another signed-in person (the mock identity reads them)."""
    return {"X-User-ID": user_id, "X-Username": name, "X-Email": f"{user_id}@example.com"}


async def register(headers: dict, org_role: str | None = "member") -> None:
    """Gives a person a profile and, unless org_role is None, a seat in the test organization (not in any workspace yet)."""
    from app.models.identity import OrgMember, User
    user_id = headers["X-User-ID"]
    async with AsyncSessionLocal() as s:
        if not await s.get(User, user_id):
            first, _, last = headers.get("X-Username", "Person").partition(" ")
            s.add(User(id=user_id, email=headers["X-Email"], email_verified=True, first_name=first, last_name=last or "Tester"))
            await s.flush()
        if org_role and not (await s.execute(select(OrgMember.id).where(OrgMember.tenant_id == TENANT, OrgMember.user_id == user_id))).first():
            s.add(OrgMember(tenant_id=TENANT, user_id=user_id, role=org_role))
        await s.commit()


async def join(client: AsyncClient, template_id: str, headers: dict, role: str, org_role: str = "member") -> None:
    """Registers the person, invites them to the workspace with `role`, and has them accept."""
    await register(headers, org_role)
    res = await client.post("/api/v1/settings/invitations", headers=ws(template_id), json={"email": headers["X-Email"], "role": role})
    assert res.status_code == 201, res.text
    accepted = await client.post(f"/api/v1/invitations/{res.json()['token']}/accept", headers=headers)
    assert accepted.status_code == 200, accepted.text


async def business_plan(template_id: str) -> None:
    """Moves a workspace to the largest plan, so a test can use several channels, keys or destinations."""
    from app.models.workspace_admin import WorkspaceSettings
    async with AsyncSessionLocal() as db:
        row = await db.get(WorkspaceSettings, template_id)
        if row is None:
            db.add(WorkspaceSettings(template_id=template_id, data={"plan": "business"}))
        else:
            row.data = {**row.data, "plan": "business"}
        await db.commit()
