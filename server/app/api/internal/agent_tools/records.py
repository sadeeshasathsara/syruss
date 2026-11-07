"""
Records: committing what was read, checking a draft, querying and searching records, reviews and quality.
"""
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, cast, String
from app.models.document import Document, DocumentStatus, DocumentRole
from app.models.extraction import ExtractionResult
from app.models.knowledge import Entity
from app.models.question import AgentQuestion, AgentQuestionStatus
from app.models.review import ReviewTask, ReviewStatus
from app.services import (
    duplicate_check, eval_service, extraction_log, graph_service, quality_service, record_query, settings_service,
    workspace_events,
)
from app.services.document_key import label_for
from app.services.identity_check import normalize_id
from app.services.record_service import record_service
from app.services.rules import service as rule_service
from app.services.rules.engine import normalize_schema, validate_record
from app.services.live.ui_events import publish_ui_event
from app.api.internal.agent_tools.scope import Workspace, get_workspace
from app.api.internal.agent_tools.common import document_in


router = APIRouter()


class RecordSubmission(BaseModel):
    data: Dict[str, Any] = Field(..., description="Records keyed by object type")
    decision: str = Field(..., description="COMMIT or REVIEW")
    confidence: float = Field(..., ge=0, le=1)
    evidence: List[Dict[str, Any]] = Field(default_factory=list)
    notes: Optional[str] = None
    review_reason: Optional[str] = None
    uncertain_fields: List[Dict[str, Any]] = Field(default_factory=list)
    acknowledged_signals: List[Dict[str, Any]] = Field(
        default_factory=list, description="[{id, reason}]: identity signals you checked on the page and stand by")


async def _questions_text(ws: Workspace, status: AgentQuestionStatus) -> str:
    """Everything said in flags with this status: their question, context and answer."""
    rows = (await ws.db.execute(select(AgentQuestion.question, AgentQuestion.context, AgentQuestion.answer).where(
        AgentQuestion.template_id == ws.template_id, AgentQuestion.status == status))).all()
    return " ".join(" ".join(filter(None, r)) for r in rows)


async def _unknown_identities(ws: Workspace, data: Dict[str, Any]) -> List[Dict[str, str]]:
    """People and things a record names that the workspace doesn't know yet (no entity with their ID)."""
    objects = normalize_schema(ws.template.schema_definition)
    found: Dict[str, Dict[str, str]] = {}
    for obj_type in objects:
        for row in graph_service.rows_of(data, obj_type):
            for thing, _ in graph_service.things_in_row(objects, data, obj_type, row):
                if thing.key not in found or (thing.label and not found[thing.key]["name"]):
                    found[thing.key] = {"type": thing.type, "id": thing.key.split(":", 1)[1], "name": thing.label}
    if not found:
        return []
    known = {f"{t}:{normalize_id(v)}" for t, v in (await ws.db.execute(
        select(Entity.entity_type, Entity.identifier_value).where(Entity.template_id == ws.template_id)
    )).all() if v}
    return [t for key, t in found.items() if key not in known]


def _listed(things: List[Dict[str, str]]) -> str:
    return "; ".join(f"{t['type']} {t['id']}" + (f" ({t['name']})" if t.get("name") else "") for t in things)


async def _refuse_unapproved_identities(ws: Workspace, doc: Document, data: Dict[str, Any]) -> None:
    """
    Registration off: a record naming people or things the workspace doesn't know can't be committed
    until a person has decided about them (a flag they answered that names them). The refusal says
    what to do, so the agent asks; it learns from the refusal rather than being nudged.
    """
    unknown = await _unknown_identities(ws, data)
    if not unknown:
        return
    decided = await _questions_text(ws, AgentQuestionStatus.ANSWERED)
    waiting = [t for t in unknown if t["id"] not in decided]
    if not waiting:
        return
    pending = (await ws.db.execute(select(AgentQuestion).where(
        AgentQuestion.template_id == ws.template_id, AgentQuestion.status == AgentQuestionStatus.OPEN,
    ).order_by(AgentQuestion.created_at))).scalars().all()
    asked = next((q for q in pending if any(t["id"] in f"{q.question} {q.context}" for t in waiting)), None)
    how = (f'You already asked: "{asked.question}". Park this document on that question: flag exactly the same '
           f'question again with waiting_document_ids ["{doc.id}"] (no duplicate is created).') if asked else (
        "Ask a person: flag ONE single-choice question listing them (type, ID, name) and asking whether to register "
        "them, or open a conversation if it needs discussion, with this document in waiting_document_ids. When they "
        "answer, register what they approved (create_entity with source_question_id) and commit.")
    raise HTTPException(status_code=422, detail={
        "message": "Record refused: it names people or things this workspace doesn't know, and its settings don't let "
                   "you register them on your own.",
        "errors": [{"code": "unregistered_identity", "path": "record",
                    "message": f"Not known yet: {_listed(waiting)}. {how}"}],
        "unregistered": waiting,
    })


