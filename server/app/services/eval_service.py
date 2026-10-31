"""
Shadow evaluations: how well the agent reads, measured against records people verified.

A golden document is a workspace document whose committed record a person stands behind; that
record is the expected answer. An evaluation run wakes the agent once per golden document
(`eval.item` events). The agent reads it as if it had just arrived, with today's template,
memory and knowledge, and submits a record as usual. For these runs the Agent Tool API
diverts the submission here instead of storing it, hides the expected answer from every read,
and refuses anything that would change the workspace. So an evaluation can never alter data.

Scores per document compare the agent's record with the expected one field by field. Across the
run they add up to: field accuracy, exact matches, how often it would have committed straight
through, and the number that matters most for trust, how often it would have committed a wrong
record ("confidently wrong").
"""
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.document import Document, DocumentRole, DocumentStatus
from app.models.evaluation import EvalRun, EvalRunItem
from app.models.extraction import ExtractionResult
from app.models.review import ReviewStatus, ReviewTask
from app.models.template import DocumentTemplate
from app.models.template_enterprise import DocumentTemplateVersion, GoldenDatasetItem
from app.services import workspace_events
from app.services.agent_events import enqueue_agent_event
from app.services.benchmark_service import compare
from app.services.quality_service import field_of


# ---------------------------------------------------------------------------
# Golden documents
# ---------------------------------------------------------------------------

async def golden_list(db: AsyncSession, template_id: str) -> List[Dict[str, Any]]:
    rows = (await db.execute(
        select(GoldenDatasetItem, Document, ReviewTask.status)
        .outerjoin(Document, Document.id == GoldenDatasetItem.document_id)
        .outerjoin(ReviewTask, ReviewTask.document_id == GoldenDatasetItem.document_id)
        .where(GoldenDatasetItem.template_id == template_id).order_by(GoldenDatasetItem.created_at.desc())
    )).all()
    return [{
        "id": g.id, "name": g.name, "document_id": g.document_id, "filename": d.original_filename if d else None,
        "complexity_tag": g.complexity_tag, "created_at": g.created_at, "created_by": g.created_by,
        "verified_in_review": status in (ReviewStatus.APPROVED, ReviewStatus.CORRECTED),
        "rows": sum(len(v) if isinstance(v, list) else 1 for v in (g.ground_truth_json or {}).values()),
        "can_run": bool(d),
    } for g, d, status in rows]


async def add_golden(db: AsyncSession, *, template: DocumentTemplate, document_id: str, user_id: str,
                     name: Optional[str] = None, complexity_tag: str = "standard") -> GoldenDatasetItem:
    doc = await db.get(Document, document_id)
    if not doc or doc.template_id != template.id or doc.role != DocumentRole.DOCUMENT:
        raise HTTPException(status_code=404, detail="Document not found in this workspace.")
    if doc.status != DocumentStatus.COMPLETED:
        raise HTTPException(status_code=409, detail="Only documents with a saved record can be golden: its record is the expected answer.")
    result = (await db.execute(select(ExtractionResult).where(ExtractionResult.document_id == doc.id))).scalar_one_or_none()
    if not result or not result.extracted_data:
        raise HTTPException(status_code=409, detail="This document has no record yet.")
    if (await db.execute(select(GoldenDatasetItem.id).where(GoldenDatasetItem.document_id == doc.id))).first():
        raise HTTPException(status_code=409, detail=f"{doc.original_filename} is already a golden document.")
    item = GoldenDatasetItem(
        template_id=template.id, tenant_id=template.tenant_id, name=name or doc.original_filename,
        storage_path=doc.minio_object_key, ground_truth_json=result.extracted_data, complexity_tag=complexity_tag,
        document_id=doc.id, created_by=user_id,
    )
    db.add(item)
    await db.flush()
    return item


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------

