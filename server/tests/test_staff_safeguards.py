"""
The safeguards around platform staff: a strong and recent sign-in, a second owner's approval for risky actions (when the
owners turn it on), customers deciding whether support may open their content, and access that is reviewed and taken
away when unused.
"""
import time
from datetime import datetime, timedelta, timezone
import pytest
from sqlalchemy import select, update
from app.models.platform import PlatformApproval, PlatformAuditEntry, PlatformSetting, PlatformStaff
from app.models.tenant import Tenant
from app.services import platform_controls, staff_offboarding
from tests.conftest import AsyncSessionLocal, invite_staff, make_staff, person
from tests.test_billing import organization

API = "/api/v1"
SYS = f"{API}/sysadmin"
OWNER = {"X-Client-ID": "syruss-admin", "X-User-ID": "usr_platform_owner", "X-Username": "Pat Platform", "X-Email": "owner@syruss.test"}


def staff_headers(user_id: str, name: str) -> dict:
    return {**person(user_id, name), "X-Client-ID": "syruss-admin"}


async def _forget(*keys: str) -> None:
    async with AsyncSessionLocal() as db:
        for key in keys:
            row = await db.get(PlatformSetting, key)
            if row is not None:
                await db.delete(row)
        await db.commit()
    platform_controls.forget()


@pytest.fixture
async def clean_rules():
    yield
    await _forget("approvals", staff_offboarding.POLICY, staff_offboarding.REMINDED)


async def test_platform_tools_need_a_one_time_code_and_a_sign_in_from_today(client):
    password_only = {**OWNER, "X-Amr": "pwd"}
    refused = await client.get(f"{SYS}/me", headers=password_only)
    assert refused.status_code == 403 and refused.json()["detail"]["code"] == "two_step_required"
    yesterday = {**OWNER, "X-Auth-Time": str(int(time.time()) - 9 * 3600)}
    expired = await client.get(f"{SYS}/me", headers=yesterday)
    assert expired.status_code == 401 and expired.json()["detail"]["code"] == "session_expired"
    me = (await client.get(f"{SYS}/me", headers=OWNER)).json()
    assert me["session"]["idle_minutes"] == 30 and me["session"]["ends_at"]
    # Accepting a staff invitation asks the same of the sign-in.
    token = await invite_staff("code.less@example.com", "viewer")
    newcomer = {**staff_headers("usr_code_less", "Cody Less"), "X-Email": "code.less@example.com", "X-Amr": "pwd"}
    assert (await client.get(f"{SYS}/staff/join/{token}", headers=newcomer)).json()["detail"]["code"] == "two_step_required"


