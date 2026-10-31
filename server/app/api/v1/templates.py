import time
import uuid
from datetime import datetime, timezone
from typing import List, Optional
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.auth import AuthContext, get_current_auth
from app.core.database import get_db_session
from app.core.scope import TemplateScope, get_template_scope, resolve_template_scope
from app.models.audit import AuditLog
from app.models.conversation import Conversation
from app.models.knowledge import KnowledgeProposal, KnowledgeProposalStatus, KnowledgeProposalType
from app.models.template import DocumentTemplate
from app.models.template_enterprise import (
    DocumentTemplateVersion,
    GoldenDatasetItem,
    TemplateEvaluationRun,
    TemplateLifecycleStatus,
)
from app.models.workspace import Workspace
from app.models.workspace_admin import WorkspaceMember
from app.schemas.conversation import ChatMessageCreate
from app.schemas.template import (
    GoldenDatasetItemCreate,
    GoldenDatasetItemResponse,
    TemplateBenchmarkResponse,
    TemplateChatRequest,
    TemplateChatResponse,
    TemplateCreate,
    TemplateDiscoveryResponse,
    TemplateManifestExport,
    TemplatePromotionRequest,
    TemplateResponse,
    TemplateUpdate,
    TemplateVersionCreate,
    TemplateVersionResponse,
)
from app.services.benchmark_service import run_benchmark
from app.services.conversation_service import conversation_service
from app.services.template_service import template_service
from app.core.permissions import Access, require
from app.core.permissions import check_permission

router = APIRouter(prefix="/templates", tags=["Templates"])


# ==============================================================================
# Designing a template with the agent
# ==============================================================================

@router.post("/discover", response_model=TemplateDiscoveryResponse)
async def discover_template_from_samples(
    files: Optional[List[UploadFile]] = File(None, description="1 to 5 sample documents; none re-studies the existing samples"),
    user_notes: Optional[str] = Form(None),
    session_id: Optional[str] = Form(None, description="Live channel for the agent's progress"),
    template_id: Optional[str] = Form(None, description="Refine this template; omit to start a new draft workspace"),
    db: AsyncSession = Depends(get_db_session),
    auth: AuthContext = Depends(get_current_auth),
):
    """
    Stores the samples in the template workspace and wakes the agent. The agent's proposed
    schema arrives on the session channel as `template_discovered`, its questions as `agent_question`.
    """
    channel_id = session_id or f"tpl_{uuid.uuid4().hex[:8]}"
    template = None
    if template_id:
        scope = await resolve_template_scope(db, auth, template_id)
        await check_permission(db, scope, auth, "template.publish")
        template = scope.template
    conversation_id = None
    if template is not None:
        conversation_id = await conversation_service.linked_conversation(db, scope, session_id)
    template = await template_service.start_discovery(
        db, tenant_id=auth.tenant_id, user_id=auth.user_id, template=template,
        files=files or [], notes=user_notes, channel_id=channel_id, conversation_id=conversation_id,
    )
    scope = await resolve_template_scope(db, auth, template.id)
    await conversation_service.get_or_create_for_channel(db, scope, channel_id)
    await db.commit()
    return TemplateDiscoveryResponse(
        template_id=template.id, name=template.name, document_type=template.document_type,
        session_id=channel_id, status="QUEUED",
    )


@router.post("/{template_id}/proposal/withdraw")
async def withdraw_template_proposal(
    scope: TemplateScope = Depends(get_template_scope),
    db: AsyncSession = Depends(get_db_session),
    _access: Access = Depends(require("template.publish")),
    x_conversation_id: Optional[str] = Header(None, alias="X-Conversation-ID"),
):
    """Sets the agent's pending template proposal aside so the admin can start the design over."""
    result = await template_service.withdraw_proposal(
        db, template=scope.template, user_id=scope.user_id, is_draft=not scope.template.active_version_id,
    )
    await conversation_service.record_action(db, scope, "You withdrew the agent's proposal to start over.", x_conversation_id)
    await db.commit()
    return result


