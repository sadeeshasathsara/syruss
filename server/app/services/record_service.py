"""
The single writer of document outcomes.

The agent decides what a document says and whether it is sure. This service decides
whether that outcome is allowed:
  * COMMIT  - only if the record passes the active template's schema and rules, and (for the
              agent) its identities agree with what the workspace already knows.
              Otherwise it is refused with the reasons, so the agent can re-check the
              page, ask a human, or send the document to review.
  * REVIEW  - always allowed; the record is stored as a draft and a review task opens.

Agent records also get signals: the workspace graph's identity checks (app.services.graph_service)
and the duplicate check (app.services.duplicate_check). A blocking signal refuses the commit until
the agent acknowledges it with its reason; every signal is kept on the record's validation, so
reviewers see it too. Committed records feed the graph.
"""
from typing import Any, Dict, List, Optional
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.audit import AuditLog
from app.models.document import Document, DocumentStatus
from app.models.extraction import ExtractionResult
from app.models.review import ReviewTask, ReviewStatus
from app.models.template import DocumentTemplate
from app.services import delivery_service, duplicate_check, graph_service
from app.services.identity_check import find_identity_conflicts, find_unsupported_identities
from app.services.rule_engine import validate_record


class RecordService:
    @staticmethod
    async def submit(
        db: AsyncSession,
        *,
        template: DocumentTemplate,
        document: Document,
        data: Dict[str, Any],
        decision: str,
        confidence: float,
        evidence: Optional[List[Dict[str, Any]]] = None,
        notes: Optional[str] = None,
        review_reason: Optional[str] = None,
        uncertain_fields: Optional[List[Dict[str, Any]]] = None,
        actor: str = "agent",
        discard_pending_review: bool = True,
        check_identities: bool = True,
        acknowledged_signals: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        `discard_pending_review`: when the agent commits a document it had sent to review
        (e.g. after a human answered its question), the open review task is no longer needed.
        Human review decisions pass False and close the task themselves.
        `check_identities`: humans are the authority on identity, so their decisions skip it.
        `acknowledged_signals`: [{id, reason}] for graph signals the agent looked into and stands by.
        """
        decision = decision.upper()
        if decision not in ("COMMIT", "REVIEW"):
            raise HTTPException(status_code=400, detail="decision must be COMMIT or REVIEW")
        if not template.active_version_id:
            raise HTTPException(status_code=409, detail="The template has no active version yet; records cannot be stored.")

        validation = validate_record(template.schema_definition, data)
        signals: List[Dict[str, Any]] = []
        if check_identities:
            signals = await graph_service.identity_signals(
                db, template_id=template.id, schema=template.schema_definition, data=data, document_id=document.id,
            ) + await duplicate_check.duplicate_signals(
                db, template_id=template.id, schema=template.schema_definition, data=data, document=document,
            )
            reasons = {a.get("id"): str(a.get("reason") or "").strip()
                       for a in acknowledged_signals or [] if isinstance(a, dict)}
            for signal in signals:
                if reasons.get(signal["id"]):
                    signal.update(acknowledged=True, reason=reasons[signal["id"]])
            validation = {**validation, "signals": signals}
        if decision == "COMMIT" and check_identities:
            conflicts = await find_unsupported_identities(
                db, template_id=template.id, schema=template.schema_definition, data=data,
                evidence=evidence or [], document_id=document.id,
            ) + await find_identity_conflicts(
                db, template_id=template.id, schema=template.schema_definition, data=data, document_id=document.id,
            ) + [
                {"code": sig["code"], "path": sig["path"], "message": f"{sig['message']} (signal {sig['id']})",
                 **({"evidence": sig["evidence"]} if sig.get("evidence") else {})}
                for sig in signals if sig["blocking"] and not sig.get("acknowledged")
            ]
            if conflicts:
                validation = {**validation, "valid": False, "errors": validation["errors"] + conflicts}
        if decision == "COMMIT" and not validation["valid"]:
            raise HTTPException(status_code=422, detail={
                "message": "Record refused: it does not satisfy the active template. Fix it or send it to review.",
                "errors": validation["errors"],
            })
        if decision == "REVIEW" and not (review_reason or "").strip():
            raise HTTPException(status_code=400, detail="review_reason is required when sending a document to review")

        result = (await db.execute(
            select(ExtractionResult).where(ExtractionResult.document_id == document.id)
        )).scalar_one_or_none()
        if result is None:
            result = ExtractionResult(
                tenant_id=document.tenant_id, template_id=document.template_id, document_id=document.id,
                extracted_data=data,
            )
            db.add(result)
        result.extracted_data = data
        result.template_version_id = template.active_version_id
        result.overall_confidence = max(0.0, min(1.0, float(confidence)))
        result.evidence_records = evidence or []
        result.validation = validation
        result.agent_notes = notes
        document.template_version_id = template.active_version_id
        document.error_message = None

        review = (await db.execute(
            select(ReviewTask).where(ReviewTask.document_id == document.id)
        )).scalar_one_or_none()

        if decision == "COMMIT":
            document.status = DocumentStatus.COMPLETED
            result.revision = (result.revision or 0) + 1
            if discard_pending_review and review and review.status == ReviewStatus.PENDING:
                await db.delete(review)
            await graph_service.record_sightings(
                db, tenant_id=document.tenant_id, template_id=template.id, document_id=document.id,
                schema=template.schema_definition, data=data,
            )
        else:
            await graph_service.forget_sightings(db, template.id, document.id)
            document.status = DocumentStatus.NEEDS_REVIEW
            if review is None:
                review = ReviewTask(
                    tenant_id=document.tenant_id, template_id=document.template_id, document_id=document.id,
                    reason=review_reason, original_data=data,
                )
                db.add(review)
            review.reason = review_reason
            review.original_data = data
            review.uncertain_fields = uncertain_fields or []
            review.status = ReviewStatus.PENDING
            review.corrected_data = None

        db.add(AuditLog(
            tenant_id=document.tenant_id, template_id=document.template_id,
            event_type="RECORD_COMMITTED" if decision == "COMMIT" else "RECORD_SENT_TO_REVIEW",
            entity_type="Document", entity_id=document.id, actor=actor,
            details={"confidence": result.overall_confidence, "validation_errors": len(validation["errors"]),
                     "template_version_id": template.active_version_id, "review_reason": review_reason,
                     "acknowledged_signals": [{"id": x["id"], "message": x["message"], "reason": x["reason"]}
                                              for x in signals if x.get("acknowledged")]},
        ))
        await db.flush()
        if decision == "COMMIT":
            # Written in this same transaction, so an approved record is never lost on its way out to the destinations.
            await delivery_service.enqueue(db, template=template, document=document, result=result, actor=actor)
        return {"status": document.status.value, "validation": validation}


record_service = RecordService()