async def _new_identities(ws: Workspace, data: Dict[str, Any]) -> List[Dict[str, str]]:
    """Registration on: who and what a committed record names that isn't registered yet."""
    return await _unknown_identities(ws, data)


@router.post("/documents/{document_id}/record")
async def submit_record(document_id: str, body: RecordSubmission, ws: Workspace = Depends(get_workspace)):
    doc = await document_in(ws, document_id)
    if doc.role != DocumentRole.DOCUMENT:
        raise HTTPException(status_code=400, detail="Samples are not processed into records.")
    threshold = (await settings_service.get_settings(ws.db, ws.template_id)).documents.commit_threshold
    if ws.eval_item:
        return await _evaluate_record(ws, doc, body, threshold)
    if body.decision.upper() == "COMMIT" and body.confidence < threshold:
        raise HTTPException(status_code=422, detail={"message": "Workspace policy", "problems": [
            f"This workspace commits only records the agent is at least {threshold:.0%} sure of "
            f"(you said {body.confidence:.0%}). Send it to review with a specific reason instead."]})
    may_register = (await settings_service.get_settings(ws.db, ws.template_id)).agent.may_register_entities
    if body.decision.upper() == "COMMIT" and not may_register:
        await _refuse_unapproved_identities(ws, doc, body.data)
    outcome = await record_service.submit(
        ws.db, template=ws.template, document=doc, data=body.data, decision=body.decision,
        confidence=body.confidence, evidence=body.evidence, notes=body.notes,
        review_reason=body.review_reason, uncertain_fields=body.uncertain_fields,
        acknowledged_signals=body.acknowledged_signals,
    )
    name = label_for(doc, ws.template.schema_definition, body.data)
    if outcome["status"] == "NEEDS_REVIEW":
        await workspace_events.emit(
            ws.db, tenant_id=ws.tenant_id, template_id=ws.template_id, type="document.needs_review", severity="warning",
            title=f"{name} needs review", body=body.review_reason or "", path=f"documents/{doc.id}",
            data={"document_id": doc.id},
        )
    elif outcome["status"] == "COMPLETED":
        await workspace_events.emit(
            ws.db, tenant_id=ws.tenant_id, template_id=ws.template_id, type="document.completed", severity="success",
            title=f"{name} was read and saved", path=f"documents/{doc.id}",
            data={"document_id": doc.id, "confidence": body.confidence},
        )
    await ws.db.commit()
    await workspace_events.dispatch(ws.db)
    await publish_ui_event(doc.id, "agent_completed", {
        "status": outcome["status"], "confidence": body.confidence, "reason": body.review_reason,
    }, tenant_id=ws.tenant_id, template_id=ws.template_id)
    if outcome["status"] == "NEEDS_REVIEW":
        await extraction_log.record(
            tenant_id=ws.tenant_id, template_id=ws.template_id, stage="review", level="warning", actor="Agent", document_id=doc.id,
            document_name=name, message=f"Sent to review. {body.review_reason or ''}".strip(),
            details={"confidence": body.confidence}, event_id=ws.event_id)
    elif outcome["status"] == "COMPLETED":
        await extraction_log.record(
            tenant_id=ws.tenant_id, template_id=ws.template_id, stage="saved", level="success", actor="Agent", document_id=doc.id,
            document_name=name, message="Read and saved.", details={"confidence": body.confidence}, event_id=ws.event_id)
    if outcome["status"] == "COMPLETED" and may_register:
        new = await _new_identities(ws, body.data)
        if new:
            outcome["new_identities"] = new
            outcome["registration"] = "you_may_register"
            outcome["next"] = ("These aren't registered yet. Register each one you are sure of with create_entity "
                               f"(source_document_id {doc.id}) so you recognize them on later documents.")
    return outcome


async def _evaluate_record(ws: Workspace, doc: Document, body: RecordSubmission, threshold: float) -> Dict[str, Any]:
    """The same checks a real commit gets, then the record is scored instead of stored."""
    if doc.id != ws.eval_item.document_id:
        raise HTTPException(status_code=403, detail="Not during an evaluation: only the document being evaluated.")
    decision = body.decision.upper()
    if decision not in ("COMMIT", "REVIEW"):
        raise HTTPException(status_code=400, detail="decision must be COMMIT or REVIEW")
    validation = validate_record(ws.template.schema_definition, body.data, await rule_service.active_rules(ws.db, ws.template_id))
    signals = await graph_service.identity_signals(
        ws.db, template_id=ws.template_id, schema=ws.template.schema_definition, data=body.data, document_id=doc.id,
    ) + await duplicate_check.duplicate_signals(
        ws.db, template_id=ws.template_id, schema=ws.template.schema_definition, data=body.data, document=doc)
    acknowledged = {a.get("id") for a in body.acknowledged_signals if isinstance(a, dict) and str(a.get("reason") or "").strip()}
    blocking = [s for s in signals if s["blocking"] and s["id"] not in acknowledged]
    if decision == "COMMIT" and body.confidence < threshold:
        validation = {**validation, "valid": False, "errors": validation["errors"] + [
            {"code": "below_commit_threshold", "path": "", "message": f"Confidence {body.confidence:.0%} is under {threshold:.0%}."}]}
    if blocking:
        validation = {**validation, "valid": False, "errors": validation["errors"] + [
            {"code": s["code"], "path": s["path"], "message": s["message"]} for s in blocking]}
    outcome = await eval_service.record_answer(
        ws.db, ws.eval_item, data=body.data, decision=decision, confidence=body.confidence,
        validation={**validation, "signals": signals}, notes=body.notes,
    )
    await ws.db.commit()
    await workspace_events.dispatch(ws.db)
    return outcome


