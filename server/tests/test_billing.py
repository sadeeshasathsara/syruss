"""
Plans belong to the organization: limits come from its plan version, add-ons and overrides; every document accepted for
reading is charged its pages from the allowance its workspaces share; periods roll over by themselves.
"""
import asyncio
import io
import json
from datetime import datetime, timedelta, timezone
from alembic import command
from PIL import Image
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import create_async_engine
from app.core.database import AsyncSessionLocal, engine
from app.core.migrations import alembic_config
from app.models.billing import OrgSubscription, UsageEvent
from app.models.document import Document, DocumentRole
from app.models.identity import OrgMember
from app.models.tenant import Tenant
from app.services.billing import catalog, service as billing
from app.services.billing.entitlements import compose
from app.services.document_service import document_service
from app.services.intake.rules import IntakeRejected
from tests.conftest import SCHEMA, pages_allowance, png_bytes
from tests.mock_iam import DEV_USER_ID

API = "/api/v1"


async def organization(slug: str) -> dict:
    """A new organization the test owner owns, on the Free plan: the headers to act in it."""
    async with AsyncSessionLocal() as db:
        db.add(Tenant(id=f"tenant_{slug}", name=slug.title(), slug=slug, is_active=True))
        await db.flush()
        db.add(OrgMember(tenant_id=f"tenant_{slug}", user_id=DEV_USER_ID, role="owner"))
        await db.commit()
    return {"X-Org-Slug": slug}


async def workspace(client, org: dict, name: str = "Forms") -> str:
    res = await client.post(f"{API}/templates", headers=org, json={"name": name, "schema_definition": SCHEMA})
    assert res.status_code == 201, res.text
    return res.json()["id"]


def tiff(pages: int) -> bytes:
    frames = [Image.new("RGB", (300, 200), (40 * i, 90, 160)) for i in range(pages)]
    buf = io.BytesIO()
    frames[0].save(buf, format="TIFF", save_all=True, append_images=frames[1:])
    return buf.getvalue()


async def send(client, org: dict, tpl: str, content: bytes = None, name: str = "page.png"):
    kind = "image/tiff" if name.endswith(".tif") else "image/png"
    return await client.post(f"{API}/documents/upload", headers={**org, "X-Template-ID": tpl}, data={"process": "false"},
                             files={"file": (name, content or png_bytes(), kind)})


async def used(tenant_id: str) -> int:
    async with AsyncSessionLocal() as db:
        return (await billing.summary(db, tenant_id))["pages_used"]


def test_limits_come_from_the_plan_then_its_add_ons_then_overrides():
    plan = {"pages_per_month": 100, "channels_per_workspace": 1, "workspaces": None}
    limits = compose(plan, [({"pages_per_month": 50}, 2), ({"workspaces": 5, "not_a_limit": 9}, 1)], {"channels_per_workspace": 3})
    assert limits["pages_per_month"] == 200          # two packs of 50 stack on the plan's 100
    assert limits["workspaces"] is None              # adding to unlimited stays unlimited
    assert limits["channels_per_workspace"] == 3     # an override wins
    assert limits["members_per_workspace"] is None   # a limit the plan doesn't name is unlimited
    assert "not_a_limit" not in limits               # only registered limits exist


def test_a_month_later_keeps_the_day_the_subscription_started():
    jan31 = datetime(2026, 1, 31, 9, tzinfo=timezone.utc)
    feb = billing.month_after(jan31, 31)
    assert feb.date().isoformat() == "2026-02-28" and billing.month_after(feb, 31).date().isoformat() == "2026-03-31"
    assert billing.month_after(datetime(2026, 12, 15, tzinfo=timezone.utc), 15).date().isoformat() == "2027-01-15"


async def test_pages_come_out_of_one_allowance_the_organizations_workspaces_share(client):
    org = await organization("pages-shared")
    await pages_allowance(4, "tenant_pages-shared")
    first, second = await workspace(client, org, "First"), await workspace(client, org, "Second")

    assert (await send(client, org, first)).status_code == 201
    three = await send(client, org, second, tiff(3), "scan.tif")
    assert three.status_code == 201, three.text
    assert await used("tenant_pages-shared") == 4
    async with AsyncSessionLocal() as db:
        assert (await db.get(Document, three.json()["id"])).page_count == 3
        period = (await billing.subscription(db, "tenant_pages-shared")).period_start
        assert await billing.pages_by_workspace(db, "tenant_pages-shared", period) == {first: 1, second: 3}

    refused = await send(client, org, first)
    assert refused.status_code == 402 and "pages for this period are used up" in refused.json()["detail"]
    assert await used("tenant_pages-shared") == 4


