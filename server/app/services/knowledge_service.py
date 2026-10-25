"""
Workspace knowledge: known entities, and the agent's proposals awaiting human approval.
"""
from datetime import datetime, timezone
from typing import List, Optional
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.scope import TemplateScope
from app.models.audit import AuditLog
from app.models.knowledge import (
    Entity, EntityAlias, EntityRelationship, KnowledgeProposal, KnowledgeProposalStatus, KnowledgeProposalType,
)
from app.models.knowledge_source import KnowledgeSource
from app.schemas.knowledge import EntityCreate
from app.services import source_service
from app.services.agent_events import enqueue_agent_event, agent_event_relay
from app.services.template_service import template_service


class KnowledgeService:
    @staticmethod
    async def create_entity(db: AsyncSession, scope: TemplateScope, data: EntityCreate) -> Entity:
        entity = Entity(
            tenant_id=scope.tenant_id, template_id=scope.template_id, entity_type=data.entity_type, name=data.name,
            identifier_key=data.identifier_key, identifier_value=data.identifier_value,
            attributes=data.attributes, confidence=1.0, created_by="human",
        )
        db.add(entity)
        await db.flush()
        db.add(AuditLog(
            tenant_id=scope.tenant_id, template_id=scope.template_id, event_type="ENTITY_CREATED",
            entity_type="Entity", entity_id=entity.id, actor=scope.user_id,
            details={"name": data.name, "entity_type": data.entity_type},
        ))
        await db.commit()
        await db.refresh(entity)
        return entity

    @staticmethod
    async def search_entities(
        db: AsyncSession,
        scope: TemplateScope,
        query: Optional[str] = None,
        entity_type: Optional[str] = None,
        identifier_value: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Entity]:
        stmt = select(Entity).where(Entity.template_id == scope.template_id)
        if entity_type:
            stmt = stmt.where(Entity.entity_type == entity_type)
        if identifier_value:
            stmt = stmt.where(Entity.identifier_value == identifier_value)
        if query:
            stmt = stmt.where(Entity.name.ilike(f"%{query}%"))
        stmt = stmt.order_by(Entity.created_at.desc()).offset(offset).limit(limit)
        return list((await db.execute(stmt)).scalars().all())

    @staticmethod
    async def get_entity(db: AsyncSession, scope: TemplateScope, entity_id: str) -> Entity:
        entity = await db.get(Entity, entity_id)
        if not entity or entity.template_id != scope.template_id:
            raise HTTPException(status_code=404, detail="Entity not found in this workspace.")
        return entity

    @staticmethod
    async def list_proposals(db: AsyncSession, scope: TemplateScope, status: Optional[str] = "PENDING") -> List[KnowledgeProposal]:
        stmt = select(KnowledgeProposal).where(KnowledgeProposal.template_id == scope.template_id)
        if status and status.upper() != "ALL":
            try:
                stmt = stmt.where(KnowledgeProposal.status == KnowledgeProposalStatus(status.upper()))
            except ValueError:
                raise HTTPException(status_code=400, detail="status must be PENDING, APPROVED, REJECTED or ALL")
        return list((await db.execute(stmt.order_by(KnowledgeProposal.created_at.desc()))).scalars().all())

    @staticmethod
    async def review_proposal(
        db: AsyncSession, scope: TemplateScope, proposal_id: str, action: str, notes: Optional[str] = None,
    ) -> KnowledgeProposal:
        """Approving applies the change. Either way the agent is told the decision."""
        proposal = await db.get(KnowledgeProposal, proposal_id)
        if not proposal or proposal.template_id != scope.template_id:
            raise HTTPException(status_code=404, detail="Proposal not found in this workspace.")
        if proposal.status != KnowledgeProposalStatus.PENDING:
            raise HTTPException(status_code=400, detail="Proposal has already been decided.")

        action = action.upper()
        if action not in ("APPROVE", "REJECT"):
            raise HTTPException(status_code=400, detail="action must be APPROVE or REJECT")

        applied = {}
        if action == "APPROVE":
            applied = await KnowledgeService._apply(db, scope, proposal)
            proposal.status = KnowledgeProposalStatus.APPROVED
        else:
            proposal.status = KnowledgeProposalStatus.REJECTED
            if proposal.proposal_type == KnowledgeProposalType.SOURCE_MAPPING.value:
                source = await db.get(KnowledgeSource, (proposal.proposed_data or {}).get("source_id"))
                if source and source.template_id == scope.template_id:
                    source.status = "MAPPING"  # back to the agent, with your notes in the decision event
        proposal.reviewed_by = scope.username
        proposal.reviewer_notes = notes
        proposal.reviewed_at = datetime.now(timezone.utc)

        db.add(AuditLog(
            tenant_id=scope.tenant_id, template_id=scope.template_id, event_type="PROPOSAL_DECIDED",
            entity_type="KnowledgeProposal", entity_id=proposal.id, actor=scope.user_id,
            details={"action": action, "proposal_type": proposal.proposal_type, "notes": notes, **applied},
        ))
        enqueue_agent_event(
            db, event_type="proposal.decided", tenant_id=scope.tenant_id, template_id=scope.template_id,
            payload={"proposal_id": proposal.id, "proposal_type": proposal.proposal_type,
                     "decision": proposal.status.value, "notes": notes, **applied},
            actor_id=scope.user_id, actor_type="user",
        )
        await db.commit()
        agent_event_relay.notify()
        await db.refresh(proposal)
        return proposal

    @staticmethod
    async def _apply(db: AsyncSession, scope: TemplateScope, proposal: KnowledgeProposal) -> dict:
        data = proposal.proposed_data or {}
        ptype = proposal.proposal_type
        if ptype == KnowledgeProposalType.TEMPLATE_CHANGE.value:
            version = await template_service.add_version(
                db, scope.template, data["schema_definition"],
                change_summary=data.get("change_summary") or proposal.observation[:500],
                created_by="agent", activate=True, approved_by=scope.user_id,
            )
            if data.get("name"):
                scope.template.name = data["name"]
            return {"activated_version_id": version.id, "version": version.version}

        if ptype == KnowledgeProposalType.ENTITY_CREATE.value:
            entity = Entity(
                tenant_id=scope.tenant_id, template_id=scope.template_id,
                entity_type=data.get("entity_type", "Entity"), name=data["name"],
                identifier_key=data.get("identifier_key"), identifier_value=data.get("identifier_value"),
                attributes=data.get("attributes", {}), confidence=1.0, created_by="agent",
            )
            db.add(entity)
            await db.flush()
            return {"entity_id": entity.id}

        if ptype == KnowledgeProposalType.ENTITY_MERGE.value:
            keep = await db.get(Entity, data["keep_entity_id"])
            if not keep or keep.template_id != scope.template_id:
                raise HTTPException(status_code=409, detail="Entity to keep no longer exists.")
            known = {a.alias.lower() for a in keep.aliases} | {keep.name.lower()}
            for other_id in data.get("merge_entity_ids", []):
                other = await db.get(Entity, other_id)
                if not other or other.template_id != scope.template_id or other.id == keep.id:
                    continue
                for alias in [other.name] + [a.alias for a in other.aliases]:
                    if alias.lower() not in known:
                        db.add(EntityAlias(entity_id=keep.id, template_id=scope.template_id, alias=alias, source=f"merge {proposal.id}"))
                        known.add(alias.lower())
                keep.attributes = {**(other.attributes or {}), **(keep.attributes or {})}
                for rel in (await db.execute(select(EntityRelationship).where(
                    (EntityRelationship.source_entity_id == other.id) | (EntityRelationship.target_entity_id == other.id)
                ))).scalars().all():
                    if rel.source_entity_id == other.id:
                        rel.source_entity_id = keep.id
                    if rel.target_entity_id == other.id:
                        rel.target_entity_id = keep.id
                await db.delete(other)
            return {"kept_entity_id": keep.id}

        if ptype == KnowledgeProposalType.SOURCE_MAPPING.value:
            source = await db.get(KnowledgeSource, data.get("source_id"))
            if not source or source.template_id != scope.template_id:
                raise HTTPException(status_code=409, detail="The data source no longer exists.")
            totals = await source_service.import_records(
                db, source, data["mapping"], schema=scope.template.schema_definition, actor=scope.user_id,
            )
            return {"source_id": source.id, "imported": totals}

        raise HTTPException(status_code=400, detail=f"Unknown proposal type '{ptype}'.")


knowledge_service = KnowledgeService()