async def test_with_the_rule_on_a_risky_action_waits_for_a_second_owner(client, mailbox, clean_rules, monkeypatch):
    # The rule needs two owners who can approve.
    from app.api.v1.sysadmin import approvals

    async def only_one(db):
        return [{"id": "usr_platform_owner", "email": None}]
    with monkeypatch.context() as m:
        m.setattr(approvals, "approvers", only_one)
        refused = await client.put(f"{SYS}/platform/approvals", headers=OWNER, json={"on": True, "reason": "Four eyes on risky changes"})
    assert refused.status_code == 409
    second = staff_headers("usr_second_owner", "Sam Second")
    await make_staff(client, second, "owner")
    on = await client.put(f"{SYS}/platform/approvals", headers=OWNER, json={"on": True, "reason": "Four eyes on risky changes"})
    assert on.status_code == 200 and on.json()["on"] is True

    await organization("riskyorg")
    mailbox.clear()
    held = await client.post(f"{SYS}/organizations/tenant_riskyorg/suspend", headers=OWNER, json={"reason": "Unpaid for three months"})
    assert held.status_code == 202
    approval = held.json()["approval"]
    assert approval["status"] == "pending" and approval["summary"] == "Suspend Riskyorg"
    async with AsyncSessionLocal() as db:
        assert (await db.get(Tenant, "tenant_riskyorg")).status == "active"   # nothing happened yet
    assert any(m.to == "usr_second_owner@example.com" and "Waiting for your approval" in m.subject for m in mailbox)

    # Not by the person who asked; only by an owner; only once.
    assert (await client.post(f"{SYS}/approvals/{approval['id']}/approve", headers=OWNER, json={})).status_code == 409
    waiting = (await client.get(f"{SYS}/approvals", headers=second)).json()
    assert waiting["waiting"] == 1 and waiting["items"][0]["requested_by_name"] == "Pat Platform"
    done = await client.post(f"{SYS}/approvals/{approval['id']}/approve", headers=second, json={"note": "Checked the invoices"})
    assert done.status_code == 200 and done.json()["approval"]["status"] == "done"
    async with AsyncSessionLocal() as db:
        assert (await db.get(Tenant, "tenant_riskyorg")).status == "suspended"
        actions = [(e.action, e.actor_id) for e in (await db.execute(select(PlatformAuditEntry).where(
            PlatformAuditEntry.target_id.in_(["tenant_riskyorg", approval["id"]])).order_by(PlatformAuditEntry.created_at))).scalars()]
    assert actions == [("approval.requested", "usr_platform_owner"), ("approval.approved", "usr_second_owner"),
                       ("organization.suspended", "usr_platform_owner")]
    assert (await client.post(f"{SYS}/approvals/{approval['id']}/approve", headers=second, json={})).status_code == 409

    # Turned down, with a reason the asker hears.
    held = (await client.post(f"{SYS}/organizations/tenant_riskyorg/reactivate", headers=OWNER, json={"reason": "Paid in full"}))
    assert held.status_code == 200   # reactivating isn't risky
    held = (await client.post(f"{SYS}/billing/organizations/tenant_riskyorg/credits", headers=OWNER,
                              json={"pages": 5000, "kind": "grant", "reason": "Goodwill"})).json()["approval"]
    assert (await client.post(f"{SYS}/approvals/{held['id']}/reject", headers=second, json={})).status_code == 422
    mailbox.clear()
    assert (await client.post(f"{SYS}/approvals/{held['id']}/reject", headers=second, json={"note": "Too many pages"})).json()["status"] == "rejected"
    assert [m.subject for m in mailbox] == ["Turned down: Give 5,000 prepaid pages (grant) for Riskyorg"]

    # An expired approval can't be approved.
    late = (await client.post(f"{SYS}/billing/organizations/tenant_riskyorg/credits", headers=OWNER,
                              json={"pages": 10, "kind": "grant", "reason": "Goodwill"})).json()["approval"]
    async with AsyncSessionLocal() as db:
        await db.execute(update(PlatformApproval).where(PlatformApproval.id == late["id"]).values(expires_at=datetime.now(timezone.utc) - timedelta(minutes=1)))
        await db.commit()
    assert (await client.post(f"{SYS}/approvals/{late['id']}/approve", headers=second, json={})).status_code == 409

    # Turning the rule off is guarded by the rule itself.
    off = await client.put(f"{SYS}/platform/approvals", headers=second, json={"on": False, "reason": "Back to one pair of eyes"})
    assert off.status_code == 202
    assert (await client.post(f"{SYS}/approvals/{off.json()['approval']['id']}/approve", headers=OWNER, json={})).status_code == 200
    assert (await client.get(f"{SYS}/platform/approvals", headers=OWNER)).json()["on"] is False


async def test_an_approval_is_not_carried_out_for_someone_who_may_no_longer_do_it(client, clean_rules):
    second = staff_headers("usr_owner_two", "Olive Two")
    await make_staff(client, second, "owner")
    biller = staff_headers("usr_rule_biller", "Bill Rule")
    await make_staff(client, biller, "billing")
    await client.put(f"{SYS}/platform/approvals", headers=OWNER, json={"on": True, "categories": ["billing.giveaways"], "reason": "Billing only"})
    await organization("lostperm")
    held = (await client.post(f"{SYS}/billing/organizations/tenant_lostperm/credits", headers=biller,
                              json={"pages": 100, "kind": "grant", "reason": "Promised on a call"})).json()["approval"]
    # Covered categories only: suspending isn't, now.
    assert (await client.post(f"{SYS}/organizations/tenant_lostperm/suspend", headers=OWNER, json={"reason": "Testing the rule"})).status_code == 200
    await client.patch(f"{SYS}/staff/usr_rule_biller", headers=OWNER, json={"role": "viewer", "reason": "Moved teams"})
    failed = await client.post(f"{SYS}/approvals/{held['id']}/approve", headers=second, json={})
    assert failed.status_code == 409 and "may no longer" in failed.json()["detail"]
    assert (await client.get(f"{SYS}/approvals", headers=OWNER, params={"status": "failed"})).json()["items"][0]["id"] == held["id"]