@router.post("/chat", response_model=TemplateChatResponse)
async def chat_about_template(
    payload: TemplateChatRequest,
    db: AsyncSession = Depends(get_db_session),
    auth: AuthContext = Depends(get_current_auth),
):
    """Talk to the agent while designing a template; it may answer and revise its proposal."""
    conv = await db.get(Conversation, payload.session_id)
    template_id = payload.template_id or (conv.template_id if conv else None)
    if not template_id:
        raise HTTPException(status_code=400, detail="Unknown session. Upload samples via /templates/discover first.")
    scope = await resolve_template_scope(db, auth, template_id)
    await conversation_service.get_or_create_for_channel(db, scope, payload.session_id)
    await db.commit()

    context = {"current_page": "template_design"}
    if payload.schema_definition:
        context["schema_on_screen"] = payload.schema_definition
    reply = await conversation_service.send_and_wait(
        db, scope, payload.session_id, ChatMessageCreate(content=payload.message, context=context),
    )
    latest = (await db.execute(
        select(KnowledgeProposal).where(
            KnowledgeProposal.template_id == template_id,
            KnowledgeProposal.proposal_type == KnowledgeProposalType.TEMPLATE_CHANGE.value,
            KnowledgeProposal.status == KnowledgeProposalStatus.PENDING,
        ).order_by(KnowledgeProposal.created_at.desc())
    )).scalars().first()
    schema = latest.proposed_data.get("schema_definition") if latest else None
    return TemplateChatResponse(
        reply=reply["content"],
        updated_schema=schema,
        updated_rules=schema.get("validation_rules") if schema else None,
    )


# ==============================================================================
# CRUD
# ==============================================================================

def _with_slug(template: DocumentTemplate, ws: Optional[Workspace]) -> DocumentTemplate:
    setattr(template, "slug", ws.slug if ws else None)
    setattr(template, "workspace_slug", ws.slug if ws else None)
    return template


@router.post("", response_model=TemplateResponse, status_code=201)
async def create_template(
    payload: TemplateCreate,
    db: AsyncSession = Depends(get_db_session),
    auth: AuthContext = Depends(get_current_auth),
):
    """Creates a template, or finalizes the draft created during discovery (pass its `id`)."""
    if payload.id:
        scope = await resolve_template_scope(db, auth, payload.id)
        await check_permission(db, scope, auth, "template.publish")
        template = scope.template
        template.name = payload.name
        template.description = payload.description or template.description
        if payload.document_type:
            template.document_type = payload.document_type
        await template_service.add_version(
            db, template, payload.schema_definition, change_summary="Finalized in the template designer",
            created_by=auth.user_id, activate=True, approved_by=auth.user_id,
        )
        for p in (await db.execute(select(KnowledgeProposal).where(
            KnowledgeProposal.template_id == template.id,
            KnowledgeProposal.proposal_type == KnowledgeProposalType.TEMPLATE_CHANGE.value,
            KnowledgeProposal.status == KnowledgeProposalStatus.PENDING,
        ))).scalars().all():
            p.status = KnowledgeProposalStatus.APPROVED
            p.reviewed_by = auth.username
            p.reviewer_notes = "Accepted (possibly edited) in the template designer."
            p.reviewed_at = datetime.now(timezone.utc)
        ws = await db.get(Workspace, template.id)
        if ws:
            ws.name = payload.name
    else:
        template = await template_service.create_template(
            db, tenant_id=auth.tenant_id, user_id=auth.user_id, name=payload.name,
            document_type=payload.document_type, description=payload.description,
            schema_definition=payload.schema_definition,
        )
    await db.commit()
    await db.refresh(template)
    return _with_slug(template, await db.get(Workspace, template.id))


@router.get("", response_model=List[TemplateResponse])
async def list_templates(db: AsyncSession = Depends(get_db_session), auth: AuthContext = Depends(get_current_auth)):
    """Workspaces the caller is a member of (an API key: its own workspace)."""
    stmt = select(DocumentTemplate).where(DocumentTemplate.tenant_id == auth.tenant_id)
    if "*" not in auth.accessible_template_ids:
        stmt = stmt.where(DocumentTemplate.id.in_(auth.accessible_template_ids))
    else:
        stmt = stmt.where(DocumentTemplate.id.in_(
            select(WorkspaceMember.template_id).where(WorkspaceMember.user_id == auth.user_id, WorkspaceMember.tenant_id == auth.tenant_id)
        ))
    templates = (await db.execute(stmt.order_by(DocumentTemplate.created_at.desc()))).scalars().all()
    return [_with_slug(t, await db.get(Workspace, t.id)) for t in templates]


