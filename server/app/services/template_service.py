"""
Template (= workspace) lifecycle: creation, versions, activation, and sample-based discovery.

Discovery is done by the workspace agent. The server only stores the samples inside the
template workspace and wakes the agent; the agent answers with a TEMPLATE_CHANGE proposal
and questions, which the wizard shows live.
"""
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from fastapi import HTTPException, UploadFile
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.audit import AuditLog
from app.models.document import Document, DocumentRole
from app.models.knowledge import KnowledgeProposal, KnowledgeProposalStatus, KnowledgeProposalType
from app.models.question import AgentQuestion, AgentQuestionStatus
from app.models.template import DocumentTemplate
from app.models.template_enterprise import DocumentTemplateVersion, TemplateLifecycleStatus
from app.models.identity import User
from app.models.workspace import Workspace
from app.models.workspace_admin import WorkspaceMember
from app.services.agent_events import enqueue_agent_event, agent_event_relay
from app.services.document_service import document_service
from app.services.rule_engine import check_rule_definitions
from app.services.workspace_service import unique_slug

MAX_SAMPLES = 5


def _slugify(value: str) -> str:
    return "-".join("".join(c if c.isalnum() else " " for c in value.lower()).split()) or "workspace"


def split_schema(schema: Dict[str, Any]) -> Dict[str, Any]:
    """Returns the schema with validation_rules kept inside it (single source of truth)."""
    schema = dict(schema or {})
    schema.setdefault("objects", [])
    schema.setdefault("validation_rules", [])
    return schema