async def test_unused_access_is_taken_away_and_reviews_come_round(client, mailbox, clean_rules):
    idle = staff_headers("usr_idle_staff", "Ida Idle")
    await make_staff(client, idle, "support")
    owner_two = staff_headers("usr_review_owner", "Rhea Review")
    await make_staff(client, owner_two, "owner")
    now = datetime.now(timezone.utc)

    async def last_used(days_ago: int) -> None:
        async with AsyncSessionLocal() as db:
            await db.execute(update(PlatformStaff).where(PlatformStaff.user_id == "usr_idle_staff").values(last_active_at=now - timedelta(days=days_ago)))
            await db.commit()

    await last_used(54)
    mailbox.clear()
    async with AsyncSessionLocal() as db:
        await staff_offboarding.run(db, now)
        await staff_offboarding.run(db, now)   # told once
    assert [m.subject for m in mailbox if m.to == "usr_idle_staff@example.com"] == ["Your platform staff access ends soon"]

    # Using the admin app again starts the clock again.
    await client.get(f"{SYS}/me", headers=idle)
    async with AsyncSessionLocal() as db:
        row = await db.get(PlatformStaff, "usr_idle_staff")
        assert row.offboard_warned_at is None and row.last_active_at > now - timedelta(minutes=1)

    await last_used(61)
    mailbox.clear()
    async with AsyncSessionLocal() as db:
        await staff_offboarding.run(db, now)
        assert await db.get(PlatformStaff, "usr_idle_staff") is None
        entry = (await db.execute(select(PlatformAuditEntry).where(PlatformAuditEntry.action == "staff.offboarded"))).scalars().first()
        assert entry.actor_id == "system" and entry.target_id == "usr_idle_staff"
    assert any(m.to == "usr_idle_staff@example.com" and "no longer on" in m.subject for m in mailbox)
    assert any(m.to == "usr_review_owner@example.com" and "taken away" in m.subject for m in mailbox)
    assert (await client.get(f"{SYS}/me", headers=idle)).status_code == 403

    # Reviews: due after review_days; owners are reminded once a week; another owner confirms.
    async with AsyncSessionLocal() as db:
        await db.execute(update(PlatformStaff).where(PlatformStaff.user_id == "usr_review_owner").values(created_at=now - timedelta(days=100)))
        await db.commit()
    listed = {m["user_id"]: m for m in (await client.get(f"{SYS}/staff", headers=OWNER)).json()["items"]}
    assert listed["usr_review_owner"]["review_due"] is True
    mailbox.clear()
    async with AsyncSessionLocal() as db:
        await staff_offboarding.run(db, now)
        await staff_offboarding.run(db, now)
    assert len([m for m in mailbox if m.subject.startswith("Access review")]) == len({m.to for m in mailbox if m.subject.startswith("Access review")})
    assert (await client.post(f"{SYS}/staff/usr_review_owner/review", headers=owner_two, json={})).status_code == 409   # not your own
    reviewed = (await client.post(f"{SYS}/staff/usr_review_owner/review", headers=OWNER, json={"note": "Still runs billing"})).json()
    assert reviewed["review_due"] is False and reviewed["reviewed_by_name"] == "Pat Platform"

    # The policy is the owners' to set, within sense.
    bad = await client.put(f"{SYS}/staff/policy", headers=OWNER, json={"inactive_days": 5, "warn_days": 1, "review_days": 90, "reason": "Too strict"})
    assert bad.status_code == 422
    ok = await client.put(f"{SYS}/staff/policy", headers=OWNER, json={"inactive_days": 0, "warn_days": 0, "review_days": 180, "reason": "Reviews only"})
    assert ok.json() == {"inactive_days": 0, "warn_days": 0, "review_days": 180}
    assert (await client.get(f"{SYS}/staff", headers=OWNER)).json()["policy"]["review_days"] == 180