async def test_a_document_that_does_not_fit_is_refused_and_test_documents_and_repeats_cost_nothing(client):
    org = await organization("pages-fit")
    tenant = "tenant_pages-fit"
    await pages_allowance(2, tenant)
    tpl = await workspace(client, org)
    async with AsyncSessionLocal() as db:
        before = (await db.execute(select(func.count(Document.id)).where(Document.template_id == tpl))).scalar()

    refused = await send(client, org, tpl, tiff(3), "big.tif")
    assert refused.status_code == 402
    assert refused.json()["detail"].startswith("This document has 3 pages, but only 2 of the Free plan's 2 pages")
    async with AsyncSessionLocal() as db:
        assert (await db.execute(select(func.count(Document.id)).where(Document.template_id == tpl))).scalar() == before

        test_doc = await document_service.store_bytes(db, tenant_id=tenant, template_id=tpl, content=tiff(2), filename="t.tif", is_test=True)
        sample = await document_service.store_bytes(db, tenant_id=tenant, template_id=tpl, content=png_bytes(), filename="s.png",
                                                    role=DocumentRole.SAMPLE)
        await db.commit()
        charged = (await db.execute(select(func.count(UsageEvent.id)).where(UsageEvent.source_id.in_([test_doc.id, sample.id])))).scalar()
        assert charged == 0

    ok = await send(client, org, tpl)
    async with AsyncSessionLocal() as db:
        await billing.charge_pages(db, await db.get(Document, ok.json()["id"]))  # charging it again changes nothing
        await db.commit()
    assert await used(tenant) == 1


async def test_two_documents_at_once_cannot_both_take_the_last_page(client):
    org = await organization("pages-race")
    await pages_allowance(1, "tenant_pages-race")
    tpl = await workspace(client, org)
    answers = await asyncio.gather(send(client, org, tpl), send(client, org, tpl))
    assert sorted(r.status_code for r in answers) == [201, 402]
    assert await used("tenant_pages-race") == 1


async def test_workspace_limits_apply_to_each_workspace_and_an_override_can_raise_one(client):
    org = await organization("limits-each")
    tenant = "tenant_limits-each"
    first, second = await workspace(client, org, "First"), await workspace(client, org, "Second")
    for tpl in (first, second):
        assert (await client.post(f"{API}/channels", headers={**org, "X-Template-ID": tpl}, json={"name": "One"})).status_code == 201
    again = await client.post(f"{API}/channels", headers={**org, "X-Template-ID": first}, json={"name": "Two"})
    assert again.status_code == 402 and again.json()["detail"] == "The Free plan allows 1 intake channels; this would make 2."

    async with AsyncSessionLocal() as db:
        await billing.set_override(db, tenant_id=tenant, template_id=first, key="channels_per_workspace", value=2, reason="test", set_by="tests")
        await billing.set_override(db, tenant_id=tenant, key="workspaces", value=2, reason="test", set_by="tests")
        await db.commit()
    assert (await client.post(f"{API}/channels", headers={**org, "X-Template-ID": first}, json={"name": "Two"})).status_code == 201
    assert (await client.post(f"{API}/channels", headers={**org, "X-Template-ID": second}, json={"name": "Two"})).status_code == 402
    third = await client.post(f"{API}/templates", headers=org, json={"name": "Third", "schema_definition": SCHEMA})
    assert third.status_code == 402 and third.json()["detail"] == "This organization's limit is 2 workspaces; this would make 3."


