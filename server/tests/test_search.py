"""
The search boxes: staff find anything on the platform; a customer finds only what they may open in their organization.
"""
from datetime import datetime, timezone

from app.models.billing import Invoice
from app.models.identity import OrgMember, User, WorkspaceRole
from app.models.workspace_admin import WorkspaceMember
from tests.conftest import AsyncSessionLocal, person, png_bytes
from tests.test_billing import organization, workspace
from tests.test_invoices import ADMIN_APP, API


async def send(client, org: dict, tpl: str, name: str) -> str:
    res = await client.post(f"{API}/documents/upload", headers={**org, "X-Template-ID": tpl}, data={"process": "false"},
                            files={"file": (name, png_bytes(), "image/png")})
    assert res.status_code == 201, res.text
    return res.json()["id"]


def groups(found: dict) -> dict:
    return {g["key"]: g["items"] for g in found["groups"]}


async def test_staff_find_organizations_documents_workspaces_and_tickets_exact_matches_first(client):
    org = await organization("findable")
    tpl = await workspace(client, org, "Findable Claims")
    doc = await send(client, org, tpl, "findable-scan.png")
    ticket = await client.post(f"{API}/support/tickets", headers=org, data={"subject": "Findable upload stuck", "body": "It stopped.", "category": "technical"})
    assert ticket.status_code == 201, ticket.text

    found = (await client.get(f"{API}/sysadmin/search", headers=ADMIN_APP, params={"q": "findable"})).json()
    by = groups(found)
    assert found["query"] == "findable"
    assert by["organizations"][0]["id"] == "tenant_findable" and by["organizations"][0]["href"] == "/accounts/organizations/tenant_findable"
    assert by["documents"][0]["id"] == doc and by["documents"][0]["href"] == f"/documents/{doc}" and "Findable" in by["documents"][0]["detail"]
    assert by["workspaces"][0]["title"] == "Findable Claims"
    assert by["tickets"][0]["href"] == f"/support/tickets/{ticket.json()['id']}" and "Findable upload stuck" in by["tickets"][0]["title"]
    # The exact name leads its group, and that group comes first
    assert found["groups"][0]["key"] == "organizations"

    # An id finds its thing; runs and documents by id too
    by_id = groups((await client.get(f"{API}/sysadmin/search", headers=ADMIN_APP, params={"q": doc})).json())
    assert by_id["documents"][0]["id"] == doc
    assert (await client.get(f"{API}/sysadmin/search", headers=ADMIN_APP, params={"q": "x"})).status_code == 422
    # Not for the everyday app
    assert (await client.get(f"{API}/sysadmin/search", params={"q": "findable"})).status_code == 403


async def test_a_customer_finds_only_what_they_may_open(client):
    org = await organization("seekers")
    alpha = await workspace(client, org, "Seek Alpha")
    beta = await workspace(client, org, "Seek Beta")
    await workspace(client, org, "Seek Gamma")                     # not theirs
    in_alpha = await send(client, org, alpha, "seekledger-alpha.png")
    await send(client, org, beta, "seekledger-beta.png")

    member = {**person("usr_seeker", "Sam Seeker"), **org}
    async with AsyncSessionLocal() as db:
        db.add(User(id="usr_seeker", email="usr_seeker@example.com", email_verified=True, first_name="Sam", last_name="Seeker"))
        await db.flush()
        db.add(OrgMember(tenant_id="tenant_seekers", user_id="usr_seeker", role="member"))
        db.add(WorkspaceRole(id="seek_outsider", tenant_id="tenant_seekers", template_id=beta, name="Outsider", permissions=["members.view"]))
        await db.flush()
        db.add(WorkspaceMember(tenant_id="tenant_seekers", template_id=alpha, user_id="usr_seeker", name="Sam Seeker", role="viewer"))
        db.add(WorkspaceMember(tenant_id="tenant_seekers", template_id=beta, user_id="usr_seeker", name="Sam Seeker", role="seek_outsider"))
        now = datetime.now(timezone.utc)
        db.add(Invoice(tenant_id="tenant_seekers", kind="subscription", number="SEEKLEDGER-1", status="issued", currency="EUR", subtotal_cents=100,
                       tax_cents=0, total_cents=100, amount_paid_cents=0, seller={}, buyer={}, bank=[], tax_lines=[], issued_at=now, due_at=now, created_by="usr_platform_owner"))
        await db.commit()
    mine = await client.post(f"{API}/conversations", headers={**member, "X-Template-ID": alpha}, json={"title": "Seekledger questions"})
    assert mine.status_code in (200, 201), mine.text
    theirs = await client.post(f"{API}/conversations", headers={**org, "X-Template-ID": alpha}, json={"title": "Seekledger owner notes"})
    assert theirs.status_code in (200, 201), theirs.text

    by = groups((await client.get(f"{API}/search", headers=member, params={"q": "seekledger"})).json())
    # Documents only where their role lets them view documents
    assert [d["id"] for d in by["documents"]] == [in_alpha] and by["documents"][0]["href"] == f"/w/seek-alpha/documents/{in_alpha}"
    # Their own chats, never someone else's
    assert [c["title"] for c in by["chats"]] == ["Seekledger questions"] and by["chats"][0]["href"] == "/w/seek-alpha" and by["chats"][0]["workspace_id"] == alpha
    # No invoices without managing billing
    assert "invoices" not in by

    spaces = groups((await client.get(f"{API}/search", headers=member, params={"q": "seek"})).json())["workspaces"]
    assert sorted(w["title"] for w in spaces) == ["Seek Alpha", "Seek Beta"]
    people = groups((await client.get(f"{API}/search", headers=member, params={"q": "sam see"})).json())["people"]
    assert people[0]["title"] == "Sam Seeker" and people[0]["href"] == "/people"

    # The owner manages billing and sees every workspace, and their own chat
    owner = groups((await client.get(f"{API}/search", headers=org, params={"q": "seekledger"})).json())
    assert owner["invoices"][0]["title"] == "SEEKLEDGER-1" and owner["invoices"][0]["href"].endswith("?invoice=" + owner["invoices"][0]["id"])
    assert [c["title"] for c in owner["chats"]] == ["Seekledger owner notes"]
    assert sorted(w["title"] for w in groups((await client.get(f"{API}/search", headers=org, params={"q": "seek"})).json())["workspaces"]) == [
        "Seek Alpha", "Seek Beta", "Seek Gamma"]
