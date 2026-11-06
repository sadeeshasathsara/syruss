"""
The organization overview's numbers: how work went across workspaces over a period and the one before it, how each
workspace is doing, what it has used and what its agent cost. Counts, rates and costs only; document names appear
only in the latest work, and only from workspaces the person holds a role in.
"""
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence
from sqlalchemy import case, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.document import Document, DocumentRole, DocumentStatus
from app.models.extraction_log import ExtractionLogEntry
from app.models.review import ReviewTask
from app.models.trace import AgentExecutionTrace
from app.models.workspace_admin import WorkspaceSettings
from app.schemas.workspace import WorkspaceSummary
from app.services.settings_service import PLANS, WorkspaceSettingsData, month_start

PERIODS = (7, 30, 90)
OUTCOMES = ("saved", "review", "failed", "working")


def _midnight(day: date) -> datetime:
    return datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)


def _outcome(status: DocumentStatus) -> str:
    """Where a document stands now, in the four groups people read."""
    if status == DocumentStatus.COMPLETED:
        return "saved"
    if status == DocumentStatus.FAILED:
        return "failed"
    if status in (DocumentStatus.NEEDS_REVIEW, DocumentStatus.AWAITING_INPUT):
        return "review"
    return "working"


def _empty() -> Dict[str, Any]:
    return {"received": 0, **{k: 0 for k in OUTCOMES}, "hands_free": 0, "seconds": 0.0, **_no_runs()}


def _no_runs() -> Dict[str, Any]:
    return {"runs": 0, "priced": 0, "cost_usd": 0.0, "tokens": 0}


def _rate(part: float, whole: int, digits: int = 3) -> Optional[float]:
    return round(part / whole, digits) if whole else None


def _window(col, start: datetime, end: Optional[datetime]) -> tuple:
    return (col >= start, *((col < end,) if end else ()))


async def _runs(db: AsyncSession, ids: Sequence[str], start: datetime, end: Optional[datetime] = None) -> Dict[str, Dict[str, Any]]:
    """Per workspace: the agent's runs, how many had a price (a model without one records no cost), the cost and the tokens."""
    T = AgentExecutionTrace
    return {tid: {"runs": runs, "priced": priced, "cost_usd": float(cost or 0), "tokens": int(tokens or 0)} for tid, runs, priced, cost, tokens in (await db.execute(
        select(T.template_id, func.count(T.id), func.count(T.cost_usd), func.sum(T.cost_usd), func.sum(T.input_tokens + T.output_tokens))
        .where(T.template_id.in_(ids), *_window(T.started_at, start, end)).group_by(T.template_id)
    )).all()}


def _spend(r: Dict[str, Any], with_cost: bool) -> Dict[str, Any]:
    """What the agent cost: unknown (None) when it ran but no run had a price, so a missing price never reads as free."""
    if not with_cost:
        return {"cost_usd": None, "tokens": None}
    return {"cost_usd": round(r["cost_usd"], 4) if r["priced"] or not r["runs"] else None, "tokens": r["tokens"]}


async def _period(db: AsyncSession, ids: Sequence[str], start: datetime, end: Optional[datetime]) -> Dict[str, Dict[str, Any]]:
    """Per workspace: documents received between start and end by where they stand now, how the saved ones went, and runs."""
    out: Dict[str, Dict[str, Any]] = {i: _empty() for i in ids}
    real = (Document.template_id.in_(ids), Document.role == DocumentRole.DOCUMENT, *_window(Document.created_at, start, end))

    for tid, status, n in (await db.execute(
        select(Document.template_id, Document.status, func.count(Document.id)).where(*real).group_by(Document.template_id, Document.status)
    )).all():
        out[tid]["received"] += n
        out[tid][_outcome(status)] += n

    checked = exists().where(ReviewTask.document_id == Document.id)
    for tid, hands_free, seconds in (await db.execute(
        select(Document.template_id, func.count(case((~checked, 1))), func.sum(func.extract("epoch", Document.updated_at - Document.created_at)))
        .where(*real, Document.status == DocumentStatus.COMPLETED).group_by(Document.template_id)
    )).all():
        out[tid].update(hands_free=hands_free, seconds=float(seconds or 0))

    for tid, r in (await _runs(db, ids, start, end)).items():
        out[tid].update(r)
    return out


def _totals(rows: Sequence[Dict[str, Any]], with_cost: bool) -> Dict[str, Any]:
    """Adds workspaces up into the figures people read: rates from the sums, not averages of averages."""
    s = {k: sum(r[k] for r in rows) for k in _empty()}
    return {
        "received": s["received"], "saved": s["saved"], "review": s["review"], "failed": s["failed"], "working": s["working"],
        "hands_free_rate": _rate(s["hands_free"], s["saved"]),
        "avg_seconds_to_save": _rate(s["seconds"], s["saved"], 1),
        "failure_rate": _rate(s["failed"], s["received"]),
        "runs": s["runs"],
        **_spend(s, with_cost),
    }


async def _daily(db: AsyncSession, ids: Sequence[str], first: date, days: int) -> List[Dict[str, Any]]:
    day = func.date(Document.created_at)
    series = {first + timedelta(days=i): {k: 0 for k in OUTCOMES} for i in range(days)}
    for d, status, n in (await db.execute(
        select(day, Document.status, func.count(Document.id))
        .where(Document.template_id.in_(ids), Document.role == DocumentRole.DOCUMENT, Document.created_at >= _midnight(first))
        .group_by(day, Document.status)
    )).all():
        if d in series:
            series[d][_outcome(status)] += n
    return [{"date": d.isoformat(), **v, "received": sum(v.values())} for d, v in sorted(series.items())]


