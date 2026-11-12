"""
The operator's console: how the business is doing, which customers need a look, everything about one organization or one
person, the platform's own switches and announcements, one search box, and a security view. Facts only, never a
customer's content (that still takes an access grant, support_access.py); every change asks for a reason and is recorded.
"""
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, cast, Date, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.v1.sysadmin.access import Staff, record, require_staff
from app.api.v1.sysadmin.common import tell_organization
from app.core.config import settings
from app.core.database import get_db_session
from app.models.audit import AuditLog
from app.models.billing import CatalogItem, Invoice, OrgSubscription, OrgSubscriptionAddon, UsageEvent
from app.models.document import Document, DocumentRole, DocumentStatus
from app.models.identity import OrgMember, SecurityEvent, TrustedDevice, User
from app.models.platform import OrganizationNote, PlatformAccessGrant, PlatformAnnouncement, PlatformAuditEntry, PlatformStaff
from app.models.tenant import Tenant
from app.models.trace import AgentExecutionTrace as Run
from app.models.workspace import Workspace
from app.models.workspace_admin import WorkspaceMember
from app.services import platform_controls
from app.services.billing import service as billing

router = APIRouter()
VIEW = Depends(require_staff())
DAYS = 30


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _days(rows, since: date, days: int = DAYS) -> List[Dict[str, Any]]:
    """[(day, value)] filled in to one point a day, oldest first, missing days as 0."""
    got = {d if isinstance(d, date) else d.date(): v for d, v in rows}
    return [{"day": (since + timedelta(days=i)).isoformat(), "value": float(got.get(since + timedelta(days=i), 0) or 0)} for i in range(days)]


def _action(e: PlatformAuditEntry) -> Dict[str, Any]:
    return {"id": e.id, "actor_id": e.actor_id, "actor_name": e.actor_name, "action": e.action, "target_type": e.target_type,
            "target_id": e.target_id, "tenant_id": e.tenant_id, "reason": e.reason, "details": e.details or {}, "created_at": e.created_at, "ip": None}


class Reason(BaseModel):
    reason: str = Field(..., min_length=5, max_length=1000)


# ---- how the business is doing --------------------------------------------------------------------

async def _active_tenants(db: AsyncSession, since: datetime) -> set:
    """Organizations that did something since then: a document arrived, or the agent ran for them."""
    docs = select(Document.tenant_id).where(Document.created_at >= since, Document.role == DocumentRole.DOCUMENT)
    runs = select(Run.tenant_id).where(Run.started_at >= since)
    return set((await db.execute(docs.union(runs))).scalars().all())