class EvalOutcome(BaseModel):
    note: Optional[str] = None


@router.post("/evals/outcome")
async def evaluation_outcome(body: EvalOutcome, ws: Workspace = Depends(get_workspace)):
    """Closes this run's evaluation document when no record was submitted (called by the runtime)."""
    if not ws.eval_item:
        raise HTTPException(status_code=400, detail="This run isn't an evaluation.")
    await eval_service.record_outcome(ws.db, ws.eval_item, "NO_ANSWER", body.note)
    await ws.db.commit()
    await workspace_events.dispatch(ws.db)
    return {"status": ws.eval_item.status}


@router.get("/quality")
async def workspace_quality(days: int = Query(30, ge=1, le=365), ws: Workspace = Depends(get_workspace)):
    """How the agent has been doing: its decisions, people's corrections by field, and calibration."""
    return await quality_service.live(ws.db, ws.template_id, days)


class RecordDraft(BaseModel):
    data: Dict[str, Any]
    document_id: Optional[str] = Field(None, description="The document the draft is for (its earlier record is then not counted)")
    schema_definition: Optional[Dict[str, Any]] = Field(None, description="Validate against this schema instead of the active one")


@router.post("/records/validate")
async def validate_draft_record(body: RecordDraft, ws: Workspace = Depends(get_workspace)):
    """Dry run: checks a draft against the active (or a proposed) template without storing anything.
    Against the active template it also returns the workspace graph's identity signals."""
    rules = [] if body.schema_definition else await rule_service.active_rules(ws.db, ws.template_id)
    result = validate_record(body.schema_definition or ws.template.schema_definition, body.data, rules)
    if not body.schema_definition:
        document = await document_in(ws, body.document_id) if body.document_id else None
        result["signals"] = await graph_service.identity_signals(
            ws.db, template_id=ws.template_id, schema=ws.template.schema_definition, data=body.data,
            document_id=body.document_id or "",
        ) + await duplicate_check.duplicate_signals(
            ws.db, template_id=ws.template_id, schema=ws.template.schema_definition, data=body.data, document=document,
        )
    return result


@router.post("/records/query")
async def query_records(body: record_query.RecordQuery, ws: Workspace = Depends(get_workspace)):
    """Rows of one template object across every saved record, filtered and grouped in one call
    (e.g. the unique names on rows that match a condition). The answer to list, count and filter questions."""
    return await record_query.query(ws.db, template_id=ws.template_id, schema=ws.template.schema_definition,
                                    q=body, hidden=ws.hidden)


@router.get("/records/search")
async def search_records(
    q: str = Query(..., min_length=1, description="Text to look for in committed records"),
    limit: int = Query(10, le=50),
    ws: Workspace = Depends(get_workspace),
):
    """Finds past records mentioning a value: how a name was written before, who usually appears, etc."""
    stmt = (
        select(ExtractionResult, Document)
        .join(Document, Document.id == ExtractionResult.document_id)
        .where(
            ExtractionResult.template_id == ws.template_id,
            Document.status == DocumentStatus.COMPLETED,
            Document.id.not_in(ws.hidden),
            cast(ExtractionResult.extracted_data, String).ilike(f"%{q}%"),
        )
        .order_by(ExtractionResult.updated_at.desc())
        .limit(limit)
    )
    rows = (await ws.db.execute(stmt)).all()
    return [{
        "document_id": doc.id, "filename": doc.original_filename,
        "date": ext.updated_at.isoformat(), "data": ext.extracted_data,
    } for ext, doc in rows]


@router.get("/reviews/history")
async def review_history(limit: int = Query(10, le=50), ws: Workspace = Depends(get_workspace)):
    """Recent human review decisions in this workspace: what people corrected and why."""
    rows = (await ws.db.execute(
        select(ReviewTask).where(ReviewTask.template_id == ws.template_id, ReviewTask.status != ReviewStatus.PENDING,
                                 ReviewTask.document_id.not_in(ws.hidden))
        .order_by(ReviewTask.decided_at.desc()).limit(limit)
    )).scalars().all()
    return [{
        "review_id": r.id, "document_id": r.document_id, "status": r.status.value, "reason": r.reason,
        "original_data": r.original_data, "corrected_data": r.corrected_data, "notes": r.reviewer_notes,
        "decided_at": r.decided_at.isoformat() if r.decided_at else None,
    } for r in rows]
