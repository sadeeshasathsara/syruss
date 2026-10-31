from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict


class ExtractionResponse(BaseModel):
    id: str
    tenant_id: str
    template_id: str
    template_version_id: Optional[str] = None
    document_id: str
    extracted_data: Dict[str, Any]
    overall_confidence: float
    # [{field, value, raw_text, confidence, entity_id, note}]
    evidence_records: List[Dict[str, Any]] = []
    validation: Dict[str, Any] = {}
    agent_notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