@router.get("/{template_id}", response_model=TemplateResponse, dependencies=[Depends(require("documents.view"))])
async def get_template(scope: TemplateScope = Depends(get_template_scope), db: AsyncSession = Depends(get_db_session)):
    return _with_slug(scope.template, await db.get(Workspace, scope.template_id))


@router.put("/{template_id}", response_model=TemplateResponse)
async def update_template(
    payload: TemplateUpdate,
    db: AsyncSession = Depends(get_db_session),
    scope: TemplateScope = Depends(get_template_scope),
    _access: Access = Depends(require("template.publish")),
):
    """Edits metadata. A new schema becomes a new active version (humans may change templates directly)."""
    template = scope.template
    if payload.name is not None:
        template.name = payload.name
    if payload.document_type is not None:
        template.document_type = payload.document_type
    if payload.description is not None:
        template.description = payload.description
    if payload.schema_definition is not None:
        await template_service.add_version(
            db, template, payload.schema_definition, change_summary="Edited directly",
            created_by=scope.user_id, activate=True, approved_by=scope.user_id,
        )
    db.add(AuditLog(
        tenant_id=scope.tenant_id, template_id=template.id, event_type="TEMPLATE_UPDATED",
        entity_type="DocumentTemplate", entity_id=template.id, actor=scope.user_id,
        details={"name": template.name, "schema_changed": payload.schema_definition is not None},
    ))
    await db.commit()
    await db.refresh(template)
    return _with_slug(template, await db.get(Workspace, template.id))


@router.delete("/{template_id}", status_code=204)
async def delete_template(db: AsyncSession = Depends(get_db_session), scope: TemplateScope = Depends(get_template_scope), _access: Access = Depends(require("workspace.delete"))):
    """Deletes the template workspace and everything in it."""
    ws = await db.get(Workspace, scope.template_id)
    if ws:
        await db.delete(ws)
    await db.delete(scope.template)
    await db.commit()


# ==============================================================================
# Versions
# ==============================================================================

@router.post("/{template_id}/versions", response_model=TemplateVersionResponse, status_code=201)
async def create_template_version(
    payload: TemplateVersionCreate,
    db: AsyncSession = Depends(get_db_session),
    scope: TemplateScope = Depends(get_template_scope),
    _access: Access = Depends(require("template.publish")),
):
    """Creates a DRAFT version; promote it to make it active."""
    schema = {**payload.schema_definition, "validation_rules": payload.validation_rules or payload.schema_definition.get("validation_rules", [])}
    version = await template_service.add_version(
        db, scope.template, schema, change_summary=payload.change_summary or "", created_by=scope.user_id,
        version=payload.version.strip(),
    )
    await db.commit()
    await db.refresh(version)
    return version


@router.get("/{template_id}/versions", response_model=List[TemplateVersionResponse], dependencies=[Depends(require("documents.view"))])
async def list_template_versions(db: AsyncSession = Depends(get_db_session), scope: TemplateScope = Depends(get_template_scope)):
    stmt = (
        select(DocumentTemplateVersion)
        .where(DocumentTemplateVersion.template_id == scope.template_id)
        .order_by(DocumentTemplateVersion.created_at.desc())
    )
    return list((await db.execute(stmt)).scalars().all())


async def _version(db: AsyncSession, scope: TemplateScope, version_id: str) -> DocumentTemplateVersion:
    version = await db.get(DocumentTemplateVersion, version_id)
    if not version or version.template_id != scope.template_id:
        raise HTTPException(status_code=404, detail="Version not found.")
    return version


@router.get("/{template_id}/versions/{version_id}", response_model=TemplateVersionResponse, dependencies=[Depends(require("documents.view"))])
async def get_template_version(version_id: str, db: AsyncSession = Depends(get_db_session), scope: TemplateScope = Depends(get_template_scope)):
    return await _version(db, scope, version_id)


