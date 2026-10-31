from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, Dict, Any, List
from datetime import datetime


class ChatMessageCreate(BaseModel):
    content: str = Field(..., min_length=1, description="User prompt or message text")
    context: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Active UI context: current_page, selected_template_id, etc.")


class ChatMessageResponse(BaseModel):
    id: str
    conversation_id: str
    role: str
    content: str
    evidence_citations: List[Any] = Field(default_factory=list)
    confidence: float = 1.0
    agent_trace: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ConversationCreate(BaseModel):
    title: Optional[str] = Field("New chat", description="Initial conversation title")
    context_metadata: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Initial page or template context")


class ConversationUpdate(BaseModel):
    title: str = Field(..., min_length=1, max_length=255, description="Updated conversation title")


class ConversationResponse(BaseModel):
    id: str
    tenant_id: str
    template_id: str
    user_id: str
    title: str
    context_metadata: Dict[str, Any] = Field(default_factory=dict)
    kind: str = "chat"
    status: Optional[str] = None
    audience: Optional[str] = None
    resolution: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    message_count: int = 0
    messages: Optional[List[ChatMessageResponse]] = None

    model_config = ConfigDict(from_attributes=True)
