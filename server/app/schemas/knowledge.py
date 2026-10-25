from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class EntityCreate(BaseModel):
    entity_type: str
    name: str
    identifier_key: Optional[str] = None
    identifier_value: Optional[str] = None
    attributes: Dict[str, Any] = Field(default_factory=dict)


class EntityResponse(BaseModel):
    id: str
    tenant_id: str
    template_id: str
    entity_type: str
    name: str
    identifier_key: Optional[str] = None
    identifier_value: Optional[str] = None
    attributes: Dict[str, Any]
    aliases: List[str] = []
    confidence: float
    created_by: str
    source_document_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @field_validator("aliases", mode="before")
    @classmethod
    def alias_strings(cls, value: Any) -> List[str]:
        return [a if isinstance(a, str) else a.alias for a in (value or [])]


class KnowledgeProposalResponse(BaseModel):
    id: str
    tenant_id: str
    template_id: str
    proposal_type: str
    observation: str
    proposed_data: Dict[str, Any]
    evidence_document_ids: List[str]
    evidence: Dict[str, Any] = {}
    status: str
    created_by: str
    reviewed_by: Optional[str] = None
    reviewer_notes: Optional[str] = None
    created_at: datetime
    reviewed_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class KnowledgeProposalReviewRequest(BaseModel):
    action: str = Field(..., description="APPROVE or REJECT")
    notes: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def accept_decision_alias(cls, data: Any) -> Any:
        if isinstance(data, dict) and "action" not in data and "decision" in data:
            data = {**data, "action": data["decision"]}
        return data


class UserHelperAskRequest(BaseModel):
    question: str
    session_id: Optional[str] = Field(None, description="Live channel for the agent's reply")


class UserHelperAskResponse(BaseModel):
    session_id: str
    conversation_id: str
    status: str = "QUEUED"
    answer: str = ""
