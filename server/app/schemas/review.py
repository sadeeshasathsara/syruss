from pydantic import BaseModel, Field, ConfigDict, model_validator
from typing import Optional, Dict, Any, List
from datetime import datetime
from app.models.review import ReviewStatus


class ReviewTaskResponse(BaseModel):
    id: str
    tenant_id: str
    template_id: str
    document_id: str
    reason: str
    uncertain_fields: List[Dict[str, Any]] = []
    status: ReviewStatus
    original_data: Dict[str, Any]
    corrected_data: Optional[Dict[str, Any]] = None
    reviewer_notes: Optional[str] = None
    decided_by: Optional[str] = None
    decided_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)



class ReviewDecisionRequest(BaseModel):
    action: str = Field(..., description="'APPROVE', 'CORRECT', 'REJECT'")
    corrected_data: Optional[Dict[str, Any]] = Field(None, description="Corrected data dictionary if action is CORRECT")
    notes: Optional[str] = Field(None, description="Reviewer comments or explanations")

    model_config = ConfigDict(populate_by_name=True)

    @model_validator(mode="before")
    @classmethod
    def normalize_aliases(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # Convert to mutable dict if needed
            d = dict(data)
            # Accept 'decision' as alias for 'action'
            if "decision" in d and "action" not in d:
                d["action"] = d["decision"]
            # Accept 'reviewer_notes' as alias for 'notes'
            if "reviewer_notes" in d and "notes" not in d:
                d["notes"] = d["reviewer_notes"]
            return d
        return data
