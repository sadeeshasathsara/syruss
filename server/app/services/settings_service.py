"""
Workspace settings, plans and usage.

Settings are typed and validated here; anything not stored takes its default, so adding a
setting never needs a migration. Several settings are enforced by the server, not only shown:
intake limits on upload, the agent's commit threshold and what it may learn on its own, its
monthly budget (app.services.budget_service), and the plan's monthly document limit.
"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from fastapi import HTTPException
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.document import Document, DocumentRole
from app.models.knowledge import Entity
from app.models.trace import AgentExecutionTrace
from app.models.workspace_admin import ApiKey, Webhook, WorkspaceMember, WorkspaceSettings

IMAGE_TYPES = ["image/jpeg", "image/png", "image/webp", "image/tiff"]


class GeneralPrefs(BaseModel):
    timezone: str = "UTC"
    date_format: Literal["YYYY-MM-DD", "DD/MM/YYYY", "MM/DD/YYYY"] = "YYYY-MM-DD"
    language: str = "en"


class AgentPrefs(BaseModel):
    instructions: str = Field("", max_length=4000, description="Standing instructions the agent reads every time it wakes up")
    may_register_entities: bool = True
    may_learn_aliases: bool = True
    reflection: Literal["off", "daily", "weekly"] = Field(
        "weekly", description="How often the agent looks back over recent work and corrections to learn from them")
    insights: Literal["off", "weekly"] = Field(
        "off", description="How often the agent looks through the saved records and suggests custom screens worth having")
    # Looking at a page costs a model call with an image; records are already in the database.
    pages_per_chat_request: int = Field(3, ge=0, le=20, description="Pages the agent may look at to answer one chat message")
    pages_per_person_per_day: int = Field(20, ge=0, le=500, description="Pages it may look at for one person's chats in a day")
    past_pages_per_run: int = Field(2, ge=0, le=20, description="Already-recorded documents it may look at again while working")


class BudgetPrefs(BaseModel):
    monthly_cost_usd: Optional[float] = Field(None, gt=0, description="Spending cap for the agent's model calls each month")
    monthly_tokens: Optional[int] = Field(None, gt=0, description="Token cap (input + output) each month")
    when_reached: Literal["warn", "pause"] = "warn"


class DocumentPrefs(BaseModel):
    auto_process: bool = True
    max_file_mb: int = Field(10, ge=1, le=50)
    allowed_types: List[str] = Field(default_factory=lambda: list(IMAGE_TYPES))
    commit_threshold: float = Field(0.8, ge=0.0, le=1.0, description="Below this confidence the agent must send to review")


class WorkspaceSettingsData(BaseModel):
    general: GeneralPrefs = Field(default_factory=GeneralPrefs)
    agent: AgentPrefs = Field(default_factory=AgentPrefs)
    documents: DocumentPrefs = Field(default_factory=DocumentPrefs)
    budget: BudgetPrefs = Field(default_factory=BudgetPrefs)
    plan: Literal["free", "team", "business"] = "free"


PLANS: Dict[str, Dict[str, Any]] = {
    "free": {"name": "Free", "price_per_month": 0, "documents_per_month": 200, "members": 3, "api_keys": 1, "webhooks": 1, "channels": 1, "destinations": 1},
    "team": {"name": "Team", "price_per_month": 49, "documents_per_month": 2000, "members": 15, "api_keys": 10, "webhooks": 5, "channels": 5, "destinations": 5},
    "business": {"name": "Business", "price_per_month": 199, "documents_per_month": 20000, "members": 100, "api_keys": 50, "webhooks": 20, "channels": 25, "destinations": 25},
}


async def get_settings(db: AsyncSession, template_id: str) -> WorkspaceSettingsData:
    row = await db.get(WorkspaceSettings, template_id)
    return WorkspaceSettingsData.model_validate(row.data if row else {})


async def update_settings(db: AsyncSession, template_id: str, patch: Dict[str, Any], user_id: str) -> WorkspaceSettingsData:
    """Merges a partial update section by section; the plan is not changed here."""
    current = (await get_settings(db, template_id)).model_dump()
    for section in ("general", "agent", "documents", "budget"):
        if isinstance(patch.get(section), dict):
            current[section] = {**current[section], **patch[section]}
    try:
        merged = WorkspaceSettingsData.model_validate(current)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=[{"field": ".".join(map(str, e["loc"])), "message": e["msg"]} for e in exc.errors()]) from exc
    row = await db.get(WorkspaceSettings, template_id)
    if row is None:
        row = WorkspaceSettings(template_id=template_id, data={})
        db.add(row)
    row.data = merged.model_dump()
    row.updated_by = user_id
    return merged


def month_start() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def usage(db: AsyncSession, template_id: str) -> Dict[str, Any]:
    """What the workspace has used, counted from its real records."""
    def count(stmt):
        return db.execute(stmt)

    # Documents sent with a test key are for trying things out: they are not counted against the plan.
    docs = select(func.count(Document.id)).where(Document.template_id == template_id, Document.role == DocumentRole.DOCUMENT, Document.is_test.is_(False))
    return {
        "documents_this_month": (await count(docs.where(Document.created_at >= month_start()))).scalar() or 0,
        "documents_total": (await count(docs)).scalar() or 0,
        "storage_bytes": (await count(select(func.coalesce(func.sum(Document.file_size_bytes), 0)).where(Document.template_id == template_id))).scalar() or 0,
        "members": (await count(select(func.count(WorkspaceMember.id)).where(WorkspaceMember.template_id == template_id))).scalar() or 0,
        "api_keys": (await count(select(func.count(ApiKey.id)).where(
            ApiKey.template_id == template_id, ApiKey.revoked_at.is_(None), ApiKey.scope != "intake"))).scalar() or 0,
        "entities": (await count(select(func.count(Entity.id)).where(Entity.template_id == template_id))).scalar() or 0,
        "agent_runs_this_month": (await count(select(func.count(AgentExecutionTrace.id)).where(
            AgentExecutionTrace.template_id == template_id, AgentExecutionTrace.started_at >= month_start(),
        ))).scalar() or 0,
        **await agent_spend(db, template_id),
        "period_start": month_start(),
    }


async def agent_spend(db: AsyncSession, template_id: str) -> Dict[str, Any]:
    """This month's model tokens, and their cost where every run's model has a price set."""
    row = (await db.execute(select(
        func.coalesce(func.sum(AgentExecutionTrace.input_tokens), 0),
        func.coalesce(func.sum(AgentExecutionTrace.output_tokens), 0),
        func.sum(AgentExecutionTrace.cost_usd),
        func.count(AgentExecutionTrace.id) - func.count(AgentExecutionTrace.cost_usd),
    ).where(AgentExecutionTrace.template_id == template_id, AgentExecutionTrace.started_at >= month_start()))).one()
    return {"agent_input_tokens_this_month": int(row[0]), "agent_output_tokens_this_month": int(row[1]),
            "agent_cost_usd_this_month": float(row[2]) if row[2] is not None else None,
            "agent_runs_without_price": int(row[3])}


async def check_limit(db: AsyncSession, template_id: str, resource: str, adding: int = 1) -> None:
    """Refuses an action that would take the workspace past its plan's limit."""
    plan_key = (await get_settings(db, template_id)).plan
    plan = PLANS[plan_key]
    used = await usage(db, template_id)
    current = {"documents_per_month": used["documents_this_month"], "members": used["members"], "api_keys": used["api_keys"]}.get(resource)
    if resource == "webhooks":
        current = (await db.execute(select(func.count(Webhook.id)).where(Webhook.template_id == template_id))).scalar() or 0
    if resource == "channels":
        from app.models.intake import IntakeChannel
        current = (await db.execute(select(func.count(IntakeChannel.id)).where(IntakeChannel.template_id == template_id))).scalar() or 0
    if resource == "destinations":
        from app.models.delivery import Destination
        current = (await db.execute(select(func.count(Destination.id)).where(Destination.template_id == template_id))).scalar() or 0
    limit = plan[resource]
    if current is not None and current + adding > limit:
        what = {"documents_per_month": "documents this month", "members": "members", "api_keys": "API keys", "webhooks": "webhooks",
                "channels": "intake channels", "destinations": "destinations"}[resource]
        raise HTTPException(status_code=402, detail=f"The {plan['name']} plan allows {limit} {what}; this would make {current + adding}.")