@router.get("/metrics", dependencies=[VIEW])
async def metrics(db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    """
    The business at a glance, and its last 30 days a day at a time: recurring revenue by currency, paying and active
    organizations, sign-ups, documents and pages read, what the agent cost, and who stopped (closed, or lapsed to Free).
    """
    now = _now()
    start = (now - timedelta(days=DAYS - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    day = lambda col: cast(col, Date)  # noqa: E731

    paying = (await db.execute(select(OrgSubscription.tenant_id, CatalogItem.currency, CatalogItem.price_cents)
                               .join(CatalogItem, CatalogItem.id == OrgSubscription.plan_id)
                               .join(Tenant, Tenant.id == OrgSubscription.tenant_id)
                               .where(Tenant.status == "active", OrgSubscription.status.in_(("active", "past_due")),
                                      CatalogItem.price_cents > 0))).all()
    mrr: Dict[str, int] = defaultdict(int)
    for _, currency, cents in paying:
        mrr[currency] += int(cents)
    ids = [t for t, _, _ in paying]
    if ids:
        for currency, cents in (await db.execute(
                select(CatalogItem.currency, func.sum(CatalogItem.price_cents * OrgSubscriptionAddon.quantity))
                .join(OrgSubscriptionAddon, OrgSubscriptionAddon.addon_id == CatalogItem.id)
                .where(OrgSubscriptionAddon.tenant_id.in_(ids)).group_by(CatalogItem.currency))).all():
            mrr[currency] += int(cents or 0)
    trialing = await db.scalar(select(func.count()).select_from(OrgSubscription).where(OrgSubscription.status == "trialing")) or 0
    total = await db.scalar(select(func.count(Tenant.id)).where(Tenant.status == "active")) or 0
    active_7, active_30 = await _active_tenants(db, now - timedelta(days=7)), await _active_tenants(db, now - timedelta(days=30))

    signups = (await db.execute(select(day(Tenant.created_at), func.count()).where(Tenant.created_at >= start).group_by(day(Tenant.created_at)))).all()
    people = (await db.execute(select(day(User.created_at), func.count()).where(User.created_at >= start).group_by(day(User.created_at)))).all()
    documents = (await db.execute(select(day(Document.created_at), func.count()).where(
        Document.created_at >= start, Document.role == DocumentRole.DOCUMENT).group_by(day(Document.created_at)))).all()
    pages = (await db.execute(select(day(UsageEvent.occurred_at), func.sum(UsageEvent.quantity)).where(
        UsageEvent.occurred_at >= start, UsageEvent.metric == "pages").group_by(day(UsageEvent.occurred_at)))).all()
    cost = (await db.execute(select(day(Run.started_at), func.sum(Run.cost_usd)).where(Run.started_at >= start).group_by(day(Run.started_at)))).all()
    runs = (await db.execute(select(func.count(Run.id), func.sum(case((Run.status.in_(("FAILED", "DEAD_LETTERED", "LOST")), 1), else_=0)))
                             .where(Run.started_at >= start))).one()

    closed = await db.scalar(select(func.count(Tenant.id)).where(Tenant.status == "closed", Tenant.status_changed_at >= month)) or 0
    lapsed = await db.scalar(select(func.count(func.distinct(Invoice.tenant_id))).where(
        Invoice.status == "uncollectible", Invoice.kind == "renewal", Invoice.due_at >= month - timedelta(days=31))) or 0
    since_series = start.date()
    return {
        "as_of": now,
        "revenue": [{"currency": c, "mrr_cents": v, "arr_cents": v * 12} for c, v in sorted(mrr.items(), key=lambda kv: -kv[1])],
        "organizations": {"active": total, "paying": len(set(ids)), "trialing": trialing, "active_7d": len(active_7), "active_30d": len(active_30),
                          "closed_this_month": closed, "lapsed_this_month": lapsed},
        "runs": {"last_30d": int(runs[0] or 0), "failed_30d": int(runs[1] or 0)},
        "series": {
            "organizations": _days(signups, since_series), "people": _days(people, since_series),
            "documents": _days(documents, since_series), "pages": _days(pages, since_series), "agent_cost_usd": _days(cost, since_series),
        },
    }


@router.get("/organizations/at-risk", dependencies=[VIEW])
async def at_risk(limit: int = Query(25, ge=1, le=100), db: AsyncSession = Depends(get_db_session)) -> List[Dict[str, Any]]:
    """
    Customers worth a look now, with the reasons: a renewal past due, a trial ending within a week, nearly out of pages,
    reading far fewer pages than the two weeks before, many documents failing, or a paying customer gone quiet.
    """
    now = _now()
    reasons: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    subs = (await db.execute(select(OrgSubscription, CatalogItem).join(CatalogItem, CatalogItem.id == OrgSubscription.plan_id)
                             .join(Tenant, Tenant.id == OrgSubscription.tenant_id).where(Tenant.status == "active"))).all()
    plan_of = {s.tenant_id: (s, p) for s, p in subs}
    for s, p in subs:
        if s.status == "past_due":
            reasons[s.tenant_id].append({"code": "past_due", "text": "Renewal past due", "level": "high"})
        if s.status == "trialing" and s.trial_ends_at and s.trial_ends_at <= now + timedelta(days=7):
            reasons[s.tenant_id].append({"code": "trial_ending", "text": f"Trial ends {s.trial_ends_at:%d %b}", "level": "medium"})

    def pages_between(a: datetime, b: datetime):
        return (select(UsageEvent.tenant_id, func.sum(UsageEvent.quantity))
                .where(UsageEvent.metric == "pages", UsageEvent.occurred_at >= a, UsageEvent.occurred_at < b).group_by(UsageEvent.tenant_id))
    recent = dict((await db.execute(pages_between(now - timedelta(days=14), now))).all())
    before = dict((await db.execute(pages_between(now - timedelta(days=28), now - timedelta(days=14)))).all())
    for tenant_id, earlier in before.items():
        later = int(recent.get(tenant_id) or 0)
        if earlier >= 20 and later < earlier * 0.5:
            reasons[tenant_id].append({"code": "usage_drop", "text": f"Pages down {round(100 - later * 100 / earlier)}% on the two weeks before", "level": "medium"})

    for tenant_id, (s, p) in plan_of.items():
        if p.price_cents > 0 and s.status == "active" and not recent.get(tenant_id) and not before.get(tenant_id):
            reasons[tenant_id].append({"code": "quiet", "text": "Paying, but no pages in four weeks", "level": "medium"})
        limit_pages = (p.limits or {}).get(billing.PAGES)
        if limit_pages:
            used = await db.scalar(select(func.coalesce(func.sum(UsageEvent.quantity - UsageEvent.from_credits), 0)).where(
                UsageEvent.tenant_id == tenant_id, UsageEvent.metric == "pages", UsageEvent.period_start == s.period_start)) or 0
            if used >= limit_pages * 0.9:
                reasons[tenant_id].append({"code": "near_limit", "text": f"{used:,} of {limit_pages:,} pages used this period", "level": "low"})

    week = now - timedelta(days=7)
    failing = (await db.execute(select(Document.tenant_id, func.count(), func.sum(case((Document.status == DocumentStatus.FAILED, 1), else_=0)))
                                .where(Document.created_at >= week, Document.role == DocumentRole.DOCUMENT).group_by(Document.tenant_id))).all()
    for tenant_id, n, failed in failing:
        if n >= 10 and (failed or 0) / n >= 0.2:
            reasons[tenant_id].append({"code": "failures", "text": f"{failed} of {n} documents failed this week", "level": "high"})

    weight = {"high": 3, "medium": 2, "low": 1}
    ranked = sorted(reasons.items(), key=lambda kv: -sum(weight[r["level"]] for r in kv[1]))[:limit]
    tenants = {t.id: t for t in (await db.execute(select(Tenant).where(Tenant.id.in_([k for k, _ in ranked])))).scalars().all()} if ranked else {}
    return [{"tenant_id": k, "name": tenants[k].name if k in tenants else k, "slug": tenants[k].slug if k in tenants else None,
             "plan": plan_of[k][1].name if k in plan_of else None, "reasons": v} for k, v in ranked if k in tenants]


# ---- one organization -----------------------------------------------------------------------------

async def _tenant(db: AsyncSession, tenant_id: str) -> Tenant:
    tenant = await db.get(Tenant, tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="No such organization.")
    return tenant


def _note(n: OrganizationNote) -> Dict[str, Any]:
    return {"id": n.id, "body": n.body, "pinned": n.pinned, "author_name": n.author_name, "author_id": n.author_id, "created_at": n.created_at}


@router.get("/organizations/{tenant_id}/profile", dependencies=[VIEW])
async def organization_profile(tenant_id: str, db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    """Everything about one organization except its content: plan and standing, usage, people, workspaces, money, notes."""
    tenant = await _tenant(db, tenant_id)
    now = _now()
    s = await billing.standing(db, tenant_id)
    summary = await billing.summary(db, tenant_id)
    await db.commit()  # standing may have brought the subscription up to date
    start = (now - timedelta(days=DAYS - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    docs = (await db.execute(select(cast(Document.created_at, Date), func.count()).where(
        Document.tenant_id == tenant_id, Document.role == DocumentRole.DOCUMENT, Document.created_at >= start)
        .group_by(cast(Document.created_at, Date)))).all()
    members = (await db.execute(select(OrgMember, User).join(User, User.id == OrgMember.user_id).where(OrgMember.tenant_id == tenant_id)
                                .order_by(OrgMember.created_at))).all()
    ws_rows = (await db.execute(select(Workspace, func.count(Document.id), func.max(Document.created_at))
                                .outerjoin(Document, and_(Document.template_id == Workspace.id, Document.role == DocumentRole.DOCUMENT))
                                .where(Workspace.tenant_id == tenant_id).group_by(Workspace.id).order_by(Workspace.created_at))).all()
    seats = dict((await db.execute(select(WorkspaceMember.template_id, func.count()).where(WorkspaceMember.tenant_id == tenant_id)
                                   .group_by(WorkspaceMember.template_id))).all())
    open_invoices = (await db.execute(select(Invoice).where(Invoice.tenant_id == tenant_id, Invoice.status == "open")
                                      .order_by(Invoice.due_at))).scalars().all()
    notes = (await db.execute(select(OrganizationNote).where(OrganizationNote.tenant_id == tenant_id)
                              .order_by(OrganizationNote.pinned.desc(), OrganizationNote.created_at.desc()).limit(50))).scalars().all()
    staff_actions = (await db.execute(select(PlatformAuditEntry).where(PlatformAuditEntry.tenant_id == tenant_id)
                                      .order_by(PlatformAuditEntry.created_at.desc()).limit(15))).scalars().all()
    grants = (await db.execute(select(PlatformAccessGrant).where(PlatformAccessGrant.tenant_id == tenant_id)
                               .order_by(PlatformAccessGrant.created_at.desc()).limit(10))).scalars().all()
    runs = (await db.execute(select(func.count(Run.id), func.sum(Run.cost_usd), func.max(Run.started_at)).where(
        Run.tenant_id == tenant_id, Run.started_at >= now - timedelta(days=30)))).one()
    controls = await platform_controls.current()
    due = sum(max(0, i.total_cents - i.amount_paid_cents - i.amount_credited_cents + i.amount_refunded_cents) for i in open_invoices)
    return {
        "organization": {"id": tenant.id, "name": tenant.name, "slug": tenant.slug, "status": tenant.status, "status_reason": tenant.status_reason,
                         "status_changed_at": tenant.status_changed_at, "created_at": tenant.created_at, "purged_at": tenant.purged_at},
        "plan": {"name": s.plan.name, "key": s.plan.key, "price_cents": s.plan.price_cents, "currency": s.plan.currency,
                 "status": s.subscription.status, "period_start": s.subscription.period_start, "period_end": s.subscription.period_end,
                 "trial_ends_at": s.subscription.trial_ends_at, "paid_until": s.subscription.paid_until},
        "usage": {"pages_used": summary["pages_used"], "pages_limit": summary["pages_limit"], "credits": summary["credits"],
                  "documents_30d": _days(docs, start.date()), "runs_30d": int(runs[0] or 0), "agent_cost_30d_usd": float(runs[1] or 0),
                  "last_run_at": runs[2]},
        "money": {"open_invoices": len(open_invoices), "owed_cents": due, "currency": s.plan.currency,
                  "overdue": sum(1 for i in open_invoices if i.due_at and i.due_at < now)},
        "members": [{"user_id": u.id, "name": u.display_name, "email": u.email, "role": m.role, "status": u.status, "last_seen_at": u.last_seen_at,
                     "joined_at": m.created_at} for m, u in members],
        "workspaces": [{"id": w.id, "name": w.name, "slug": w.slug, "documents": int(n or 0), "last_document_at": last, "members": seats.get(w.id, 0),
                        "created_at": w.created_at} for w, n, last in ws_rows],
        "notes": [_note(n) for n in notes],
        "staff_actions": [_action(e) for e in staff_actions],
        "access_grants": [{"staff_name": g.staff_name, "reason": g.reason, "created_at": g.created_at, "expires_at": g.expires_at, "ended_at": g.ended_at}
                          for g in grants],
        "agent_paused": tenant_id in controls.agent_paused_orgs,
    }


class NoteIn(BaseModel):
    body: str = Field(..., min_length=2, max_length=4000)
    pinned: bool = False


@router.post("/organizations/{tenant_id}/notes", status_code=201)
async def add_note(tenant_id: str, body: NoteIn, staff: Staff = Depends(require_staff()), db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    """A note for the rest of the staff (a call, a promise, a warning). The organization never sees it."""
    await _tenant(db, tenant_id)
    note = OrganizationNote(tenant_id=tenant_id, body=body.body.strip(), pinned=body.pinned, author_id=staff.user_id, author_name=staff.name)
    db.add(note)
    await db.flush()
    record(db, staff, "note.added", "tenant", tenant_id, tenant_id=tenant_id, details={"note_id": note.id})
    await db.commit()
    return _note(note)


@router.post("/organizations/{tenant_id}/notes/{note_id}/pin")
async def pin_note(tenant_id: str, note_id: str, staff: Staff = Depends(require_staff()), db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    note = await db.get(OrganizationNote, note_id)
    if note is None or note.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="No such note.")
    note.pinned = not note.pinned
    await db.commit()
    return _note(note)


@router.delete("/organizations/{tenant_id}/notes/{note_id}", status_code=204)
async def delete_note(tenant_id: str, note_id: str, staff: Staff = Depends(require_staff()), db: AsyncSession = Depends(get_db_session)) -> None:
    """Only its author takes a note back (the audit log keeps that it was written and removed)."""
    note = await db.get(OrganizationNote, note_id)
    if note is None or note.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="No such note.")
    if note.author_id != staff.user_id:
        raise HTTPException(status_code=403, detail="Only the person who wrote a note can remove it.")
    record(db, staff, "note.removed", "tenant", tenant_id, tenant_id=tenant_id, details={"note_id": note.id})
    await db.delete(note)
    await db.commit()


class AgentPause(Reason):
    paused: bool


@router.post("/organizations/{tenant_id}/agent")
async def pause_agent_for(tenant_id: str, body: AgentPause, staff: Staff = Depends(require_staff("operations.manage", recent=True)),
                          db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    """Pauses (or resumes) the agent for one organization: its documents wait, in order, and nothing is lost."""
    tenant = await _tenant(db, tenant_id)
    values = (await platform_controls.read(db))[platform_controls.SWITCHES]
    paused = set(values.get("agent_paused_orgs") or [])
    paused = paused | {tenant_id} if body.paused else paused - {tenant_id}
    await platform_controls.save(db, platform_controls.SWITCHES, {**values, "agent_paused_orgs": sorted(paused)}, by_id=staff.user_id, by_name=staff.name)
    record(db, staff, "agent.paused" if body.paused else "agent.resumed", "tenant", tenant_id, tenant_id=tenant_id, reason=body.reason,
           details={"organization": tenant.name})
    tell_organization(db, staff, tenant_id, "paused document reading for the organization" if body.paused else "resumed document reading", body.reason)
    await db.commit()
    if not body.paused:
        from app.services.agent_events import agent_event_relay
        agent_event_relay.notify()
    return {"agent_paused": body.paused}


# ---- one person -----------------------------------------------------------------------------------

@router.get("/people/{user_id}/profile", dependencies=[VIEW])
async def person_profile(user_id: str, db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    """One account: where they belong, the browsers they confirmed, their recent security events, and what staff did."""
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="No such account.")
    orgs = (await db.execute(select(Tenant, OrgMember.role).join(OrgMember, OrgMember.tenant_id == Tenant.id)
                             .where(OrgMember.user_id == user_id).order_by(Tenant.name))).all()
    spaces = (await db.execute(select(Workspace, WorkspaceMember.role).join(WorkspaceMember, WorkspaceMember.template_id == Workspace.id)
                               .where(WorkspaceMember.user_id == user_id).order_by(Workspace.name))).all()
    devices = (await db.execute(select(TrustedDevice).where(TrustedDevice.user_id == user_id, TrustedDevice.status == "trusted")
                                .order_by(TrustedDevice.last_seen_at.desc()))).scalars().all()
    events = (await db.execute(select(SecurityEvent).where(SecurityEvent.user_id == user_id)
                               .order_by(SecurityEvent.created_at.desc()).limit(40))).scalars().all()
    actions = (await db.execute(select(PlatformAuditEntry).where(PlatformAuditEntry.target_type == "user", PlatformAuditEntry.target_id == user_id)
                                .order_by(PlatformAuditEntry.created_at.desc()).limit(15))).scalars().all()
    staff_row = await db.get(PlatformStaff, user_id)
    return {
        "person": {"id": user.id, "name": user.display_name, "email": user.email, "email_verified": user.email_verified, "status": user.status,
                   "created_at": user.created_at, "last_seen_at": user.last_seen_at, "timezone": user.timezone,
                   "staff_role": "owner" if user.id in settings.platform_admin_ids else (staff_row.role if staff_row else None)},
        "organizations": [{"id": t.id, "name": t.name, "status": t.status, "role": role} for t, role in orgs],
        "workspaces": [{"id": w.id, "name": w.name, "tenant_id": w.tenant_id, "role": role} for w, role in spaces],
        "browsers": [{"id": d.id, "label": d.label, "ip": d.ip, "last_seen_at": d.last_seen_at, "trusted_until": d.trusted_until} for d in devices],
        "security_events": [{"kind": e.kind, "summary": e.summary, "device": e.device, "ip": e.ip, "created_at": e.created_at} for e in events],
        "staff_actions": [_action(a) for a in actions],
    }


@router.post("/people/{user_id}/forget-browsers")
async def forget_browsers(user_id: str, body: Reason, staff: Staff = Depends(require_staff("users.manage", recent=True)),
                          db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    """Every browser they confirmed must be confirmed again with an emailed code: for a lost laptop or a shared password."""
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="No such account.")
    result = await db.execute(update(TrustedDevice).where(TrustedDevice.user_id == user_id, TrustedDevice.status == "trusted")
                              .values(status="revoked", code_hash=""))
    db.add(SecurityEvent(user_id=user_id, kind="devices_removed", summary="Syruss support removed every confirmed browser."))
    record(db, staff, "account.browsers_forgotten", "user", user_id, reason=body.reason, details={"browsers": result.rowcount, "email": user.email})
    await db.commit()
    return {"browsers_forgotten": result.rowcount}


# ---- the platform's switches and announcements ----------------------------------------------------

@router.get("/platform", dependencies=[VIEW])
async def platform(db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    values = await platform_controls.read(db)
    paused = values[platform_controls.SWITCHES].get("agent_paused_orgs") or []
    names = dict((await db.execute(select(Tenant.id, Tenant.name).where(Tenant.id.in_(paused)))).all()) if paused else {}
    now = _now()
    rows = (await db.execute(select(PlatformAnnouncement).order_by(PlatformAnnouncement.created_at.desc()).limit(50))).scalars().all()
    tenants = {r.tenant_id for r in rows if r.tenant_id}
    tnames = dict((await db.execute(select(Tenant.id, Tenant.name).where(Tenant.id.in_(tenants)))).all()) if tenants else {}
    return {
        "maintenance": values[platform_controls.MAINTENANCE],
        "switches": {k: v for k, v in values[platform_controls.SWITCHES].items() if k != "agent_paused_orgs"},
        "agent_paused_orgs": [{"id": t, "name": names.get(t, t)} for t in paused],
        "announcements": [_announcement(a, now, tnames.get(a.tenant_id)) for a in rows],
    }


class MaintenanceIn(Reason):
    on: bool
    message: str = Field("", max_length=500)


@router.put("/platform/maintenance")
async def set_maintenance(body: MaintenanceIn, staff: Staff = Depends(require_staff("system.manage", recent=True)),
                          db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    """Read-only mode for everyone: people can look, nothing changes, the agent and intake wait. Staff tools keep working."""
    value = {"on": body.on, "message": body.message.strip()}
    await platform_controls.save(db, platform_controls.MAINTENANCE, value, by_id=staff.user_id, by_name=staff.name)
    record(db, staff, "maintenance.on" if body.on else "maintenance.off", "platform", "maintenance", reason=body.reason, details=value)
    await db.commit()
    return value


class SwitchIn(Reason):
    name: Literal["signups", "agent", "intake"]
    on: bool


@router.put("/platform/switches")
async def set_switch(body: SwitchIn, staff: Staff = Depends(require_staff("system.manage", recent=True)),
                     db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    """Turns one part of the platform off or on for everyone: new sign-ups, the agent, or documents arriving from other systems."""
    values = (await platform_controls.read(db))[platform_controls.SWITCHES]
    values = {**values, body.name: body.on}
    await platform_controls.save(db, platform_controls.SWITCHES, values, by_id=staff.user_id, by_name=staff.name)
    record(db, staff, f"switch.{body.name}.{'on' if body.on else 'off'}", "platform", body.name, reason=body.reason)
    await db.commit()
    if body.name == "agent" and body.on:
        from app.services.agent_events import agent_event_relay
        agent_event_relay.notify()
    return {k: v for k, v in values.items() if k != "agent_paused_orgs"}


def _announcement(a: PlatformAnnouncement, now: datetime, organization: Optional[str] = None) -> Dict[str, Any]:
    live = a.starts_at <= now and (a.ends_at is None or a.ends_at > now)
    return {"id": a.id, "title": a.title, "body": a.body, "severity": a.severity, "tenant_id": a.tenant_id, "organization": organization,
            "starts_at": a.starts_at, "ends_at": a.ends_at, "created_by_name": a.created_by_name, "created_at": a.created_at,
            "state": "live" if live else "scheduled" if a.starts_at > now else "ended"}


class AnnouncementIn(BaseModel):
    title: str = Field(..., min_length=3, max_length=160)
    body: str = Field("", max_length=2000)
    severity: Literal["info", "warning", "critical"] = "info"
    tenant_id: Optional[str] = None
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    reason: str = Field(..., min_length=5, max_length=1000)


@router.post("/announcements", status_code=201)
async def announce(body: AnnouncementIn, staff: Staff = Depends(require_staff("system.manage", recent=True)),
                   db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    """A message shown at the top of the app: to everyone, or to one organization; now, or between two times."""
    now = _now()
    starts = body.starts_at or now
    if body.ends_at and body.ends_at <= starts:
        raise HTTPException(status_code=422, detail="It has to end after it starts.")
    organization = None
    if body.tenant_id:
        organization = (await _tenant(db, body.tenant_id)).name
    row = PlatformAnnouncement(title=body.title.strip(), body=body.body.strip(), severity=body.severity, tenant_id=body.tenant_id,
                               starts_at=starts, ends_at=body.ends_at, created_by=staff.user_id, created_by_name=staff.name)
    db.add(row)
    await db.flush()
    record(db, staff, "announcement.created", "announcement", row.id, tenant_id=body.tenant_id, reason=body.reason,
           details={"title": row.title, "severity": row.severity, "audience": organization or "everyone"})
    await db.commit()
    return _announcement(row, now, organization)


@router.post("/announcements/{announcement_id}/end")
async def end_announcement(announcement_id: str, staff: Staff = Depends(require_staff("system.manage")),
                           db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    row = await db.get(PlatformAnnouncement, announcement_id)
    if row is None:
        raise HTTPException(status_code=404, detail="No such announcement.")
    now = _now()
    if row.ends_at is None or row.ends_at > now:
        row.starts_at = min(row.starts_at, now)  # one that hadn't started yet is called off
        row.ends_at = now
        record(db, staff, "announcement.ended", "announcement", row.id, tenant_id=row.tenant_id, details={"title": row.title})
        await db.commit()
    return _announcement(row, now)


# ---- one search box -------------------------------------------------------------------------------

@router.get("/search", dependencies=[VIEW])
async def search(q: str = Query(..., min_length=2, max_length=120), db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    """Organizations and people by name, address or email; invoices by number; and documents, runs or anything else by id."""
    text = q.strip()
    like = "%" + text.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    orgs = (await db.execute(select(Tenant).where(or_(
        func.lower(Tenant.name).like(like, escape="\\"), func.lower(Tenant.slug).like(like, escape="\\"), Tenant.id == text))
        .order_by(Tenant.created_at.desc()).limit(8))).scalars().all()
    people = (await db.execute(select(User).where(or_(
        func.lower(User.email).like(like, escape="\\"), func.lower(User.first_name + " " + User.last_name).like(like, escape="\\"), User.id == text))
        .order_by(User.created_at.desc()).limit(8))).scalars().all()
    invoices = (await db.execute(select(Invoice).where(func.lower(Invoice.number).like(like, escape="\\")).order_by(Invoice.issued_at.desc()).limit(6))).scalars().all()
    document = await db.get(Document, text)
    run = (await db.execute(select(Run).where(or_(Run.id == text, Run.run_id == text, Run.event_id == text)).limit(1))).scalar_one_or_none()
    return {
        "organizations": [{"id": t.id, "name": t.name, "slug": t.slug, "status": t.status} for t in orgs],
        "people": [{"id": u.id, "name": u.display_name, "email": u.email, "status": u.status} for u in people],
        "invoices": [{"id": i.id, "number": i.number, "status": i.status, "tenant_id": i.tenant_id} for i in invoices],
        "documents": [{"id": document.id, "tenant_id": document.tenant_id, "status": document.status.value}] if document else [],
        "runs": [{"id": run.id, "tenant_id": run.tenant_id, "status": run.status}] if run else [],
    }


# ---- security -------------------------------------------------------------------------------------

@router.get("/security", dependencies=[VIEW])
async def security(db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    """
    Signs of trouble across the platform: accounts with bursts of wrong sign-in codes, new browsers added, staff access
    to customers' content now open, and a review of who is on the staff and when they last did anything.
    """
    now = _now()
    day = now - timedelta(days=1)
    bursts = (await db.execute(select(SecurityEvent.user_id, func.count(), func.max(SecurityEvent.created_at)).where(
        SecurityEvent.kind == "code_failed", SecurityEvent.created_at >= day).group_by(SecurityEvent.user_id)
        .having(func.count() >= 5).order_by(func.count().desc()).limit(20))).all()
    kinds = dict((await db.execute(select(SecurityEvent.kind, func.count()).where(SecurityEvent.created_at >= now - timedelta(days=7))
                                   .group_by(SecurityEvent.kind))).all())
    users = {u.id: u for u in (await db.execute(select(User).where(User.id.in_([b[0] for b in bursts])))).scalars().all()} if bursts else {}
    grants = (await db.execute(select(PlatformAccessGrant, Tenant.name).join(Tenant, Tenant.id == PlatformAccessGrant.tenant_id).where(
        PlatformAccessGrant.ended_at.is_(None), PlatformAccessGrant.expires_at > now).order_by(PlatformAccessGrant.expires_at))).all()
    staff_rows = (await db.execute(select(PlatformStaff, User).join(User, User.id == PlatformStaff.user_id))).all()
    staff = [(u, row.role) for row, u in staff_rows]
    configured = settings.platform_admin_ids - {u.id for u, _ in staff}
    if configured:
        staff += [(u, "owner") for u in (await db.execute(select(User).where(User.id.in_(configured)))).scalars().all()]
    last_action = dict((await db.execute(select(PlatformAuditEntry.actor_id, func.max(PlatformAuditEntry.created_at))
                                         .group_by(PlatformAuditEntry.actor_id))).all())
    return {
        "last_7_days": {"wrong_codes": kinds.get("code_failed", 0), "browsers_confirmed": kinds.get("device_trusted", 0),
                        "browsers_removed": kinds.get("device_removed", 0) + kinds.get("devices_removed", 0), "codes_sent": kinds.get("code_sent", 0)},
        "wrong_code_bursts": [{"user_id": uid, "name": users[uid].display_name if uid in users else uid, "email": users[uid].email if uid in users else None,
                               "count": n, "last_at": last} for uid, n, last in bursts],
        "open_access": [{"staff_name": g.staff_name, "organization": name, "tenant_id": g.tenant_id, "reason": g.reason, "expires_at": g.expires_at}
                        for g, name in grants],
        "staff_review": sorted([{"user_id": u.id, "name": u.display_name, "email": u.email, "role": role, "status": u.status,
                                 "last_seen_at": u.last_seen_at, "last_action_at": last_action.get(u.id),
                                 "stale": not u.last_seen_at or u.last_seen_at < now - timedelta(days=60)} for u, role in staff],
                               key=lambda s: (not s["stale"], s["name"])),
    }
