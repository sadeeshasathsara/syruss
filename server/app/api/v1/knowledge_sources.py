"""
Reference data for a workspace: upload a file or connect an API; the agent works out how it
maps onto the template, and the import runs once you approve its mapping.
"""
from typing import Dict, List, Literal, Optional
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db_session
from app.core.scope import TemplateScope, get_template_scope
from app.core.secrets import unseal
from app.models.knowledge import KnowledgeProposal, KnowledgeProposalStatus, KnowledgeProposalType
from app.models.knowledge_source import KnowledgeSource
from app.services import source_service
from app.services.agent_events import agent_event_relay
from app.services.conversation_service import conversation_service
from app.services.source_data import record_sets
from app.core.permissions import Access, require

router = APIRouter(prefix="/knowledge/sources", tags=["Knowledge sources"], dependencies=[Depends(require("knowledge.view"))])


class ApiAuth(BaseModel):
    type: Literal["none", "bearer", "basic", "api_key", "oauth2_client_credentials"] = "none"
    token: Optional[str] = Field(None, description="Bearer token (secret)")
    username: Optional[str] = None
    password: Optional[str] = Field(None, description="Basic auth password (secret)")
    key_name: Optional[str] = Field(None, description="Header or query parameter that carries the API key")
    key_in: Literal["header", "query"] = "header"
    key_value: Optional[str] = Field(None, description="The API key (secret)")
    token_url: Optional[str] = None
    client_id: Optional[str] = None
    client_secret: Optional[str] = Field(None, description="OAuth client secret (secret)")
    scope: Optional[str] = None


class ApiSourceIn(BaseModel):
    name: str = ""
    url: str
    method: Literal["GET", "POST"] = "GET"
    query: Dict[str, str] = Field(default_factory=dict)
    headers: Dict[str, str] = Field(default_factory=dict, description="Header values are stored as secrets")
    body: Optional[str] = Field(None, description="JSON body for POST")
    auth: ApiAuth = Field(default_factory=ApiAuth)
    notes: str = Field("", description="What this data is, for the agent")


class RemapIn(BaseModel):
    notes: str = ""


async def _source(db: AsyncSession, scope: TemplateScope, source_id: str) -> KnowledgeSource:
    source = await db.get(KnowledgeSource, source_id)
    if not source or source.template_id != scope.template_id:
        raise HTTPException(status_code=404, detail="Source not found in this workspace.")
    return source


async def _ask_agent(db: AsyncSession, scope: TemplateScope, source: KnowledgeSource, *, reason: str, line: str,
                     conversation_id: Optional[str], notes: str = "") -> None:
    msg = await conversation_service.record_action(db, scope, line, conversation_id)
    source_service.wake_agent(db, source, reason=reason, actor_id=scope.user_id, actor_name=scope.username,
                              conversation_id=msg.conversation_id if msg else None, notes=notes)


@router.get("")
async def list_sources(db: AsyncSession = Depends(get_db_session), scope: TemplateScope = Depends(get_template_scope)):
    sources = (await db.execute(
        select(KnowledgeSource).where(KnowledgeSource.template_id == scope.template_id).order_by(KnowledgeSource.created_at.desc())
    )).scalars().all()
    return [source_service.public_view(s) for s in sources]


@router.post("/file", status_code=201)
async def add_file(
    file: UploadFile = File(...),
    notes: str = Form(""),
    db: AsyncSession = Depends(get_db_session),
    scope: TemplateScope = Depends(get_template_scope),
    _access: Access = Depends(require("knowledge.manage")),
    x_conversation_id: Optional[str] = Header(None, alias="X-Conversation-ID"),
):
    source = await source_service.create_file_source(
        db, tenant_id=scope.tenant_id, template_id=scope.template_id, user_id=scope.user_id, file=file, notes=notes,
    )
    await _ask_agent(db, scope, source, conversation_id=x_conversation_id, reason="added", line=f"You added the data source '{source.name}'.")
    await db.commit()
    agent_event_relay.notify()
    return source_service.public_view(source)


@router.post("/api/test")
async def test_api(body: ApiSourceIn, source_id: Optional[str] = None,
                   db: AsyncSession = Depends(get_db_session), scope: TemplateScope = Depends(get_template_scope),
    _access: Access = Depends(require("knowledge.manage")),
):
    """Calls the API as configured, without saving, and shows the records it found."""
    current = unseal((await _source(db, scope, source_id)).secrets) if source_id else None
    _, _, data = await source_service.fetch_preview(body.model_dump(), current)
    return {"record_sets": record_sets(data)}


