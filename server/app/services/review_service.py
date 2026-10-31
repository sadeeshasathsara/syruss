"""
Human review of records the agent was not sure about.

A decision finalizes the document and wakes the agent with `review.completed`, including
what the human changed. The agent decides what to learn from it (aliases, new entities,
lessons, template proposals). Reviews never write knowledge directly.
"""
from datetime import datetime, timezone
from typing import List
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.scope import TemplateScope
from app.models.audit import AuditLog
from app.models.document import Document, DocumentStatus
from app.models.review import ReviewStatus, ReviewTask
from app.schemas.review import ReviewDecisionRequest
from app.services.agent_events import enqueue_agent_event, agent_event_relay
from app.services import extraction_log
from app.services.record_service import record_service


class ReviewService:
    @staticmethod
    async def list_pending(
        db: AsyncSession, scope: TemplateScope, limit: int = 50, offset: int = 0, *, decided: bool = False,
    ) -> List[ReviewTask]:
        status = ReviewTask.status != ReviewStatus.PENDING if decided else ReviewTask.status == ReviewStatus.PENDING
        order = ReviewTask.decided_at.desc() if decided else ReviewTask.created_at.desc()
        stmt = (
            select(ReviewTask)
            .where(ReviewTask.template_id == scope.template_id, status)
            .order_by(order).offset(offset).limit(limit)
        )
        return list((await db.execute(stmt)).scalars().all())

    @staticmethod
    async def get(db: AsyncSession, scope: TemplateScope, task_id: str) -> ReviewTask:
        task = await db.get(ReviewTask, task_id)
        if not task or task.template_id != scope.template_id:
            raise HTTPException(status_code=404, detail="Review task not found in this workspace.")
        return task

    @staticmethod
    async def decide(db: AsyncSession, scope: TemplateScope, task_id: str, decision: ReviewDecisionRequest) -> ReviewTask:
        task = await ReviewService.get(db, scope, task_id)
        if task.status != ReviewStatus.PENDING:
            raise HTTPException(status_code=400, detail="Review task has already been resolved.")
        doc = await db.get(Document, task.document_id)
        action = decision.action.upper()

        if action == "APPROVE":
            final_data = task.original_data
        elif action == "CORRECT":
            if not decision.corrected_data:
                raise HTTPException(status_code=400, detail="corrected_data is required for CORRECT.")
            final_data = decision.corrected_data
        elif action == "REJECT":
            final_data = None
        else:
            raise HTTPException(status_code=400, detail="action must be APPROVE, CORRECT or REJECT.")

        original = task.original_data
        if final_data is not None:
            # Human-confirmed records still have to satisfy the template.
            await record_service.submit(
                db, template=scope.template, document=doc, data=final_data, decision="COMMIT",
                confidence=1.0, evidence=doc.extraction.evidence_records if doc.extraction else [],
                notes=f"Confirmed by {scope.username}", actor=scope.user_id, discard_pending_review=False,
                check_identities=False,
            )
            task.status = ReviewStatus.APPROVED if action == "APPROVE" else ReviewStatus.CORRECTED
            task.corrected_data = decision.corrected_data if action == "CORRECT" else None
        else:
            task.status = ReviewStatus.REJECTED
            doc.status = DocumentStatus.FAILED
            doc.error_message = f"Rejected in review: {decision.notes or 'no reason given'}"

        task.reviewer_notes = decision.notes
        task.decided_by = scope.username
        task.decided_at = datetime.now(timezone.utc)
        db.add(AuditLog(
            tenant_id=scope.tenant_id, template_id=scope.template_id, event_type="HUMAN_REVIEW_DECIDED",
            entity_type="ReviewTask", entity_id=task.id, actor=scope.user_id,
            details={"action": action, "document_id": doc.id, "notes": decision.notes},
        ))
        enqueue_agent_event(
            db, event_type="review.completed", tenant_id=scope.tenant_id, template_id=scope.template_id,
            payload={
                "review_id": task.id, "document_id": doc.id, "action": action, "reason": task.reason,
                "original_data": original, "corrected_data": decision.corrected_data, "notes": decision.notes,
            },
            actor_id=scope.user_id, actor_type="user",
        )
        await db.commit()
        agent_event_relay.notify()
        verb = {"APPROVE": "approved", "CORRECT": "corrected and approved", "REJECT": "rejected"}[action]
        await extraction_log.record(
            tenant_id=scope.tenant_id, template_id=scope.template_id, stage="decided", actor=scope.username, document_id=doc.id,
            document_name=doc.original_filename, level="error" if action == "REJECT" else "success",
            message=f"Review {verb}." + (f" {decision.notes}" if decision.notes else ""))
        await db.refresh(task)
        return task


review_service = ReviewService()
