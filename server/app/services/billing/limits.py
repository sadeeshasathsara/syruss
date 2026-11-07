"""
Every limit a plan can set, and how much of each is used. Plans, add-ons and overrides name limits by these keys, and
only what is registered here is enforced, so nobody can sell a limit the platform doesn't keep. The admin app's catalog
editor lists them from describe().

Adding a limit: a subclass with its used(), then register() it. Plans that don't name it leave it unlimited, so a new
limit never takes anything away from a customer until staff put it in a plan version.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.billing import UsageEvent
from app.models.delivery import Destination
from app.models.intake import IntakeChannel
from app.models.workspace import Workspace
from app.models.workspace_admin import ApiKey, Webhook, WorkspaceMember


@dataclass
class Where:
    """What a limit is counted for: the organization, the workspace (for a workspace limit) and the current period."""
    tenant_id: str
    template_id: Optional[str]
    period_start: datetime


class Limit:
    key: str = ""
    label: str = ""                # for staff: "Pages read each month"
    noun: str = ""                 # in a sentence: "The Free plan allows 3 {noun}"
    scope: str = "workspace"       # workspace: each workspace has this many | organization: shared by all of them
    metered: bool = False          # used up over the period, rather than a count of things that exist

    async def used(self, db: AsyncSession, where: Where) -> int:
        raise NotImplementedError

    def describe(self) -> Dict[str, Any]:
        return {"key": self.key, "label": self.label, "noun": self.noun, "scope": self.scope, "metered": self.metered}


LIMITS: Dict[str, Limit] = {}


def register(limit: Limit) -> Limit:
    LIMITS[limit.key] = limit
    return limit


async def _count(db: AsyncSession, stmt) -> int:
    return (await db.execute(stmt)).scalar() or 0


class PagesPerMonth(Limit):
    key, label, noun, scope, metered = "pages_per_month", "Pages read each month", "pages this month", "organization", True

    async def used(self, db: AsyncSession, where: Where) -> int:
        """Pages taken from the allowance this period (pages paid with credits aren't)."""
        return await _count(db, select(func.coalesce(func.sum(UsageEvent.quantity - UsageEvent.from_credits), 0)).where(
            UsageEvent.tenant_id == where.tenant_id, UsageEvent.metric == "pages", UsageEvent.period_start == where.period_start))


class Workspaces(Limit):
    key, label, noun, scope = "workspaces", "Workspaces", "workspaces", "organization"

    async def used(self, db: AsyncSession, where: Where) -> int:
        return await _count(db, select(func.count(Workspace.id)).where(Workspace.tenant_id == where.tenant_id))


class MembersPerWorkspace(Limit):
    key, label, noun = "members_per_workspace", "Members in each workspace", "members"

    async def used(self, db: AsyncSession, where: Where) -> int:
        return await _count(db, select(func.count(WorkspaceMember.id)).where(WorkspaceMember.template_id == where.template_id))


class ApiKeysPerWorkspace(Limit):
    key, label, noun = "api_keys_per_workspace", "API keys in each workspace", "API keys"

    async def used(self, db: AsyncSession, where: Where) -> int:
        return await _count(db, select(func.count(ApiKey.id)).where(
            ApiKey.template_id == where.template_id, ApiKey.revoked_at.is_(None), ApiKey.scope != "intake"))


class WebhooksPerWorkspace(Limit):
    key, label, noun = "webhooks_per_workspace", "Webhooks in each workspace", "webhooks"

    async def used(self, db: AsyncSession, where: Where) -> int:
        return await _count(db, select(func.count(Webhook.id)).where(Webhook.template_id == where.template_id))


class ChannelsPerWorkspace(Limit):
    key, label, noun = "channels_per_workspace", "Intake channels in each workspace", "intake channels"

    async def used(self, db: AsyncSession, where: Where) -> int:
        return await _count(db, select(func.count(IntakeChannel.id)).where(IntakeChannel.template_id == where.template_id))


class DestinationsPerWorkspace(Limit):
    key, label, noun = "destinations_per_workspace", "Destinations in each workspace", "destinations"

    async def used(self, db: AsyncSession, where: Where) -> int:
        return await _count(db, select(func.count(Destination.id)).where(Destination.template_id == where.template_id))


for _limit in (PagesPerMonth(), Workspaces(), MembersPerWorkspace(), ApiKeysPerWorkspace(), WebhooksPerWorkspace(),
               ChannelsPerWorkspace(), DestinationsPerWorkspace()):
    register(_limit)
