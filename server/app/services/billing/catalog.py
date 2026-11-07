"""
The catalog: plans and add-ons, one row per version. Staff manage it in the system admin app; until they do, the three
plans that used to be in the code are version 1 (migration 0025 seeds the same rows).
"""
from datetime import datetime, timezone
from typing import List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.billing import CatalogItem

FREE = "free"  # the plan an organization has when nothing else is set, and the one a trial falls back to

DEFAULT_PLANS = [
    ("plan-free-1", FREE, "Free", 0, 0, {"pages_per_month": 200, "workspaces": None, "members_per_workspace": 3, "api_keys_per_workspace": 1,
                                       "webhooks_per_workspace": 1, "channels_per_workspace": 1, "destinations_per_workspace": 1}),
    ("plan-team-1", "team", "Team", 4900, 1, {"pages_per_month": 2000, "workspaces": None, "members_per_workspace": 15, "api_keys_per_workspace": 10,
                                            "webhooks_per_workspace": 5, "channels_per_workspace": 5, "destinations_per_workspace": 5}),
    ("plan-business-1", "business", "Business", 19900, 2, {"pages_per_month": 20000, "workspaces": None, "members_per_workspace": 100,
                                                         "api_keys_per_workspace": 50, "webhooks_per_workspace": 20, "channels_per_workspace": 25,
                                                         "destinations_per_workspace": 25}),
]


async def ensure_default_catalog(db: AsyncSession) -> None:
    """Adds version 1 of the default plans where they are missing (safe to run on every start; never changes a row)."""
    now = datetime.now(timezone.utc)
    for pid, key, name, price, sort, limits in DEFAULT_PLANS:
        if await db.get(CatalogItem, pid) is None:
            db.add(CatalogItem(id=pid, kind="plan", key=key, version=1, name=name, status="published", price_cents=price, currency="USD",
                               interval="month", public=True, sort=sort, limits=limits, created_by="system", created_at=now, published_at=now))
    await db.commit()


async def latest(db: AsyncSession, kind: str, key: str) -> Optional[CatalogItem]:
    """The newest published version of a plan or add-on."""
    return (await db.execute(select(CatalogItem).where(
        CatalogItem.kind == kind, CatalogItem.key == key, CatalogItem.status == "published",
    ).order_by(CatalogItem.version.desc()).limit(1))).scalar_one_or_none()


async def offered(db: AsyncSession, kind: str = "plan") -> List[CatalogItem]:
    """What customers can choose from: the newest published, public version of each, in the catalog's order."""
    rows = (await db.execute(select(CatalogItem).where(
        CatalogItem.kind == kind, CatalogItem.status == "published", CatalogItem.public.is_(True),
    ).order_by(CatalogItem.key, CatalogItem.version.desc()))).scalars().all()
    newest = {}
    for row in rows:
        newest.setdefault(row.key, row)
    return sorted(newest.values(), key=lambda r: (r.sort, r.name))
