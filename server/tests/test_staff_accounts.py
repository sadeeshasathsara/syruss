"""
Staff accounts are kept apart from customer accounts: anyone is added from the server, which makes their sign-in if they
have none (the admin app adds nobody and creates no accounts), a customer account
under the same sign-in is deleted only after its owner confirms what that removes, and a staff account can never open
customer pages, belong to an organization or be invited into one.
"""
from sqlalchemy import select
from app.models.identity import OrgMember, User
from app.models.platform import PlatformStaff
from app.models.tenant import Tenant
from app.keycloak_admin import KeycloakAdminError
from tests.conftest import AsyncSessionLocal, create_template, invite_staff, make_staff, person, register, ws
from tests.test_billing import organization

API = "/api/v1"
OWNER = {"X-Client-ID": "syruss-admin", "X-User-ID": "usr_platform_owner", "X-Username": "Pat Platform", "X-Email": "owner@syruss.test"}


def admin_app(who: dict) -> dict:
    return {**who, "X-Client-ID": "syruss-admin"}


async def invite(client, email: str, role: str = "support") -> str:
    return await invite_staff(email, role, "Joining support")


class FakeKeycloak:
    """Keycloak's admin API as the server command uses it: who has a sign-in, and what it was asked to do."""

    def __init__(self, existing=(), fail_email=False):
        self.users = {e: {"id": f"kc-{e}", "email": e, "requiredActions": []} for e in existing}
        self.setups, self.deleted, self.fail_email = [], [], fail_email

    async def find_user(self, email):
        return self.users.get(email)

    async def create_user(self, email):
        self.users[email] = {"id": f"kc-{email}", "email": email, "requiredActions": ["UPDATE_PASSWORD", "CONFIGURE_TOTP"]}
        return f"kc-{email}"

    async def send_setup(self, user_id, *, client_id, redirect_uri, lifespan_seconds):
        if self.fail_email:
            raise KeycloakAdminError("Keycloak answered 500 to PUT /users/x/execute-actions-email: no mail server")
        self.setups.append((user_id, client_id, redirect_uri))

    async def delete_user(self, user_id):
        self.deleted.append(user_id)
        self.users = {e: u for e, u in self.users.items() if u["id"] != user_id}


async def test_anyone_is_invited_by_email_and_joins_without_an_account(client, mailbox):
    await make_staff(client, admin_app(person("usr_hearing_owner", "Hal Owner")), "owner")   # an owner with an address, to hear of it
    mailbox.clear()
    token = await invite(client, "New.Hire@Example.com")
    mail = [m for m in mailbox if m.to == "new.hire@example.com"]
    assert len(mail) == 1 and "The Syruss deployment invited you to the Syruss platform staff" == mail[0].subject and f"/join/{token}" in mail[0].text
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
    assert any(m.to == "usr_hearing_owner@example.com" and "joined the platform staff" in m.subject for m in mailbox)   # the owners hear of it
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


async def test_staff_are_added_only_from_the_server_which_makes_their_sign_in(client, mailbox, capsys):
    from app.invite_staff import main
    # A new address: a sign-in is made, Keycloak emails its setup link (back to the invitation), and so does Syruss.
    keycloak = FakeKeycloak()
    assert await main("first.owner@example.com", "owner", "The first owner", keycloak=keycloak) == 0
    printed = capsys.readouterr().out
    assert "Invited first.owner@example.com as owner" in printed and "/join/" in printed
    [(user_id, client_id, redirect)] = keycloak.setups
    assert user_id == "kc-first.owner@example.com" and client_id == "syruss-admin" and redirect.endswith(printed.strip().rsplit("/", 1)[1])
    told = [m for m in mailbox if m.to == "first.owner@example.com"]
    assert len(told) == 1 and "separate email from Syruss sign-in" in told[0].text

    # Again: fresh links, and the setup email again while it isn't done.
    assert await main("first.owner@example.com", "owner", keycloak=keycloak) == 0
    assert len(keycloak.setups) == 2
    pending = [i for i in (await client.get(f"{API}/sysadmin/staff/invitations", headers=OWNER)).json() if i["email"] == "first.owner@example.com"]
    assert len(pending) == 1

    # Someone who already has a sign-in only gets the invitation.
    keycloak = FakeKeycloak(existing=["has.signin@example.com"])
    assert await main("has.signin@example.com", "support", keycloak=keycloak) == 0
    assert keycloak.setups == [] and "already have a Syruss sign-in" in capsys.readouterr().out

    # When Keycloak can't send its email, nothing is left behind.
    keycloak = FakeKeycloak(fail_email=True)
    assert await main("no.mail@example.com", "viewer", keycloak=keycloak) == 1
    assert keycloak.deleted == ["kc-no.mail@example.com"] and "harden.py --smtp" in capsys.readouterr().err
    listed = (await client.get(f"{API}/sysadmin/staff/invitations", headers=OWNER)).json()
    assert not any(i["email"] == "no.mail@example.com" for i in listed)

    # Without the Keycloak admin login, it says what it needs.
    assert await main("nobody@example.com", "viewer") == 2


async def test_owners_withdraw_invitations_but_add_nobody_in_the_admin_app(client, mailbox):
    await invite(client, "maybe@example.com")
    assert (await client.post(f"{API}/sysadmin/staff/invitations", headers=OWNER, json={"email": "x@example.com", "role": "viewer"})).status_code == 405
    row = next(i for i in (await client.get(f"{API}/sysadmin/staff/invitations", headers=OWNER)).json() if i["email"] == "maybe@example.com")
    assert (await client.post(f"{API}/sysadmin/staff/invitations/{row['id']}/resend", headers=OWNER)).status_code in (404, 405)
    mailbox.clear()
    assert (await client.delete(f"{API}/sysadmin/staff/invitations/{row['id']}", headers=OWNER)).status_code == 204
    assert [m.subject for m in mailbox] == ["Your invitation to the Syruss platform staff was withdrawn"]