@router.post("/{template_id}/versions/{version_id}/promote", response_model=TemplateVersionResponse)
async def promote_template_version(
    version_id: str,
    payload: TemplatePromotionRequest,
    db: AsyncSession = Depends(get_db_session),
    scope: TemplateScope = Depends(get_template_scope),
    _access: Access = Depends(require("template.publish")),
):
    version = await _version(db, scope, version_id)
    try:
        target = TemplateLifecycleStatus(payload.target_status.upper())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid target status: {payload.target_status}")
    if target == TemplateLifecycleStatus.ACTIVE:
        await template_service.activate_version(db, scope.template, version, approved_by=scope.user_id)
    else:
        if version.id == scope.template.active_version_id:
            raise HTTPException(status_code=409, detail="Activate another version before demoting the active one.")
        version.status = target
    db.add(AuditLog(
        tenant_id=scope.tenant_id, template_id=scope.template_id, event_type="TEMPLATE_VERSION_PROMOTED",
        entity_type="DocumentTemplateVersion", entity_id=version.id, actor=scope.user_id,
        details={"status": target.value, "approval_notes": payload.approval_notes},
    ))
    await db.commit()
    await db.refresh(version)
    return version


# ==============================================================================
# Golden dataset & benchmark
# ==============================================================================

@router.post("/{template_id}/golden-dataset", response_model=GoldenDatasetItemResponse, status_code=201)
async def add_golden_dataset_item(
    payload: GoldenDatasetItemCreate,
    db: AsyncSession = Depends(get_db_session),
    scope: TemplateScope = Depends(get_template_scope),
    _access: Access = Depends(require("template.publish")),
):
    item = GoldenDatasetItem(
        template_id=scope.template_id, tenant_id=scope.tenant_id, name=payload.name,
        storage_path=payload.storage_path, ground_truth_json=payload.ground_truth_json,
        complexity_tag=payload.complexity_tag,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item


@router.get("/{template_id}/golden-dataset", response_model=List[GoldenDatasetItemResponse], dependencies=[Depends(require("documents.view"))])
async def list_golden_dataset_items(db: AsyncSession = Depends(get_db_session), scope: TemplateScope = Depends(get_template_scope)):
    stmt = select(GoldenDatasetItem).where(GoldenDatasetItem.template_id == scope.template_id).order_by(GoldenDatasetItem.created_at)
    return list((await db.execute(stmt)).scalars().all())


@router.post("/{template_id}/versions/{version_id}/benchmark", response_model=TemplateBenchmarkResponse)
async def benchmark_template_version(
    version_id: str,
    db: AsyncSession = Depends(get_db_session),
    scope: TemplateScope = Depends(get_template_scope),
    _access: Access = Depends(require("template.publish")),
):
    """Scores the agent's real records for golden documents processed with this version."""
    version = await _version(db, scope, version_id)
    started = time.perf_counter()
    report = await run_benchmark(db, version)
    if report["item_count"] == 0:
        raise HTTPException(status_code=409, detail="This template has no golden dataset items to benchmark against.")
    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    db.add(TemplateEvaluationRun(
        version_id=version.id, status=report["status"], metrics=report,
        failed_items_count=report["failed_count"], execution_duration_ms=duration_ms,
    ))
    version.benchmark_score = {k: report[k] for k in ("f1_score", "precision", "recall", "rule_pass_rate", "status", "evaluated_count")}
    await db.commit()
    return TemplateBenchmarkResponse(version_id=version.id, execution_duration_ms=duration_ms, **report)


@router.get("/{template_id}/export", response_model=TemplateManifestExport, dependencies=[Depends(require("documents.view"))])
async def export_template_manifest(db: AsyncSession = Depends(get_db_session), scope: TemplateScope = Depends(get_template_scope)):
    template = scope.template
    active = await template_service.get_active_version(db, template)
    return TemplateManifestExport(
        metadata={
            "id": template.id, "name": template.name, "document_type": template.document_type,
            "version": active.version if active else None, "exported_at": datetime.now(timezone.utc).isoformat(),
        },
        spec={"description": template.description, "schema_definition": template.schema_definition},
    )