async def _this_month(db: AsyncSession, ids: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    """Documents counted against each workspace's plan this month (test documents aren't), and its agent's runs."""
    since = month_start()
    docs = dict((await db.execute(
        select(Document.template_id, func.count(Document.id)).where(
            Document.template_id.in_(ids), Document.role == DocumentRole.DOCUMENT, Document.is_test.is_(False), Document.created_at >= since)
        .group_by(Document.template_id))).all())
    runs = await _runs(db, ids, since)
    settings = {t: WorkspaceSettingsData.model_validate(d or {}) for t, d in (await db.execute(
        select(WorkspaceSettings.template_id, WorkspaceSettings.data).where(WorkspaceSettings.template_id.in_(ids)))).all()}
    out = {}
    for i in ids:
        s = settings.get(i) or WorkspaceSettingsData()
        plan = PLANS[s.plan]
        out[i] = {"documents": docs.get(i, 0), "documents_limit": plan["documents_per_month"], "plan": plan["name"],
                  "runs": runs.get(i) or _no_runs(), "budget_usd": s.budget.monthly_cost_usd}
    return out


async def _latest_work(db: AsyncSession, ids: Sequence[str], names: Dict[str, WorkspaceSummary], limit: int) -> List[Dict[str, Any]]:
    if not ids:
        return []
    rows = (await db.execute(
        select(ExtractionLogEntry).where(ExtractionLogEntry.template_id.in_(ids))
        .order_by(ExtractionLogEntry.created_at.desc()).limit(limit))).scalars().all()
    return [{
        "id": r.id, "workspace": names[r.template_id].name, "workspace_slug": names[r.template_id].slug, "document_id": r.document_id,
        "stage": r.stage, "level": r.level, "message": r.message, "created_at": r.created_at,
    } for r in rows]


async def build(db: AsyncSession, *, visible: Sequence[WorkspaceSummary], roles: Dict[str, str], days: int, with_cost: bool) -> Dict[str, Any]:
    """
    The period figures and the workspace table cover the workspaces the person can see (an organization admin sees all
    of them, as counts); what needs them and the latest work cover only the ones they hold a role in.
    """
    ids = [w.id for w in visible]
    today = datetime.now(timezone.utc).date()
    first = today - timedelta(days=days - 1)
    current = await _period(db, ids, _midnight(first), None) if ids else {}
    previous = await _period(db, ids, _midnight(first - timedelta(days=days)), _midnight(first)) if ids else {}
    month = await _this_month(db, ids) if ids else {}
    last = dict((await db.execute(
        select(Document.template_id, func.max(Document.updated_at)).where(Document.template_id.in_(ids), Document.role == DocumentRole.DOCUMENT)
        .group_by(Document.template_id))).all()) if ids else {}
    failed = dict((await db.execute(
        select(Document.template_id, func.count(Document.id)).where(Document.template_id.in_(ids), Document.status == DocumentStatus.FAILED)
        .group_by(Document.template_id))).all()) if ids else {}

    workspaces = []
    for w in visible:
        c, m = current[w.id], month[w.id]
        workspaces.append({
            "id": w.id, "slug": w.slug, "name": w.name, "status": w.status, "my_role": roles.get(w.id),
            "received": c["received"], "saved": c["saved"], "failed": c["failed"],
            "hands_free_rate": _rate(c["hands_free"], c["saved"]),
            "needs_review": w.needs_review, "open_questions": w.open_questions + w.pending_proposals, "open_threads": w.open_conversations,
            "failed_now": failed.get(w.id, 0), "last_activity_at": last.get(w.id),
            "documents_this_month": m["documents"], "documents_limit": m["documents_limit"], "plan": m["plan"],
            **_spend(c, with_cost), "cost_this_month_usd": _spend(m["runs"], with_cost)["cost_usd"],
            "budget_usd": m["budget_usd"] if with_cost else None,
        })

    mine = [w for w in workspaces if w["my_role"]]
    month_runs = {k: sum(m["runs"][k] for m in month.values()) for k in _no_runs()}
    return {
        "costs": with_cost,
        "period": {
            "days": days, "from": first.isoformat(), "to": today.isoformat(),
            "series": await _daily(db, ids, first, days),
            "current": _totals(list(current.values()), with_cost),
            "previous": _totals(list(previous.values()), with_cost),
        },
        "workspace_health": workspaces,
        "needs_you": [
            {"slug": w["slug"], "name": w["name"], "status": w["status"], "needs_review": w["needs_review"],
             "questions": w["open_questions"], "threads": w["open_threads"], "failed": w["failed_now"]}
            for w in mine if w["status"] == "draft" or w["needs_review"] or w["open_questions"] or w["open_threads"] or w["failed_now"]
        ],
        "this_month": {
            "documents": sum(m["documents"] for m in month.values()),
            **_spend(month_runs, with_cost),
            "near_limit": [w["name"] for w in workspaces if w["documents_limit"] and w["documents_this_month"] >= 0.8 * w["documents_limit"]],
        },
        "latest_work": await _latest_work(db, [w["id"] for w in mine], {w.id: w for w in visible}, 8),
    }
