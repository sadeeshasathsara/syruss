from pydantic import BaseModel, ConfigDict
from typing import Dict, Any, Optional
from datetime import datetime


class AuditLogResponse(BaseModel):
    id: str
    tenant_id: str
    template_id: Optional[str] = None
    event_type: str
    entity_type: str
    entity_id: str
    actor: str
    details: Dict[str, Any]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

