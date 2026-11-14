"""
The organization's billing as it stands, read-only, for the agent to answer a person who asks about it in a chat.

Billing belongs to the organization and only the people who manage it (org billing.manage: its owners) may see it, so
this answers only during a person's chat message, and only when that person's organization role can manage billing.
Work runs, evaluations and anyone else are refused: a document can never make the agent read billing.
"""
from typing import Any, Dict
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from app.api.internal.agent_tools.scope import Workspace, get_workspace
from app.core.org_permissions import ORG_GRANTS
from app.models.billing import CatalogItem, Invoice, PaymentSubmission
from app.models.identity import OrgMember
from app.models.outbox import AgentEventOutbox
from app.services.billing import invoices, payments, proofs, service as billing

router = APIRouter()


async def _billing_manager(ws: Workspace) -> None:
    """Refuses unless this run is a person's chat message and their organization role manages billing."""
    event = await ws.db.get(AgentEventOutbox, ws.event_id)
    actor = (event.actor or {}) if event else {}
    if (event is None or event.event_type != "human.message" or (event.payload or {}).get("conversation_kind") != "chat"
            or actor.get("type") != "user"):
        raise HTTPException(status_code=403, detail="Billing is only read in a chat, for the person asking.")
    member = (await ws.db.execute(select(OrgMember).where(
        OrgMember.tenant_id == ws.tenant_id, OrgMember.user_id == (actor.get("id") or "")))).scalar_one_or_none()
    if member is None or "billing.manage" not in ORG_GRANTS.get(member.role, set()):
        raise HTTPException(status_code=403, detail=(
            f"{actor.get('name') or 'This person'}'s organization role can't see billing: only the organization's owners can. "
            "They can ask an owner."))


def _invoice(i: Invoice) -> Dict[str, Any]:
    d = invoices.describe(i)
    return {k: d[k] for k in ("number", "kind", "status", "overdue", "currency", "total_cents", "amount_due_cents", "issued_at", "due_at")}


@router.get("/billing/status")
async def billing_status(ws: Workspace = Depends(get_workspace)):
    await _billing_manager(ws)
    db = ws.db
    s = await billing.standing(db, ws.tenant_id)
    sub = s.subscription
    pending = await db.get(CatalogItem, sub.pending_plan_id) if sub.pending_plan_id else None
    waiting = (await db.execute(select(Invoice).where(Invoice.tenant_id == ws.tenant_id, Invoice.status.in_(invoices.PAYABLE))
                                .order_by(Invoice.due_at))).scalars().all()
    latest = (await db.execute(select(PaymentSubmission, Invoice.number).join(Invoice, Invoice.id == PaymentSubmission.invoice_id)
                               .where(PaymentSubmission.tenant_id == ws.tenant_id)
                               .order_by(PaymentSubmission.created_at.desc()).limit(1))).first()
    sent = None
    if latest:
        submission, number = latest
        d = payments.describe(submission, await proofs.files_of(db, submission_id=submission.id))
        sent = {"invoice": number, "status": d["status"], "amount_cents": d["amount_cents"], "currency": d["currency"],
                "paid_on": d["paid_on"], "sent_at": d["created_at"], "rejection_reason": d["rejection_reason"]}
    result = {
        "plan": {"name": s.plan.name, "status": sub.status, "period_start": sub.period_start, "period_end": sub.period_end,
                 "trial_ends_at": sub.trial_ends_at, "changes_to": pending.name if pending else None},
        "open_invoices": [_invoice(i) for i in waiting],
        "latest_payment_sent": sent,
        "where": "Settings → Billing (Invoices to pay or send a slip)",
    }
    await db.commit()  # the subscription may have been created or brought up to date
    return result
