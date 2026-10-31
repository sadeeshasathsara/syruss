"""
How well the agent is doing, from what really happened in the workspace.

Live quality is counted, never estimated:
  * the agent's decisions (from the audit log): records committed straight through vs sent to review;
  * people's review decisions: approved as the agent read it, corrected, or rejected;
  * which fields people correct most, with examples;
  * calibration: when the agent said it was N% sure and sent a document to review, how often did
    people have to change it;
  * identity signals the agent acknowledged when committing.
Shadow evaluations against golden documents are app.services.eval_service.
"""
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.audit import AuditLog
from app.models.evaluation import EvalRun
from app.models.review import ReviewStatus, ReviewTask
from app.services.benchmark_service import _flatten

BUCKETS = [(0.0, 0.5), (0.5, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.01)]


def field_of(path: str) -> str:
    """'Guard[3].name' -> 'Guard.name'."""
    return re.sub(r"\[\d+\]", "", path)


def corrections(original: Dict[str, Any], corrected: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every value a person changed, added or removed."""
    before, after = _flatten(original or {}), _flatten(corrected or {})
    out = []
    for path in sorted(set(before) | set(after)):
        if before.get(path) != after.get(path):
            out.append({"path": path, "field": field_of(path), "from": before.get(path), "to": after.get(path)})
    return out


def _rate(part: int, whole: int) -> Optional[float]:
    return round(part / whole, 4) if whole else None


async def live(db: AsyncSession, template_id: str, days: int = 30) -> Dict[str, Any]:
    since = datetime.now(timezone.utc) - timedelta(days=days)

    decisions = (await db.execute(select(AuditLog).where(
        AuditLog.template_id == template_id, AuditLog.actor == "agent", AuditLog.created_at >= since,
        AuditLog.event_type.in_(("RECORD_COMMITTED", "RECORD_SENT_TO_REVIEW")),
    ).order_by(AuditLog.created_at))).scalars().all()
    committed = sum(1 for d in decisions if d.event_type == "RECORD_COMMITTED")
    to_review = len(decisions) - committed
    acknowledged = sum(len((d.details or {}).get("acknowledged_signals") or []) for d in decisions)
    # The agent's confidence when it last sent each document to review
    review_confidence = {d.entity_id: (d.details or {}).get("confidence")
                         for d in decisions if d.event_type == "RECORD_SENT_TO_REVIEW"}

    reviews = (await db.execute(select(ReviewTask).where(
        ReviewTask.template_id == template_id, ReviewTask.status != ReviewStatus.PENDING, ReviewTask.decided_at >= since,
    ))).scalars().all()
    outcome = Counter()
    fields = Counter()
    examples: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    buckets = {b: {"reviewed": 0, "changed": 0} for b in BUCKETS}
    for r in reviews:
        changes = corrections(r.original_data, r.corrected_data) if r.corrected_data else []
        changed = bool(changes) or r.status == ReviewStatus.REJECTED
        outcome["rejected" if r.status == ReviewStatus.REJECTED else "corrected" if changes else "approved"] += 1
        for field in {c["field"] for c in changes}:
            fields[field] += 1
        for c in changes:
            if len(examples[c["field"]]) < 3:
                examples[c["field"]].append({"from": c["from"], "to": c["to"]})
        confidence = review_confidence.get(r.document_id)
        if isinstance(confidence, (int, float)):
            for b in BUCKETS:
                if b[0] <= confidence < b[1]:
                    buckets[b]["reviewed"] += 1
                    buckets[b]["changed"] += int(changed)

    last_eval = (await db.execute(select(EvalRun).where(
        EvalRun.template_id == template_id, EvalRun.status == "COMPLETED",
    ).order_by(EvalRun.completed_at.desc()).limit(1))).scalar_one_or_none()

    decided = len(reviews)
    return {
        "days": days,
        "since": since,
        "agent": {
            "decisions": len(decisions), "committed": committed, "sent_to_review": to_review,
            "straight_through_rate": _rate(committed, len(decisions)),
            "acknowledged_signals": acknowledged,
        },
        "reviews": {
            "decided": decided, "approved": outcome["approved"], "corrected": outcome["corrected"],
            "rejected": outcome["rejected"], "correction_rate": _rate(outcome["corrected"] + outcome["rejected"], decided),
        },
        "fields": [
            {"field": f, "corrections": n, "share_of_reviews": _rate(n, decided), "examples": examples[f]}
            for f, n in fields.most_common(15)
        ],
        "calibration": [
            {"confidence_from": lo, "confidence_to": min(hi, 1.0), "reviewed": v["reviewed"],
             "changed_by_people": v["changed"], "change_rate": _rate(v["changed"], v["reviewed"])}
            for (lo, hi), v in buckets.items()
        ],
        "last_evaluation": {
            "id": last_eval.id, "completed_at": last_eval.completed_at, "metrics": {
                k: last_eval.metrics.get(k) for k in ("field_accuracy", "exact_match_rate", "confidently_wrong_rate", "answered")
            },
        } if last_eval else None,
    }
