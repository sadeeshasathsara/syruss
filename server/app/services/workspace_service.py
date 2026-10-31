"""
Organization home and workspaces. A workspace is a template and everything scoped to it;
it is addressed in URLs by its slug, which is unique within the organization.
"""
from typing import Dict, List
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.models.conversation import Conversation
from app.models.document import Document, DocumentRole, DocumentStatus
from app.models.question import AgentQuestion, AgentQuestionStatus
from app.models.knowledge import KnowledgeProposal, KnowledgeProposalStatus
from app.models.template import DocumentTemplate
from app.models.tenant import Tenant
from app.models.workspace import Workspace
from app.schemas.workspace import OrganizationResponse, WorkspaceSummary


async def unique_slug(db: AsyncSession, tenant_id: str, base: str, exclude_id: str | None = None) -> str:
    """`invoices`, then `invoices-2`, `invoices-3`... within the organization."""
    base = base or "workspace"
    taken = set((await db.execute(
        select(Workspace.slug).where(Workspace.tenant_id == tenant_id, Workspace.id != (exclude_id or ""))
    )).scalars().all())
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


class WorkspaceService:
    @staticmethod
    async def organization(db: AsyncSession, tenant_id: str) -> OrganizationResponse:
        tenant = await db.get(Tenant, tenant_id)
        if not tenant:
            raise HTTPException(status_code=404, detail="Organization not found.")
        return OrganizationResponse(id=tenant.id, name=tenant.name, slug=tenant.slug)

    @staticmethod
    async def list_summaries(db: AsyncSession, tenant_id: str) -> List[WorkspaceSummary]:
        rows = (await db.execute(
            select(Workspace, DocumentTemplate)
            .join(DocumentTemplate, DocumentTemplate.id == Workspace.id)
            .where(Workspace.tenant_id == tenant_id)
            .order_by(Workspace.created_at)
        )).all()
        if not rows:
            return []
        ids = [w.id for w, _ in rows]

        def counts(stmt) -> Dict[str, int]:
            return {k: v for k, v in stmt}

        docs = counts((await db.execute(
            select(Document.template_id, func.count(Document.id))
            .where(Document.template_id.in_(ids), Document.role == DocumentRole.DOCUMENT)
            .group_by(Document.template_id)
        )).all())
        review = counts((await db.execute(
            select(Document.template_id, func.count(Document.id))
            .where(Document.template_id.in_(ids), Document.status == DocumentStatus.NEEDS_REVIEW)
            .group_by(Document.template_id)
        )).all())
        questions = counts((await db.execute(
            select(AgentQuestion.template_id, func.count(AgentQuestion.id))
            .where(AgentQuestion.template_id.in_(ids), AgentQuestion.status == AgentQuestionStatus.OPEN)
            .group_by(AgentQuestion.template_id)
        )).all())
        proposals = counts((await db.execute(
            select(KnowledgeProposal.template_id, func.count(KnowledgeProposal.id))
            .where(KnowledgeProposal.template_id.in_(ids), KnowledgeProposal.status == KnowledgeProposalStatus.PENDING)
            .group_by(KnowledgeProposal.template_id)
        )).all())
        conversations = counts((await db.execute(
            select(Conversation.template_id, func.count(Conversation.id))
            .where(Conversation.template_id.in_(ids), Conversation.kind == "agent", Conversation.status == "open")
            .group_by(Conversation.template_id)
        )).all())
        return [WorkspaceService._summary(w, t, docs, review, questions, proposals, conversations) for w, t in rows]

    @staticmethod
    def _summary(w, t, docs, review, questions, proposals, conversations) -> WorkspaceSummary:
        return WorkspaceSummary(
            id=w.id, slug=w.slug, name=w.name, description=t.description,
            document_type=t.document_type,
            status="active" if t.active_version_id else "draft",
            documents=docs.get(w.id, 0), needs_review=review.get(w.id, 0),
            open_questions=questions.get(w.id, 0), pending_proposals=proposals.get(w.id, 0),
            open_conversations=conversations.get(w.id, 0),
            created_at=w.created_at, updated_at=t.updated_at,
        )

    @staticmethod
    async def get_by_slug(db: AsyncSession, tenant_id: str, slug: str) -> WorkspaceSummary:
        row = (await db.execute(
            select(Workspace, DocumentTemplate)
            .join(DocumentTemplate, DocumentTemplate.id == Workspace.id)
            .where(Workspace.tenant_id == tenant_id, Workspace.slug == slug)
        )).first()
        if not row:
            raise HTTPException(status_code=404, detail=f"No workspace '{slug}' in this organization.")
        summaries = await WorkspaceService.list_summaries(db, tenant_id)
        return next(s for s in summaries if s.id == row[0].id)


workspace_service = WorkspaceService()
