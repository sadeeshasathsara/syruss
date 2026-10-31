from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, Dict, Any
from datetime import datetime


class TemplateBase(BaseModel):
    name: str = Field(...)
    document_type: Optional[str] = Field(None, description="Document type tag, e.g. attendance_sheet, invoice")
    description: Optional[str] = Field(None)
    schema_definition: Dict[str, Any] = Field(..., description="JSON schema defining objects, fields, and types")


class TemplateCreate(TemplateBase):
    id: Optional[str] = Field(None, description="Draft workspace created by /templates/discover to finalize")


class TemplateUpdate(BaseModel):
    name: Optional[str] = None
    document_type: Optional[str] = None
    description: Optional[str] = None
    schema_definition: Optional[Dict[str, Any]] = None


class TemplateResponse(BaseModel):
    id: str
    tenant_id: str
    name: str
    document_type: str
    slug: Optional[str] = None
    workspace_slug: Optional[str] = None
    description: Optional[str] = None
    schema_definition: Dict[str, Any]
    active_version_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ChecklistItem(BaseModel):
    field: str
    label: str
    description: Optional[str] = None
    selected: bool = True


class TemplateDiscoveryResponse(BaseModel):
    template_id: str
    name: str = "Discovered Template"
    document_type: str = "General"
    description: Optional[str] = None
    objects: list[Dict[str, Any]] = Field(default_factory=list, description="Detected standard and custom business objects")
    relationships: list[Dict[str, Any]] = Field(default_factory=list, description="Detected entity relationships")
    user_checklist: list[ChecklistItem] = Field(default_factory=list, description="User-friendly extraction field checklist per Section 7 of PROCESS.md")
    schema_definition: Dict[str, Any] = Field(default_factory=dict, description="Ready-to-use JSON Schema")
    validation_rules: list[Dict[str, Any]] = Field(default_factory=list, description="Pre-compiled declarative validation rules")
    session_id: Optional[str] = None
    status: Optional[str] = "QUEUED"
    agent_questions: list[str] = Field(default_factory=list, description="Clarification questions from discovery agent")


class TemplateChatRequest(BaseModel):
    session_id: str
    template_id: Optional[str] = None
    message: str
    schema_definition: Optional[Dict[str, Any]] = None
    validation_rules: Optional[list[Dict[str, Any]]] = None


class TemplateChatResponse(BaseModel):
    reply: str
    updated_schema: Optional[Dict[str, Any]] = None
    updated_rules: Optional[list[Dict[str, Any]]] = None


class TemplateVersionCreate(BaseModel):
    version: str = Field(..., description="SemVer version tag, e.g. 1.0.0, 1.1.0")
    schema_definition: Dict[str, Any] = Field(..., description="JSON schema defining entities, objects, and types")
    validation_rules: list[Dict[str, Any]] = Field(default_factory=list, description="Declarative AST validation rules")
    confidence_policy: Dict[str, Any] = Field(default_factory=dict, description="Field routing and review trigger policies")
    change_summary: Optional[str] = Field(None, description="Changelog description of this version")


class TemplateVersionResponse(BaseModel):
    id: str
    template_id: str
    version: str
    status: str
    schema_definition: Dict[str, Any]
    validation_rules: list[Dict[str, Any]]
    confidence_policy: Dict[str, Any]
    benchmark_score: Optional[Dict[str, Any]] = None
    change_summary: Optional[str] = None
    created_by: str
    approved_by: Optional[str] = None
    created_at: datetime
    activated_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class TemplatePromotionRequest(BaseModel):
    target_status: str = Field("ACTIVE", description="Target lifecycle state: STAGED, ACTIVE, ARCHIVED")
    approval_notes: Optional[str] = None
    skip_benchmark: bool = Field(False, description="Whether to bypass automated golden dataset gate (admin override)")


class GoldenDatasetItemCreate(BaseModel):
    name: str = Field(..., description="Document test case name, e.g. clean_roster_sample_1")
    storage_path: str = Field(..., description="MinIO/S3 object key")
    ground_truth_json: Dict[str, Any] = Field(..., description="Expected extracted JSON dictionary")
    complexity_tag: str = Field("standard", description="standard, noisy, rotated, handwritten")


class GoldenDatasetItemResponse(BaseModel):
    id: str
    template_id: str
    tenant_id: str
    name: str
    storage_path: str
    ground_truth_json: Dict[str, Any]
    complexity_tag: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TemplateBenchmarkResponse(BaseModel):
    version_id: str
    status: str
    item_count: int
    evaluated_count: int = 0
    precision: float
    recall: float
    f1_score: float
    exact_match_rate: float
    average_cer: float
    rule_pass_rate: float
    failed_count: int
    execution_duration_ms: float
    results: list[Dict[str, Any]] = Field(default_factory=list)


class TemplateManifestExport(BaseModel):
    api_version: str = "docai.enterprise.io/v1"
    kind: str = "DocumentTemplate"
    metadata: Dict[str, Any]
    spec: Dict[str, Any]




