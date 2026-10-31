from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, Field


class OrganizationResponse(BaseModel):
    id: str
    name: str
    slug: str


class WorkspaceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=2000)


class WorkspaceUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = Field(None, max_length=2000)


class WorkspaceSummary(BaseModel):
    id: str
    slug: str
    name: str
    description: Optional[str] = None
    document_type: str
    # draft: the template is still being designed; no documents can be processed yet
    status: Literal["draft", "active"]
    documents: int = 0
    needs_review: int = 0
    open_questions: int = 0
    pending_proposals: int = 0
    # Conversations Syruss opened that are still open (the agent inbox)
    open_conversations: int = 0
    # The caller's role id in this workspace; null for an organization admin who isn't a member (can manage, can't open)
    my_role: Optional[str] = None
    my_role_name: Optional[str] = None
    created_at: datetime
    updated_at: datetime
