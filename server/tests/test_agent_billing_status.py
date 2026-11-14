"""
The agent reads the organization's billing only for the person it is chatting with, and only when their organization role
manages billing: never in a work run (a document can't make it look), never for a member who can't see billing.
"""
from app.core.agent_token import mint_agent_token
from app.models.identity import OrgMember
from app.models.outbox import AgentEventOutbox
from app.models.workspace_admin import WorkspaceMember
from tests.conftest import AsyncSessionLocal, person, register
from tests.mock_iam import DEV_USER_ID, DEV_USER_NAME
from tests.test_billing import workspace
from tests.test_invoices import ADMIN_APP, API
from tests.test_payment_proofs import bought, photo, send_slip

AGENT = "/internal/agent/v1"


async def run_as(tenant: str, tpl: str, event_type: str, actor: dict, payload: dict) -> dict:
    """Headers for an agent run woken by this event."""
    async with AsyncSessionLocal() as db:
        row = AgentEventOutbox(tenant_id=tenant, template_id=tpl, event_type=event_type, payload=payload, actor=actor)
        db.add(row)
        await db.commit()
        return {"Authorization": f"Bearer {mint_agent_token(tenant, tpl, row.id)}"}


def chat(user_id: str, name: str) -> tuple:
    return "human.message", {"type": "user", "id": user_id, "name": name}, {"conversation_id": "c1", "conversation_kind": "chat"}


async def test_the_agent_reads_billing_only_for_a_billing_manager_in_their_chat(client):
    org, tenant, invoice = await bought(client, "agentbill", "pages-500-agentbill")
    tpl = await workspace(client, org)
    sent = (await send_slip(client, org, invoice["id"], [("slip.jpg", photo((9, 9, 9)), "image/jpeg")], reference="REF-A")).json()
    rejected = await client.post(f"{API}/sysadmin/billing/submissions/{sent['id']}/reject", headers=ADMIN_APP,
                                 json={"reason": "Nothing arrived on our account with that reference yet."})
    assert rejected.status_code == 200, rejected.text

    # The owner, chatting: the plan, the invoice still waiting, and why the slip wasn't confirmed.
    owner = await run_as(tenant, tpl, *chat(DEV_USER_ID, DEV_USER_NAME))
    res = await client.get(f"{AGENT}/billing/status", headers=owner)
    assert res.status_code == 200, res.text
    status = res.json()
    assert status["plan"]["name"] and status["plan"]["status"] in ("active", "trialing", "past_due")
    [waiting] = status["open_invoices"]
    assert (waiting["number"], waiting["status"], waiting["total_cents"], waiting["amount_due_cents"]) == (
        invoice["number"], "open", invoice["total_cents"], invoice["total_cents"])
    assert waiting["due_at"]
    latest = status["latest_payment_sent"]
    assert latest["invoice"] == invoice["number"] and latest["status"] == "rejected"
    assert latest["rejection_reason"].startswith("Nothing arrived")
    assert "id" not in waiting and "tenant_id" not in waiting  # only what a person needs, no internal ids

    # A member of the workspace whose organization role can't see billing: refused, with who can.
    member = person("usr_agentbill_member", "Mo Member")
    await register(member, None)
    async with AsyncSessionLocal() as db:
        db.add(OrgMember(tenant_id=tenant, user_id="usr_agentbill_member", role="member"))
        db.add(WorkspaceMember(tenant_id=tenant, template_id=tpl, user_id="usr_agentbill_member", name="Mo Member", role="editor"))
        await db.commit()
    refused = await client.get(f"{AGENT}/billing/status", headers=await run_as(tenant, tpl, *chat("usr_agentbill_member", "Mo Member")))
    assert refused.status_code == 403 and "owners" in refused.json()["detail"]

    # An organization admin can't either: billing is for its owners.
    async with AsyncSessionLocal() as db:
        row = (await db.execute(OrgMember.__table__.select().where(OrgMember.user_id == "usr_agentbill_member"))).first()
        await db.execute(OrgMember.__table__.update().where(OrgMember.id == row.id).values(role="admin"))
        await db.commit()
    admin = await client.get(f"{AGENT}/billing/status", headers=await run_as(tenant, tpl, *chat("usr_agentbill_member", "Mo Member")))
    assert admin.status_code == 403

    # A work run (a document being read), even one the owner started: never.
    work = await run_as(tenant, tpl, "document.submitted", {"type": "user", "id": DEV_USER_ID, "name": DEV_USER_NAME}, {"document_id": "d1"})
    assert (await client.get(f"{AGENT}/billing/status", headers=work)).status_code == 403
    # Nor a reply in a conversation Syruss opened (not the person's own chat).
    thread = await run_as(tenant, tpl, "human.message", {"type": "user", "id": DEV_USER_ID, "name": DEV_USER_NAME},
                          {"conversation_id": "c2", "conversation_kind": "agent"})
    assert (await client.get(f"{AGENT}/billing/status", headers=thread)).status_code == 403