@router.post("/api", status_code=201)
async def add_api(body: ApiSourceIn, db: AsyncSession = Depends(get_db_session), scope: TemplateScope = Depends(get_template_scope), _access: Access = Depends(require("knowledge.manage")), x_conversation_id: Optional[str] = Header(None, alias="X-Conversation-ID")):
    source = await source_service.create_api_source(
        db, tenant_id=scope.tenant_id, template_id=scope.template_id, user_id=scope.user_id, body=body.model_dump(),
    )
    await _ask_agent(db, scope, source, conversation_id=x_conversation_id, reason="added", line=f"You connected the API '{source.name}'.")
    await db.commit()
    agent_event_relay.notify()
    return source_service.public_view(source)


@router.put("/{source_id}")
async def update_api(source_id: str, body: ApiSourceIn, db: AsyncSession = Depends(get_db_session), scope: TemplateScope = Depends(get_template_scope), _access: Access = Depends(require("knowledge.manage")), x_conversation_id: Optional[str] = Header(None, alias="X-Conversation-ID")):
    source = await _source(db, scope, source_id)
    if source.kind != "API":
        raise HTTPException(status_code=400, detail="Only API sources have settings; upload a new file instead.")
    await source_service.update_api_source(db, source, body.model_dump())
    problems, _ = source_service.check_mapping(scope.template.schema_definition, source.data, source.mapping or {})
    if problems:
        await _ask_agent(db, scope, source, conversation_id=x_conversation_id, reason="changed", line=f"You changed the API '{source.name}'.")
    await db.commit()
    agent_event_relay.notify()
    return source_service.public_view(source)


@router.post("/{source_id}/sync")
async def sync(source_id: str, db: AsyncSession = Depends(get_db_session), scope: TemplateScope = Depends(get_template_scope), _access: Access = Depends(require("knowledge.manage")), x_conversation_id: Optional[str] = Header(None, alias="X-Conversation-ID")):
    """Reads the source again and imports it with the approved mapping."""
    source = await _source(db, scope, source_id)
    try:
        await source_service.refresh(source)
    finally:
        await db.commit()  # keep the error on the source when the API fails
    if not source.mapping:
        raise HTTPException(status_code=409, detail="There is no approved mapping yet.")
    problems, _ = source_service.check_mapping(scope.template.schema_definition, source.data, source.mapping)
    if problems:
        await _ask_agent(db, scope, source, conversation_id=x_conversation_id, reason="changed", notes="; ".join(problems),
                         line=f"'{source.name}' no longer fits its mapping, so the agent is looking at it again.")
        await db.commit()
        agent_event_relay.notify()
        return {"source": source_service.public_view(source), "imported": None}
    totals = await source_service.import_records(db, source, source.mapping, schema=scope.template.schema_definition, actor=scope.user_id)
    await conversation_service.record_action(
        db, scope, f"You synced '{source.name}': {totals['created']} new, {totals['updated']} updated.", x_conversation_id,
    )
    await db.commit()
    return {"source": source_service.public_view(source), "imported": totals}


@router.post("/{source_id}/remap")
async def remap(source_id: str, body: RemapIn, db: AsyncSession = Depends(get_db_session), scope: TemplateScope = Depends(get_template_scope), _access: Access = Depends(require("knowledge.manage")), x_conversation_id: Optional[str] = Header(None, alias="X-Conversation-ID")):
    """Asks the agent to work out the mapping again, with your notes."""
    source = await _source(db, scope, source_id)
    await _ask_agent(db, scope, source, conversation_id=x_conversation_id, reason="remap", notes=body.notes,
                     line=f"You asked the agent to map '{source.name}' again." + (f" {body.notes}" if body.notes else ""))
    await db.commit()
    agent_event_relay.notify()
    return source_service.public_view(source)


@router.delete("/{source_id}", status_code=204)
async def remove(source_id: str, db: AsyncSession = Depends(get_db_session), scope: TemplateScope = Depends(get_template_scope), _access: Access = Depends(require("knowledge.manage"))):
    """Removes the source. Entities it already imported stay known."""
    source = await _source(db, scope, source_id)
    pending: List[KnowledgeProposal] = list((await db.execute(select(KnowledgeProposal).where(
        KnowledgeProposal.template_id == scope.template_id,
        KnowledgeProposal.proposal_type == KnowledgeProposalType.SOURCE_MAPPING.value,
        KnowledgeProposal.status == KnowledgeProposalStatus.PENDING,
    ))).scalars().all())
    for p in pending:
        if (p.proposed_data or {}).get("source_id") == source.id:
            p.status = KnowledgeProposalStatus.REJECTED
            p.reviewer_notes = "The source was removed."
    await db.delete(source)
    await db.commit()
