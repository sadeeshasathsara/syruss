"""
A second owner's approval for risky staff actions (the four-eyes rule), when the platform's owners turn it on.

With the rule on, an action it covers isn't carried out when it is asked for: it waits (202, the approval) until a
platform owner other than the person who asked approves it, within APPROVAL_HOURS. It is then carried out as the person
who asked, only if they may still do it, through the same code as without the rule. Rejecting or cancelling ends it.
Every step is in the platform audit log, and the owners are emailed when one is waiting.

The rule itself is guarded too: while it is on, changing or turning it off waits for a second owner like anything else.
It can be turned on only while the platform has at least two owners who can approve. If owners are lost while it is on,
the server can turn it off: python -m app.approvals off.

Each risky operation registers once (`risky`), and its endpoint calls `act` instead of carrying it out itself.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.v1.sysadmin.access import Staff, record, require_staff, staff_for
from app.core.config import settings
from app.core.database import AsyncSessionLocal, get_db_session
from app.models.identity import User
from app.models.platform import PlatformApproval, PlatformStaff
from app.services import platform_controls

router = APIRouter()

APPROVAL_HOURS = 24
SETTING = "approvals"

# What the rule can cover, as owners choose it in the admin app (each risky operation belongs to one)
CATEGORIES: Dict[str, str] = {
    "organization.suspend": "Suspending an organization",
    "account.disable": "Disabling someone's account",
    "staff.owners": "Adding or removing a platform owner",
    "billing.corrections": "Credit notes and refunds, and voiding an invoice",
    "billing.giveaways": "Prepaid pages, plan changes and switching on an unpaid invoice by hand",
}
RULE_CHANGE = "approvals.change"  # covered whenever the rule is on


@dataclass(frozen=True)
class Risky:
    key: str
    category: str
    permission: str
    run: Callable[[AsyncSession, Staff, Dict[str, Any]], Awaitable[Any]]


OPERATIONS: Dict[str, Risky] = {}


def risky(key: str, category: str, permission: str):
    """Registers how to carry out a risky operation from its stored payload (the same code its endpoint runs)."""
    if category not in CATEGORIES and category != RULE_CHANGE:
        raise ValueError(f"Unknown approval category {category}")

    def register(fn):
        OPERATIONS[key] = Risky(key, category, permission, fn)
        return fn
    return register


# ---- the rule -------------------------------------------------------------------------------------

async def rule(db: AsyncSession) -> Dict[str, Any]:
    """{on, categories}: whether the rule is on and what it covers."""
    stored = (await platform_controls.read_key(db, SETTING)) or {}
    return {"on": bool(stored.get("on")), "categories": [c for c in stored.get("categories", list(CATEGORIES)) if c in CATEGORIES]}


def covers(current: Dict[str, Any], category: str) -> bool:
    return current["on"] and (category == RULE_CHANGE or category in current["categories"])


async def approvers(db: AsyncSession) -> List[Dict[str, Optional[str]]]:
    """The owners who can approve: the deployment's and the stored ones, with an account that isn't disabled."""
    stored = (await db.execute(select(PlatformStaff.user_id).where(PlatformStaff.role == "owner"))).scalars().all()
    ids = set(stored) | settings.platform_admin_ids
    users = {u.id: u for u in (await db.execute(select(User).where(User.id.in_(ids)))).scalars()} if ids else {}
    return [{"id": i, "email": users[i].email if i in users else None} for i in sorted(ids)
            if i not in users or users[i].status == "active"]


# ---- holding and carrying out -----------------------------------------------------------------------

def approval_dict(a: PlatformApproval, now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    status = "expired" if a.status == "pending" and a.expires_at <= now else a.status
    return {"id": a.id, "action": a.action, "category": OPERATIONS[a.action].category if a.action in OPERATIONS else None,
            "summary": a.summary, "reason": a.reason, "target_type": a.target_type, "target_id": a.target_id, "tenant_id": a.tenant_id,
            "requested_by": a.requested_by, "requested_by_name": a.requested_by_name, "status": status, "created_at": a.created_at,
            "expires_at": a.expires_at, "decided_by_name": a.decided_by_name, "decided_at": a.decided_at, "note": a.note}


async def act(db: AsyncSession, staff: Staff, key: str, payload: Dict[str, Any], *, summary: str, target_type: str, target_id: str,
              tenant_id: Optional[str] = None, reason: Optional[str] = None) -> Any:
    """
    Carries out a risky operation now, or, when the rule covers it, holds it for a second owner and answers 202 with
    the approval. The endpoint has checked what it can beforehand (that the target exists, that the change makes sense).
    """
    op = OPERATIONS[key]
    if not covers(await rule(db), op.category):
        return await op.run(db, staff, payload)
    approval = PlatformApproval(action=key, summary=summary, payload=jsonable_encoder(payload), target_type=target_type, target_id=target_id,
                                tenant_id=tenant_id, reason=(reason or "").strip() or None, requested_by=staff.user_id,
                                requested_by_name=staff.name, requested_by_email=staff.email, expires_at=datetime.now(timezone.utc) + timedelta(hours=APPROVAL_HOURS))
    db.add(approval)
    await db.flush()
    record(db, staff, "approval.requested", "approval", approval.id, tenant_id=tenant_id, reason=reason,
           details={"action": key, "summary": summary})
    await db.commit()
    for owner in await approvers(db):
        if owner["id"] != staff.user_id:
            await _tell(owner["email"], f"Waiting for your approval: {summary}",
                        f"{staff.name} asked to {summary[0].lower() + summary[1:]}." + (f" Why: {reason}" if reason else "")
                        + f" It needs a second owner's approval within {APPROVAL_HOURS} hours.")
    return JSONResponse(status_code=202, content=jsonable_encoder({
        "approval": approval_dict(approval),
        "message": "Sent to another platform owner to approve. It's carried out once they do."}))


async def _tell(email: Optional[str], title: str, body: str) -> None:
    from app.services import staff_service
    await staff_service.tell(email, title, body, path="/approvals")


# ---- deciding -------------------------------------------------------------------------------------

async def _pending(db: AsyncSession, approval_id: str) -> PlatformApproval:
    """The approval, locked against a second owner deciding it at the same moment."""
    row = (await db.execute(select(PlatformApproval).where(PlatformApproval.id == approval_id).with_for_update())).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="No such approval.")
    if row.status != "pending":
        raise HTTPException(status_code=409, detail=f"This was already {row.status}.")
    if row.expires_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=409, detail="This expired before anyone approved it. Ask for it again if it's still needed.")
    return row


def _owner(staff: Staff) -> Staff:
    if staff.role != "owner":
        raise HTTPException(status_code=403, detail={"code": "platform_permission_denied", "message": "Only a platform owner approves."})
    return staff


class Decision(BaseModel):
    note: Optional[str] = Field(None, max_length=1000)


@router.get("/approvals", dependencies=[Depends(require_staff())])
async def list_approvals(status: str = Query("pending", pattern="^(pending|done|rejected|cancelled|failed|all)$"),
                         db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    """Approvals, newest first: waiting ones (expired ones included, to see what lapsed), or decided ones."""
    stmt = select(PlatformApproval).order_by(PlatformApproval.created_at.desc()).limit(100)
    if status != "all":
        stmt = stmt.where(PlatformApproval.status == status)
    now = datetime.now(timezone.utc)
    waiting = await db.scalar(select(func.count()).select_from(PlatformApproval).where(
        PlatformApproval.status == "pending", PlatformApproval.expires_at > now))
    return {"items": [approval_dict(a, now) for a in (await db.execute(stmt)).scalars().all()], "waiting": waiting or 0}


@router.post("/approvals/{approval_id}/approve")
async def approve(approval_id: str, body: Decision, staff: Staff = Depends(require_staff("staff.manage", recent=True)),
                  db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    """Carries the action out, as the person who asked. You can't approve your own."""
    _owner(staff)
    row = await _pending(db, approval_id)
    if row.requested_by == staff.user_id:
        raise HTTPException(status_code=409, detail="You asked for this: another owner has to approve it.")
    op = OPERATIONS.get(row.action)
    asker = await staff_for(db, row.requested_by)
    problem = None
    if op is None:
        problem = "This kind of action can no longer be carried out."
    elif asker is None or not asker.can(op.permission):
        problem = f"{row.requested_by_name} may no longer do this, so it wasn't carried out."
    if problem:
        await _close(db, row, staff, "failed", problem)
        raise HTTPException(status_code=409, detail=problem)
    _decide(row, staff, "done", body.note)
    record(db, staff, "approval.approved", "approval", row.id, tenant_id=row.tenant_id, reason=body.note,
           details={"action": row.action, "summary": row.summary, "requested_by": row.requested_by_name})
    try:
        result = await op.run(db, asker, row.payload)  # commits the approval with the change itself
        await db.commit()
    except HTTPException as exc:  # it can't be done any more (already done, or the target changed)
        await db.rollback()
        message = exc.detail if isinstance(exc.detail, str) else (exc.detail or {}).get("message", "It couldn't be carried out.")
        async with AsyncSessionLocal() as fresh:
            again = await fresh.get(PlatformApproval, approval_id)
            await _close(fresh, again, staff, "failed", message)
        raise
    await _tell(row.requested_by_email or await _email(db, row.requested_by), f"Approved: {row.summary}",
                f"{staff.name} approved what you asked, and it's done." + (f" Note: {body.note}" if body.note else ""))
    if isinstance(result, JSONResponse):  # never another hold: the rule doesn't apply twice
        result = None
    return {"approval": approval_dict(row), "result": jsonable_encoder(result)}


