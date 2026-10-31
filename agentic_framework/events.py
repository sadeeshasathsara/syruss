"""The event envelope published by the core server (see server/app/services/agent_events.py)."""
from typing import Any, Dict
from pydantic import BaseModel, Field


class AgentEvent(BaseModel):
    event_id: str
    type: str
    tenant_id: str
    template_id: str
    occurred_at: str
    actor: Dict[str, Any] = Field(default_factory=dict)
    payload: Dict[str, Any] = Field(default_factory=dict)
    agent_token: str = Field(..., repr=False)

    @property
    def workspace_key(self) -> str:
        return f"{self.tenant_id}:{self.template_id}"

    @property
    def document_id(self) -> str | None:
        return self.payload.get("document_id")

    @property
    def channel_id(self) -> str:
        """Where live progress for this run is shown."""
        return (
            self.payload.get("channel_id")
            or self.payload.get("document_id")
            or self.payload.get("conversation_id")
            or f"template:{self.template_id}"
        )
