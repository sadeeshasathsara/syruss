"""
The agent's monthly budget for a workspace.

Admins can cap what Syruss uses in a calendar month (UTC), in money (needs model prices:
MODEL_PRICES in the agent's environment) and/or in tokens. The month's spend is counted from
the run log, so it is exact to the last finished run.

  * at 80% and at 100% the people who manage the workspace are notified, once a month each;
  * when the budget is reached and set to "pause", the workspace's agent events wait in the
    outbox (nothing is lost) until the budget is raised or the month turns, and chat messages
    get an immediate notice instead of a reply. A run already in progress finishes.
"""
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.jobs import WorkspaceJob
from app.services import settings_service, workspace_events

WARN_AT = 0.8
CACHE_SECONDS = 30.0
PAUSED_REPLY = ("I'm paused: this workspace has used its agent budget for the month, so I can't work on this now. "
                "An admin can raise the budget in Settings, Agent, Budget; otherwise I'll pick work up again on {resets}.")

_paused_cache: Dict[str, Tuple[float, bool]] = {}


def _next_month(start: datetime) -> datetime:
    return start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)


async def status(db: AsyncSession, template_id: str) -> Dict[str, Any]:
    prefs = (await settings_service.get_settings(db, template_id)).budget
    spend = await settings_service.agent_spend(db, template_id)
    cost = spend["agent_cost_usd_this_month"] or 0.0
    tokens = spend["agent_input_tokens_this_month"] + spend["agent_output_tokens_this_month"]
    shares = []
    if prefs.monthly_cost_usd:
        shares.append(cost / prefs.monthly_cost_usd)
    if prefs.monthly_tokens:
        shares.append(tokens / prefs.monthly_tokens)
    share = max(shares) if shares else None
    state = "unlimited" if share is None else "reached" if share >= 1 else "warning" if share >= WARN_AT else "ok"
    start = settings_service.month_start()
    return {
        "limits": prefs.model_dump(),
        "used": {"cost_usd": round(cost, 4), "tokens": tokens, "runs_without_price": spend["agent_runs_without_price"]},
        "share": round(share, 4) if share is not None else None,
        "state": state,
        "paused": state == "reached" and prefs.when_reached == "pause",
        "period_start": start,
        "resets_at": _next_month(start),
    }


async def is_paused(db: AsyncSession, template_id: str) -> bool:
    """Cached briefly: the outbox relay asks every second."""
    hit = _paused_cache.get(template_id)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    paused = (await status(db, template_id))["paused"]
    _paused_cache[template_id] = (time.monotonic(), paused)
    return paused


def forget(template_id: str) -> None:
    """The budget changed: decide afresh (in this process; others within CACHE_SECONDS)."""
    _paused_cache.pop(template_id, None)


async def paused_reply(db: AsyncSession, template_id: str) -> Optional[str]:
    current = await status(db, template_id)
    if not current["paused"]:
        return None
    return PAUSED_REPLY.format(resets=current["resets_at"].strftime("%d %B"))


async def check_alerts(db: AsyncSession, *, tenant_id: str, template_id: str) -> None:
    """After each run: tell the admins when the month's spend crosses 80% or 100%, once each."""
    current = await status(db, template_id)
    if current["state"] not in ("warning", "reached"):
        return
    period = current["period_start"].strftime("%Y-%m")
    job = await db.get(WorkspaceJob, (template_id, "budget"))
    if job is None:
        job = WorkspaceJob(template_id=template_id, job="budget", tenant_id=tenant_id, state={})
        db.add(job)
    state = job.state if (job.state or {}).get("period") == period else {"period": period, "alerted": []}
    if current["state"] in state["alerted"]:
        return
    limits = current["limits"]
    used = current["used"]
    spent = ", ".join(filter(None, [
        f"${used['cost_usd']:.2f} of ${limits['monthly_cost_usd']:.2f}" if limits["monthly_cost_usd"] else None,
        f"{used['tokens']:,} of {limits['monthly_tokens']:,} tokens" if limits["monthly_tokens"] else None,
    ]))
    if current["state"] == "reached":
        title = "Syruss has used this month's budget"
        body = (f"Used {spent}. " + ("Its work is paused until the budget is raised or the month turns."
                                     if current["paused"] else "It keeps working; the budget is set to warn only."))
    else:
        title = f"Syruss has used {current['share']:.0%} of this month's budget"
        body = f"Used {spent}."
    await workspace_events.emit(
        db, tenant_id=tenant_id, template_id=template_id, type="agent.budget",
        severity="error" if current["state"] == "reached" else "warning", title=title, body=body,
        path="settings/agent/budget", data={"state": current["state"], "share": current["share"]},
    )
    job.state = {**state, "alerted": [*state["alerted"], current["state"]]}
    job.last_run_at = datetime.now(timezone.utc)
    job.last_status = current["state"]
