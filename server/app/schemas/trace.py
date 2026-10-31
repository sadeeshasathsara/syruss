from typing import Optional, Dict, Any, List
from datetime import datetime
from pydantic import BaseModel, ConfigDict


class ToolCallRecordSchema(BaseModel):
    tool_call_id: Optional[str] = None
    tool_name: str
    arguments: Dict[str, Any] = {}
    observation: Any = None
    duration_ms: Optional[float] = None
    status: str = "success"


class AgentStepSchema(BaseModel):
    step_number: int
    step_started_at: str
    step_completed_at: Optional[str] = None
    duration_ms: Optional[float] = None
    thought: Optional[str] = None
    tool_calls: List[ToolCallRecordSchema] = []


class AgentExecutionTraceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    template_id: Optional[str] = None
    document_id: Optional[str] = None
    profile: str
    model: str
    status: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    total_duration_ms: Optional[float] = None
    steps: List[Dict[str, Any]] = []
    final_thought: Optional[str] = None
    usage: Dict[str, Any] = {}
    error_message: Optional[str] = None


class AgentTraceSummaryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tenant_id: str
    document_id: Optional[str] = None
    profile: str
    model: str
    status: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    total_duration_ms: Optional[float] = None
    step_count: int = 0
    tool_call_count: int = 0
    final_thought: Optional[str] = None
    error_message: Optional[str] = None


class AgentTraceStatsResponse(BaseModel):
    total_threads: int = 0
    completed_threads: int = 0
    needs_review_threads: int = 0
    failed_threads: int = 0
    running_threads: int = 0
    avg_duration_ms: float = 0.0
    total_tool_calls: int = 0
    profiles: Dict[str, int] = {}
    models: Dict[str, int] = {}


class AgentTraceListResponse(BaseModel):
    stats: AgentTraceStatsResponse
    traces: List[AgentTraceSummaryResponse]
    total: int
    limit: int
    offset: int