@router.post("/approvals/{approval_id}/reject")
async def reject(approval_id: str, body: Decision, staff: Staff = Depends(require_staff("staff.manage")),
                 db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    _owner(staff)
    if not (body.note or "").strip():
        raise HTTPException(status_code=422, detail="Say why it's turned down: the person who asked is told.")
    row = await _pending(db, approval_id)
    await _close(db, row, staff, "rejected", body.note)
    await _tell(row.requested_by_email or await _email(db, row.requested_by), f"Turned down: {row.summary}", f"{staff.name} turned down what you asked. Why: {body.note}")
    return approval_dict(row)


@router.post("/approvals/{approval_id}/cancel")
async def cancel(approval_id: str, staff: Staff = Depends(require_staff()), db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    """Withdraws something you asked for."""
    row = await _pending(db, approval_id)
    if row.requested_by != staff.user_id:
        raise HTTPException(status_code=403, detail="Only the person who asked can withdraw it.")
    await _close(db, row, staff, "cancelled", None)
    return approval_dict(row)


def _decide(row: PlatformApproval, staff: Staff, status: str, note: Optional[str]) -> None:
    row.status, row.decided_by, row.decided_by_name = status, staff.user_id, staff.name
    row.decided_at, row.note = datetime.now(timezone.utc), (note or "").strip() or None


async def _close(db: AsyncSession, row: PlatformApproval, staff: Staff, status: str, note: Optional[str]) -> None:
    _decide(row, staff, status, note)
    record(db, staff, f"approval.{status}", "approval", row.id, tenant_id=row.tenant_id, reason=note,
           details={"action": row.action, "summary": row.summary, "requested_by": row.requested_by_name})
    await db.commit()


async def _email(db: AsyncSession, user_id: str) -> Optional[str]:
    user = await db.get(User, user_id)
    return user.email if user else None


# ---- the rule's own setting -----------------------------------------------------------------------

@router.get("/platform/approvals", dependencies=[Depends(require_staff())])
async def get_rule(db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    return {**await rule(db), "all_categories": CATEGORIES, "approvers": len(await approvers(db)), "hours": APPROVAL_HOURS}


class RuleIn(BaseModel):
    on: bool
    categories: List[str] = Field(default_factory=lambda: list(CATEGORIES))
    reason: str = Field(..., min_length=5, max_length=1000)


@risky("approvals.change", RULE_CHANGE, "staff.manage")
async def _change_rule(db: AsyncSession, staff: Staff, p: Dict[str, Any]) -> Dict[str, Any]:
    value = {"on": bool(p["on"]), "categories": [c for c in p["categories"] if c in CATEGORIES]}
    if value["on"] and len(await approvers(db)) < 2:
        raise HTTPException(status_code=409, detail="A second owner's approval needs at least two platform owners. Invite another owner first.")
    await platform_controls.save(db, SETTING, value, by_id=staff.user_id, by_name=staff.name)
    record(db, staff, "approvals.on" if value["on"] else "approvals.off", "platform", SETTING, reason=p.get("reason"), details=value)
    await db.commit()
    return value


@router.put("/platform/approvals")
async def set_rule(body: RuleIn, staff: Staff = Depends(require_staff("staff.manage", recent=True)),
                   db: AsyncSession = Depends(get_db_session)) -> Any:
    """Turns the rule on or off, or changes what it covers. While it is on, this waits for a second owner too."""
    unknown = [c for c in body.categories if c not in CATEGORIES]
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown: {', '.join(unknown)}")
    if body.on and not body.categories:
        raise HTTPException(status_code=422, detail="Choose at least one kind of action for the rule to cover.")
    if body.on and len(await approvers(db)) < 2:
        raise HTTPException(status_code=409, detail="A second owner's approval needs at least two platform owners. Invite another owner first.")
    summary = "Turn off the second owner's approval" if not body.on else "Change what needs a second owner's approval"
    return await act(db, staff, "approvals.change", body.model_dump(), summary=summary, target_type="platform", target_id=SETTING,
                     reason=body.reason)
