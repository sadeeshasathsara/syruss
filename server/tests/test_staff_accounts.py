"""
Staff accounts are kept apart from customer accounts: anyone is invited by email (no account needed), a customer account
under the same sign-in is deleted only after its owner confirms what that removes, and a staff account can never open
customer pages, belong to an organization or be invited into one.
"""
from sqlalchemy import select
from app.models.identity import OrgMember, User
from app.models.platform import PlatformStaff
from app.models.tenant import Tenant
from tests.conftest import AsyncSessionLocal, create_template, make_staff, person, register, ws
from tests.test_billing import organization

API = "/api/v1"
OWNER = {"X-Client-ID": "syruss-admin", "X-User-ID": "usr_platform_owner", "X-Username": "Pat Platform", "X-Email": "owner@syruss.test"}


def admin_app(who: dict) -> dict:
    return {**who, "X-Client-ID": "syruss-admin"}


async def invite(client, email: str, role: str = "support") -> str:
    res = await client.post(f"{API}/sysadmin/staff/invitations", headers=OWNER, json={"email": email, "role": role, "reason": "Joining support"})
    assert res.status_code == 201, res.text
    return res.json()["link"].rsplit("/", 1)[1]


async def test_anyone_is_invited_by_email_and_joins_without_an_account(client, mailbox):
    token = await invite(client, "New.Hire@Example.com")
    mail = [m for m in mailbox if m.to == "new.hire@example.com"]
    assert len(mail) == 1 and "Pat Platform invited you to the Syruss platform staff" == mail[0].subject and f"/join/{token}" in mail[0].text
    pending = (await client.get(f"{API}/sysadmin/staff/invitations", headers=OWNER)).json()
    assert any(i["email"] == "new.hire@example.com" and i["status"] == "pending" for i in pending)

    newcomer = person("usr_new_hire", "Nina Hire") | {"X-Email": "new.hire@example.com"}
    # Only in the admin app, only with the invited address.
    assert (await client.get(f"{API}/sysadmin/staff/join/{token}", headers=newcomer)).json()["detail"]["code"] == "admin_app_required"
    stranger = admin_app(person("usr_stranger", "Stan Ger"))
    assert (await client.get(f"{API}/sysadmin/staff/join/{token}", headers=stranger)).json()["problem"]["code"] == "wrong_address"

    view = (await client.get(f"{API}/sysadmin/staff/join/{token}", headers=admin_app(newcomer))).json()
    assert view["problem"] is None and view["customer_account"] is None and view["invitation"]["role"] == "support"
    mailbox.clear()
    assert (await client.post(f"{API}/sysadmin/staff/join/{token}", headers=admin_app(newcomer), json={})).json() == {"role": "support"}
    me = (await client.get(f"{API}/sysadmin/me", headers=admin_app(newcomer))).json()
    assert me["role"] == "support" and me["name"] == "Nina Hire"
    assert any(m.to == "new.hire@example.com" and m.subject == "You're on the Syruss platform staff" for m in mailbox)
    assert any(m.to == "owner@syruss.test" or "joined the platform staff" in m.subject for m in mailbox)
    # The link works once.
    assert (await client.post(f"{API}/sysadmin/staff/join/{token}", headers=admin_app(newcomer), json={})).status_code == 409


async def test_a_customer_account_is_deleted_only_after_its_owner_confirms(client):
    customer = person("usr_turning_staff", "Tia Turner")
    await register(customer, org_role=None)
    await organization("tiaorg")                      # Tia will be its only owner
    async with AsyncSessionLocal() as db:
        db.add(OrgMember(tenant_id="tenant_tiaorg", user_id="usr_turning_staff", role="owner"))
        from app.models.identity import OrgMember as M
        await db.execute(M.__table__.delete().where(M.tenant_id == "tenant_tiaorg", M.user_id != "usr_turning_staff"))
        db.add(OrgMember(tenant_id="tenant_default", user_id="usr_turning_staff", role="member"))
        await db.commit()
    token = await invite(client, "usr_turning_staff@example.com", "billing")

    view = (await client.get(f"{API}/sysadmin/staff/join/{token}", headers=admin_app(customer))).json()
    outcomes = {o["id"]: o["outcome"] for o in view["customer_account"]["organizations"]}
    assert outcomes == {"tenant_tiaorg": "closed", "tenant_default": "left"}
    refused = await client.post(f"{API}/sysadmin/staff/join/{token}", headers=admin_app(customer), json={})
    assert refused.status_code == 409 and refused.json()["detail"]["code"] == "confirm_customer_account_deletion"
    async with AsyncSessionLocal() as db:
        assert (await db.execute(select(OrgMember).where(OrgMember.user_id == "usr_turning_staff"))).scalars().all()  # nothing changed yet

    ok = await client.post(f"{API}/sysadmin/staff/join/{token}", headers=admin_app(customer), json={"delete_customer_account": True})
    assert ok.status_code == 200
    async with AsyncSessionLocal() as db:
        assert (await db.execute(select(OrgMember).where(OrgMember.user_id == "usr_turning_staff"))).scalars().all() == []
        assert (await db.get(Tenant, "tenant_tiaorg")).status == "closed"
        assert (await db.get(Tenant, "tenant_default")).status == "active"
        assert (await db.get(PlatformStaff, "usr_turning_staff")).role == "billing"
        assert (await db.get(User, "usr_turning_staff")).phone is None


async def test_a_staff_account_never_opens_customer_pages_or_joins_an_organization(client):
    staff = person("usr_kept_apart", "Kai Apart")
    await make_staff(client, staff, "viewer")
    kind = (await client.get(f"{API}/me/kind", headers=staff)).json()
    assert kind["staff"] is True and kind["admin_url"]
    assert (await client.get(f"{API}/workspaces", headers=staff)).json()["detail"]["code"] == "staff_account"
    assert (await client.post(f"{API}/organizations", headers=staff, json={"name": "Side project"})).json()["detail"]["code"] == "staff_account"
    assert (await client.get(f"{API}/me/context", headers=staff)).json()["staff"] is True
    # Organizations and workspaces can't invite a staff address.
    assert (await client.post(f"{API}/organization/invitations", json={"email": "usr_kept_apart@example.com"})).status_code == 409
    tpl = await create_template(client)
    assert (await client.post(f"{API}/settings/invitations", headers=ws(tpl), json={"email": "usr_kept_apart@example.com", "role": "viewer"})).status_code == 409
    # A customer is never staff by accident.
    assert (await client.get(f"{API}/me/kind")).json()["staff"] is False


async def test_invitations_are_resent_and_withdrawn_and_the_first_owner_comes_from_the_server(client, mailbox, capsys):
    await invite(client, "maybe@example.com")
    row = next(i for i in (await client.get(f"{API}/sysadmin/staff/invitations", headers=OWNER)).json() if i["email"] == "maybe@example.com")
    resent = await client.post(f"{API}/sysadmin/staff/invitations/{row['id']}/resend", headers=OWNER)
    assert resent.status_code == 200 and resent.json()["email_sent"] is True
    mailbox.clear()
    assert (await client.delete(f"{API}/sysadmin/staff/invitations/{row['id']}", headers=OWNER)).status_code == 204
    assert [m.subject for m in mailbox] == ["Your invitation to the Syruss platform staff was withdrawn"]

    from app.invite_staff import main
    await main("first.owner@example.com", "owner")
    printed = capsys.readouterr().out
    assert "Invited first.owner@example.com as owner" in printed and "/join/" in printed
