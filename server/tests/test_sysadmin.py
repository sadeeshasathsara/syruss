"""
The system admin API: only the admin app's tokens, only platform staff, each role only what it grants, a fresh sign-in
for staff changes, and every change in the platform audit log.
"""
import time
from sqlalchemy import select, update
from app.models.identity import User
from app.models.platform import PlatformAuditEntry
from app.models.tenant import Tenant
from tests.conftest import AsyncSessionLocal, person, register

API = "/api/v1/sysadmin"
ADMIN_APP = {"X-Client-ID": "syruss-admin"}


def staff_headers(user_id: str, name: str = "Staff Person", **extra) -> dict:
    return {**person(user_id, name), **ADMIN_APP, **extra}


async def test_only_platform_staff_signed_in_through_the_admin_app_get_in(client):
    assert (await client.get(f"{API}/me")).json()["detail"]["code"] == "admin_app_required"   # the everyday app's token
    me = (await client.get(f"{API}/me", headers=ADMIN_APP)).json()
    assert me["role"] == "owner" and me["from_config"] and "staff.manage" in me["permissions"]

    outsider = staff_headers("usr_sys_outsider", "Otto Outsider")
    await register(outsider, "owner")   # owning an organization doesn't make anyone platform staff
    res = await client.get(f"{API}/stats", headers=outsider)
    assert res.status_code == 403 and res.json()["detail"]["code"] == "platform_staff_required"


async def test_owners_manage_the_staff_and_every_change_is_recorded(client):
    viewer = staff_headers("usr_sys_viewer", "Vic Viewer")
    await register(viewer, None)
    assert (await client.post(f"{API}/staff", headers=ADMIN_APP, json={"email": "nobody@example.com", "role": "viewer"})).status_code == 404
    added = await client.post(f"{API}/staff", headers=ADMIN_APP, json={"email": "usr_sys_viewer@example.com", "role": "viewer", "reason": "On call"})
    assert added.status_code == 201 and added.json()["permissions"] == ["platform.view"]
    assert (await client.post(f"{API}/staff", headers=ADMIN_APP, json={"email": "usr_sys_viewer@example.com", "role": "viewer"})).status_code == 409

    # A viewer sees the platform but changes nothing.
    assert (await client.get(f"{API}/stats", headers=viewer)).status_code == 200
    denied = await client.post(f"{API}/staff", headers=viewer, json={"email": "dev@syruss.test", "role": "viewer"})
    assert denied.status_code == 403 and denied.json()["detail"]["code"] == "platform_permission_denied"

    # Staff changes need a fresh sign-in.
    stale = {**ADMIN_APP, "X-Auth-Time": str(int(time.time()) - 3600)}
    assert (await client.patch(f"{API}/staff/usr_sys_viewer", headers=stale, json={"role": "support"})).json()["detail"]["code"] == "reauth_required"
    changed = await client.patch(f"{API}/staff/usr_sys_viewer", headers=ADMIN_APP, json={"role": "support", "reason": "Helps customers now"})
    assert changed.json()["role"] == "support" and "support.access" in changed.json()["permissions"]

    # The deployment's owners and your own place can't be changed here.
    assert (await client.patch(f"{API}/staff/usr_admin_default", headers=ADMIN_APP, json={"role": "viewer"})).status_code == 409
    listed = (await client.get(f"{API}/staff", headers=ADMIN_APP)).json()["items"]
    assert {m["user_id"]: (m["role"], m["from_config"]) for m in listed}["usr_admin_default"] == ("owner", True)
    assert (await client.delete(f"{API}/staff/usr_sys_viewer", headers=ADMIN_APP, params={"reason": "Left the team"})).status_code == 204
    assert (await client.get(f"{API}/me", headers=viewer)).status_code == 403   # gone from the staff, gone from the tools

    log = (await client.get(f"{API}/audit", headers=ADMIN_APP, params={"action": "staff"})).json()["items"]
    mine = [(e["action"], e["reason"]) for e in log if e["target_id"] == "usr_sys_viewer"]
    assert mine == [("staff.removed", "Left the team"), ("staff.role_changed", "Helps customers now"), ("staff.added", "On call")]
    assert log[0]["actor_id"] == "usr_admin_default"


async def test_a_disabled_account_loses_the_platform_tools_whatever_its_role(client):
    operator = staff_headers("usr_sys_operator", "Ola Operator")
    await register(operator, None)
    await client.post(f"{API}/staff", headers=ADMIN_APP, json={"email": "usr_sys_operator@example.com", "role": "operator"})
    assert (await client.get(f"{API}/me", headers=operator)).json()["role"] == "operator"
    async with AsyncSessionLocal() as db:
        await db.execute(update(User).where(User.id == "usr_sys_operator").values(status="disabled"))
        await db.commit()
    assert (await client.get(f"{API}/me", headers=operator)).json()["detail"]["code"] == "account_disabled"


async def test_the_overview_counts_the_platform_and_checks_each_service(client):
    stats = (await client.get(f"{API}/stats", headers=ADMIN_APP)).json()
    assert stats["organizations"]["total"] >= 1 and stats["people"]["total"] >= 1
    assert set(stats) == {"organizations", "people", "workspaces", "documents", "agent", "deliveries"}
    health = (await client.get(f"{API}/health", headers=ADMIN_APP)).json()
    services = {s["key"]: s for s in health["services"]}
    assert list(services) == ["database", "storage", "sign_in", "events", "agent"]
    assert services["database"]["status"] == "ok" and services["storage"]["status"] == "ok"
    assert all(s["status"] in ("ok", "degraded", "down") and isinstance(s["latency_ms"], int) for s in services.values())


async def test_an_organization_says_why_it_is_shut():
    async with AsyncSessionLocal() as db:
        tenant = Tenant(name="Shut Ltd", slug="shut-ltd")
        db.add(tenant)
        tenant.set_status("suspended", "Unpaid invoices")
        await db.commit()
        saved = (await db.execute(select(Tenant).where(Tenant.slug == "shut-ltd"))).scalar_one()
        assert (saved.is_active, saved.status, saved.status_reason) == (False, "suspended", "Unpaid invoices")
        saved.set_status("active")
        assert saved.is_active and saved.status_reason is None
        assert (await db.execute(select(PlatformAuditEntry).where(PlatformAuditEntry.target_id == saved.id))).first() is None