class TemplateService:
    @staticmethod
    async def create_template(
        db: AsyncSession,
        *,
        tenant_id: str,
        user_id: str,
        name: str,
        document_type: Optional[str] = None,
        description: Optional[str] = None,
        schema_definition: Optional[Dict[str, Any]] = None,
    ) -> DocumentTemplate:
        """Creates the template and its workspace. A schema with objects becomes version 1.0.0 (ACTIVE)."""
        schema = split_schema(schema_definition or {})
        doc_type = document_type or schema.get("document_type") or name.lower().replace(" ", "_")
        template = DocumentTemplate(
            id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            name=name,
            document_type=doc_type,
            description=description or schema.get("description"),
            schema_definition=schema,
        )
        db.add(template)
        db.add(Workspace(
            id=template.id,
            tenant_id=tenant_id,
            name=name,
            slug=await unique_slug(db, tenant_id, _slugify(name)),
            description=template.description,
            is_active=True,
        ))
        await db.flush()
        # Whoever creates a workspace owns it: the only way into a workspace is a membership.
        creator = await db.get(User, user_id)
        db.add(WorkspaceMember(
            tenant_id=tenant_id, template_id=template.id, user_id=user_id, role="owner",
            name=creator.display_name if creator else user_id, email=creator.email if creator else None,
        ))
        await db.flush()
        if schema["objects"]:
            await TemplateService.add_version(
                db, template, schema, change_summary="Initial template definition",
                created_by=user_id, activate=True, approved_by=user_id,
            )
        db.add(AuditLog(
            tenant_id=tenant_id, template_id=template.id, event_type="TEMPLATE_CREATED",
            entity_type="DocumentTemplate", entity_id=template.id, actor=user_id,
            details={"name": name, "document_type": doc_type},
        ))
        return template

    @staticmethod
    async def get_template(db: AsyncSession, template_id: str) -> DocumentTemplate:
        template = await db.get(DocumentTemplate, template_id)
        if not template:
            raise HTTPException(status_code=404, detail="Template not found.")
        return template

    @staticmethod
    async def next_version_number(db: AsyncSession, template_id: str) -> str:
        count = await db.scalar(
            select(func.count(DocumentTemplateVersion.id)).where(DocumentTemplateVersion.template_id == template_id)
        ) or 0
        return f"1.{count}.0"

    @staticmethod
    async def add_version(
        db: AsyncSession,
        template: DocumentTemplate,
        schema: Dict[str, Any],
        *,
        change_summary: str,
        created_by: str,
        activate: bool = False,
        approved_by: Optional[str] = None,
        version: Optional[str] = None,
    ) -> DocumentTemplateVersion:
        schema = split_schema(schema)
        problems = check_rule_definitions(schema)
        if problems:
            raise HTTPException(status_code=422, detail={"message": "Invalid template schema", "problems": problems})
        row = DocumentTemplateVersion(
            id=str(uuid.uuid4()),
            template_id=template.id,
            version=version or await TemplateService.next_version_number(db, template.id),
            status=TemplateLifecycleStatus.DRAFT,
            schema_definition=schema,
            validation_rules=schema["validation_rules"],
            confidence_policy={},
            change_summary=change_summary,
            created_by=created_by,
        )
        db.add(row)
        await db.flush()
        if activate:
            await TemplateService.activate_version(db, template, row, approved_by=approved_by or created_by)
        return row

    @staticmethod
    async def activate_version(
        db: AsyncSession, template: DocumentTemplate, version: DocumentTemplateVersion, *, approved_by: str
    ) -> None:
        """Makes `version` the one the agent extracts with; archives the previous active version."""
        stmt = select(DocumentTemplateVersion).where(
            DocumentTemplateVersion.template_id == template.id,
            DocumentTemplateVersion.status == TemplateLifecycleStatus.ACTIVE,
            DocumentTemplateVersion.id != version.id,
        )
        for prev in (await db.execute(stmt)).scalars().all():
            prev.status = TemplateLifecycleStatus.ARCHIVED
        version.status = TemplateLifecycleStatus.ACTIVE
        version.approved_by = approved_by
        version.activated_at = datetime.now(timezone.utc)
        template.schema_definition = version.schema_definition
        template.active_version_id = version.id
        if version.schema_definition.get("document_type"):
            template.document_type = version.schema_definition["document_type"]
        if version.schema_definition.get("description"):
            template.description = version.schema_definition["description"]

    @staticmethod
    async def get_active_version(db: AsyncSession, template: DocumentTemplate) -> Optional[DocumentTemplateVersion]:
        if not template.active_version_id:
            return None
        return await db.get(DocumentTemplateVersion, template.active_version_id)

    @staticmethod
    async def start_discovery(
        db: AsyncSession,
        *,
        tenant_id: str,
        user_id: str,
        template: Optional[DocumentTemplate],
        files: List[UploadFile],
        notes: Optional[str],
        channel_id: str,
        conversation_id: Optional[str] = None,
    ) -> DocumentTemplate:
        """
        Stores samples in the template workspace (creating a draft workspace when needed)
        and wakes the agent to design or refine the template from them. Without new files,
        the agent studies the workspace's existing samples again.
        """
        if not files and template is None:
            raise HTTPException(status_code=400, detail="Upload at least one sample document.")
        if len(files) > MAX_SAMPLES:
            raise HTTPException(status_code=400, detail=f"Upload at most {MAX_SAMPLES} samples at once.")
        if template is None:
            template = await TemplateService.create_template(
                db, tenant_id=tenant_id, user_id=user_id,
                name=f"Untitled template {datetime.now(timezone.utc):%Y-%m-%d %H:%M}",
                document_type="draft",
            )
        sample_ids = []
        for f in files:
            doc = await document_service.store_document(
                db, tenant_id=tenant_id, template_id=template.id, file=f, role=DocumentRole.SAMPLE, actor=user_id,
            )
            sample_ids.append(doc.id)
        if not sample_ids:
            sample_ids = list((await db.execute(
                select(Document.id).where(Document.template_id == template.id, Document.role == DocumentRole.SAMPLE)
                .order_by(Document.created_at)
            )).scalars().all())
            if not sample_ids:
                raise HTTPException(status_code=400, detail="Upload at least one sample document.")
        enqueue_agent_event(
            db, event_type="template.samples_added", tenant_id=tenant_id, template_id=template.id,
            payload={"sample_document_ids": sample_ids, "notes": notes or "", "channel_id": channel_id},
            actor_id=user_id, actor_type="user", conversation_id=conversation_id,
        )
        await db.commit()
        agent_event_relay.notify()
        return template

    @staticmethod
    async def withdraw_proposal(db: AsyncSession, *, template: DocumentTemplate, user_id: str, is_draft: bool) -> Dict[str, int]:
        """
        The admin sets the agent's pending template proposal aside to start over. The agent is
        not woken: it studies again when samples are sent. In a draft workspace, the agent's open
        questions were all about that design, so they are closed with it.
        """
        now = datetime.now(timezone.utc)
        proposals = (await db.execute(select(KnowledgeProposal).where(
            KnowledgeProposal.template_id == template.id,
            KnowledgeProposal.proposal_type == KnowledgeProposalType.TEMPLATE_CHANGE.value,
            KnowledgeProposal.status == KnowledgeProposalStatus.PENDING,
        ))).scalars().all()
        if not proposals:
            raise HTTPException(status_code=409, detail="There is no template proposal to withdraw.")
        for p in proposals:
            p.status = KnowledgeProposalStatus.REJECTED
            p.reviewed_by = user_id
            p.reviewed_at = now
            p.reviewer_notes = "Withdrawn by the admin to start over."
        questions = []
        if is_draft:
            questions = (await db.execute(select(AgentQuestion).where(
                AgentQuestion.template_id == template.id, AgentQuestion.status == AgentQuestionStatus.OPEN,
            ))).scalars().all()
            for q in questions:
                q.status = AgentQuestionStatus.DISMISSED
                q.answer = "Withdrawn with the proposal."
                q.answered_by = user_id
                q.answered_at = now
        db.add(AuditLog(
            tenant_id=template.tenant_id, template_id=template.id, event_type="TEMPLATE_PROPOSAL_WITHDRAWN",
            entity_type="KnowledgeProposal", entity_id=proposals[0].id, actor=user_id,
            details={"proposals": len(proposals), "questions_closed": len(questions)},
        ))
        return {"proposals": len(proposals), "questions_closed": len(questions)}


template_service = TemplateService()