async def test_periods_roll_over_applying_a_downgrade_and_trials_end(client):
    await organization("periods")
    tenant = "tenant_periods"
    async with AsyncSessionLocal() as db:
        team, free, business = [await catalog.latest(db, "plan", k) for k in ("team", "free", "business")]
        await billing.change_plan(db, tenant, team)
        sub = await billing.change_plan(db, tenant, free, at_period_end=True)
        await db.commit()
        start, end, day = sub.period_start, sub.period_end, sub.anchor_day
        assert (await billing.standing(db, tenant)).plan.key == "team"   # the downgrade waits for the period's end

        await billing.roll_periods(db, now=end + timedelta(seconds=1))
        sub = await db.get(OrgSubscription, tenant)
        await db.refresh(sub)
        assert sub.plan_id == free.id and sub.pending_plan_id is None
        assert sub.period_start == end and sub.period_end == billing.month_after(end, day) and start < end

        # A trial that ran out goes back to Free unless it was paid for.
        sub.plan_id, sub.status, sub.trial_ends_at = business.id, "trialing", datetime.now(timezone.utc) - timedelta(minutes=1)
        await db.commit()
        await billing.roll_periods(db)
        await db.refresh(sub)
        assert (sub.plan_id, sub.status) == (free.id, "active")

        # An override that has expired no longer counts.
        await billing.set_override(db, tenant_id=tenant, key=billing.PAGES, value=5, reason="test", set_by="tests",
                                   expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        await db.commit()
        assert (await billing.standing(db, tenant)).limits[billing.PAGES] == free.limits[billing.PAGES]


async def test_a_new_organization_starts_on_free_and_reads_the_catalog(client):
    await organization("brand-new")
    async with AsyncSessionLocal() as db:
        s = await billing.standing(db, "tenant_brand-new")
        assert s.plan.key == "free" and s.subscription.anchor_day == s.subscription.period_start.day
        assert [p.key for p in await catalog.offered(db)] == ["free", "team", "business"]
    res = await client.get(f"{API}/settings/billing", headers={"X-Org-Slug": "brand-new",
                                                               "X-Template-ID": await workspace(client, {"X-Org-Slug": "brand-new"})})
    body = res.json()
    assert body["plan"] == "free" and body["plans"]["team"]["documents_per_month"] == 2000 and body["pages"]["pages_limit"] == 200


async def test_refusing_pages_is_an_intake_refusal_with_its_own_code():
    async with AsyncSessionLocal() as db:
        db.add(Tenant(id="tenant_refusal", name="Refusal", slug="refusal", is_active=True))
        await db.commit()
        await billing.set_override(db, tenant_id="tenant_refusal", key=billing.PAGES, value=0, reason="test", set_by="tests")
        try:
            await billing.reserve_pages(db, tenant_id="tenant_refusal", pages=1)
            raise AssertionError("should be refused")
        except IntakeRejected as rejected:
            assert (rejected.status, rejected.code) == (402, "plan_limit_reached")


async def test_the_migration_moves_each_organization_onto_its_best_plan_and_keeps_its_capacity():
    name = f"{engine.url.database}_billing"
    admin = create_async_engine(engine.url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = engine.url.set(database=name).render_as_string(hide_password=False)
    try:
        await asyncio.to_thread(command.upgrade, alembic_config(url), "0024")
        old = create_async_engine(url)
        async with old.begin() as conn:
            for tenant in ("t_two_teams", "t_none"):
                await conn.execute(text("INSERT INTO tenants (id, name, slug, is_active, status, created_at, updated_at) "
                                        "VALUES (:t, :t, :t, true, 'active', now(), now())"), {"t": tenant})
            for tpl, plan in (("w1", "team"), ("w2", "team")):
                await conn.execute(text("INSERT INTO document_templates (id, tenant_id, name, document_type, schema_definition, created_at, updated_at) "
                                        "VALUES (:w, 't_two_teams', :w, 'form', '{}', now(), now())"), {"w": tpl})
                await conn.execute(text("INSERT INTO workspace_settings (template_id, data, updated_at) VALUES (:w, :d, now())"),
                                   {"w": tpl, "d": json.dumps({"plan": plan, "general": {"timezone": "UTC"}})})
        await old.dispose()
        await asyncio.to_thread(command.upgrade, alembic_config(url), "head")
        new = create_async_engine(url)
        async with new.connect() as conn:  # (the engine is disposed below, even when a check fails)
            plans = dict((await conn.execute(text("SELECT tenant_id, plan_id FROM org_subscriptions"))).all())
            assert plans == {"t_two_teams": "plan-team-1", "t_none": "plan-free-1"}
            carried = (await conn.execute(text("SELECT tenant_id, key, value, reason FROM org_limit_overrides"))).all()
            assert [tuple(r) for r in carried] == [("t_two_teams", "pages_per_month", 4000, "Carried over from per-workspace plans")]
            data = (await conn.execute(text("SELECT data FROM workspace_settings WHERE template_id = 'w1'"))).scalar()
            data = data if isinstance(data, dict) else json.loads(data)
            assert "plan" not in data and data["general"] == {"timezone": "UTC"}
    finally:
        for made in ("old", "new"):
            if made in locals():
                await locals()[made].dispose()
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await admin.dispose()