async def start(db: AsyncSession, *, template: DocumentTemplate, user_id: str, user_name: str) -> EvalRun:
    if not template.active_version_id:
        raise HTTPException(status_code=409, detail="Publish the template first.")
    running = (await db.execute(select(EvalRun.id).where(
        EvalRun.template_id == template.id, EvalRun.status == "RUNNING"))).first()
    if running:
        raise HTTPException(status_code=409, detail="An evaluation is already running. Wait for it or cancel it.")
    golden = (await db.execute(select(GoldenDatasetItem).where(
        GoldenDatasetItem.template_id == template.id, GoldenDatasetItem.document_id.is_not(None),
    ).order_by(GoldenDatasetItem.created_at))).scalars().all()
    if not golden:
        raise HTTPException(status_code=409, detail="Add golden documents first: records people verified.")
    run = EvalRun(tenant_id=template.tenant_id, template_id=template.id, version_id=template.active_version_id,
                  item_count=len(golden), requested_by=user_id, requested_by_name=user_name)
    db.add(run)
    await db.flush()
    for g in golden:
        item = EvalRunItem(run_id=run.id, golden_item_id=g.id, document_id=g.document_id, name=g.name,
                           expected=g.ground_truth_json)
        db.add(item)
        await db.flush()
        event = enqueue_agent_event(
            db, event_type="eval.item", tenant_id=template.tenant_id, template_id=template.id,
            payload={"eval_run_id": run.id, "item_id": item.id, "document_id": g.document_id, "name": g.name,
                     "channel_id": f"template:{template.id}"},
            actor_id=user_id, actor_type="user", actor_name=user_name, source="dashboard",
        )
        await db.flush()
        item.event_id = event.id
    return run


async def item_for_event(db: AsyncSession, event_id: Optional[str]) -> Optional[EvalRunItem]:
    if not event_id:
        return None
    return (await db.execute(select(EvalRunItem).where(EvalRunItem.event_id == event_id))).scalar_one_or_none()


async def run_documents(db: AsyncSession, run_id: str) -> Set[str]:
    """Documents under evaluation in a run: their records are the answers, so reads hide them."""
    return set((await db.execute(select(EvalRunItem.document_id).where(EvalRunItem.run_id == run_id))).scalars().all())


def _score(item: EvalRunItem) -> Dict[str, Any]:
    metrics = compare(item.expected or {}, item.answer or {})
    rules_ok = bool((item.scores or {}).get("valid"))
    return {**metrics, "valid": rules_ok,
            "would_commit": item.decision == "COMMIT" and rules_ok,
            "errors": (item.scores or {}).get("errors", [])}


async def record_answer(db: AsyncSession, item: EvalRunItem, *, data: Dict[str, Any], decision: str,
                        confidence: float, validation: Dict[str, Any], notes: Optional[str] = None) -> Dict[str, Any]:
    if item.status != "QUEUED":
        raise HTTPException(status_code=409, detail="This evaluation document already has an answer.")
    item.answer, item.decision, item.confidence, item.note = data, decision.upper(), confidence, notes
    item.scores = {"valid": bool(validation.get("valid")), "errors": validation.get("errors", [])[:20]}
    item.scores = _score(item)
    item.status, item.completed_at = "ANSWERED", datetime.now(timezone.utc)
    await maybe_finish(db, item.run_id)
    return {"status": "EVALUATED", "validation": validation,
            "note": "Evaluation run: your record was scored, not stored. Nothing in the workspace changed."}


async def record_outcome(db: AsyncSession, item: EvalRunItem, status: str, note: Optional[str]) -> None:
    """The agent marked the document unreadable (FAILED), or its run ended without an answer (NO_ANSWER)."""
    if item.status != "QUEUED":
        return
    item.status, item.note, item.completed_at = status, (note or "")[:2000], datetime.now(timezone.utc)
    item.scores = {"f1_score": 0.0, "precision": 0.0, "recall": 0.0, "exact_match": False, "would_commit": False}
    await maybe_finish(db, item.run_id)


def summarize(items: List[EvalRunItem]) -> Dict[str, Any]:
    n = len(items)
    answered = [i for i in items if i.status == "ANSWERED"]
    exact = sum(1 for i in answered if i.scores.get("exact_match"))
    would = [i for i in answered if i.scores.get("would_commit")]
    wrong = [i for i in would if not i.scores.get("exact_match")]
    misses = Counter()
    for i in answered:
        for path in set(i.scores.get("wrong_fields", [])) | set(i.scores.get("missing_fields", [])):
            misses[field_of(path)] += 1

    def mean(key: str) -> Optional[float]:
        return round(sum(i.scores.get(key, 0.0) for i in answered) / len(answered), 4) if answered else None

    confidences = [i.confidence for i in answered if i.confidence is not None]
    return {
        "items": n, "answered": len(answered),
        "failed": sum(1 for i in items if i.status == "FAILED"),
        "no_answer": sum(1 for i in items if i.status == "NO_ANSWER"),
        "field_accuracy": mean("recall"), "precision": mean("precision"), "f1_score": mean("f1_score"),
        "average_cer": mean("cer"),
        "exact_match_rate": round(exact / n, 4) if n else None,
        "straight_through_rate": round(len(would) / n, 4) if n else None,
        "confidently_wrong": len(wrong),
        "confidently_wrong_rate": round(len(wrong) / n, 4) if n else None,
        "rules_pass_rate": round(sum(1 for i in answered if i.scores.get("valid")) / len(answered), 4) if answered else None,
        "average_confidence": round(sum(confidences) / len(confidences), 4) if confidences else None,
        "most_missed_fields": [{"field": f, "documents": c} for f, c in misses.most_common(10)],
    }


async def maybe_finish(db: AsyncSession, run_id: str) -> Optional[EvalRun]:
    run = await db.get(EvalRun, run_id, with_for_update=True)
    if not run or run.status != "RUNNING":
        return None
    items = (await db.execute(select(EvalRunItem).where(EvalRunItem.run_id == run_id))).scalars().all()
    run.metrics = summarize(items)
    if any(i.status == "QUEUED" for i in items):
        return None
    run.status, run.completed_at = "COMPLETED", datetime.now(timezone.utc)
    if run.version_id:
        version = await db.get(DocumentTemplateVersion, run.version_id)
        if version:
            version.benchmark_score = {"eval_run_id": run.id, **{k: run.metrics[k] for k in (
                "field_accuracy", "exact_match_rate", "confidently_wrong_rate", "items")}}
    m = run.metrics
    accuracy = f"{m['field_accuracy']:.0%} of fields right" if m["field_accuracy"] is not None else "no answers"
    await workspace_events.emit(
        db, tenant_id=run.tenant_id, template_id=run.template_id, type="agent.evaluation",
        severity="warning" if m["confidently_wrong"] else "success",
        title="Evaluation finished",
        body=f"{accuracy} across {m['items']} golden documents; {m['confidently_wrong']} would have been committed wrong.",
        path="template?tab=quality", data={"eval_run_id": run.id},
    )
    return run


async def cancel(db: AsyncSession, run: EvalRun) -> None:
    if run.status != "RUNNING":
        raise HTTPException(status_code=409, detail="This evaluation isn't running.")
    for item in (await db.execute(select(EvalRunItem).where(EvalRunItem.run_id == run.id, EvalRunItem.status == "QUEUED"))).scalars().all():
        item.status, item.note, item.completed_at = "NO_ANSWER", "Cancelled", datetime.now(timezone.utc)
    items = (await db.execute(select(EvalRunItem).where(EvalRunItem.run_id == run.id))).scalars().all()
    run.metrics = summarize(items)
    run.status, run.completed_at = "CANCELLED", datetime.now(timezone.utc)


def run_dict(run: EvalRun, items: Optional[List[EvalRunItem]] = None) -> Dict[str, Any]:
    out = {
        "id": run.id, "status": run.status, "version_id": run.version_id, "item_count": run.item_count,
        "metrics": run.metrics, "requested_by": run.requested_by_name or run.requested_by,
        "created_at": run.created_at, "completed_at": run.completed_at,
    }
    if items is not None:
        out["items"] = [{
            "id": i.id, "name": i.name, "document_id": i.document_id, "status": i.status, "decision": i.decision,
            "confidence": i.confidence, "note": i.note, "scores": i.scores, "expected": i.expected, "answer": i.answer,
            "completed_at": i.completed_at,
        } for i in items]
    return out
