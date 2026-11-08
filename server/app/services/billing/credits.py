"""
Prepaid credits: pages an organization bought or was given, used once its plan's monthly pages run out. They don't
expire and don't renew. The ledger only grows; the balance is its sum.
"""
from typing import List, Optional
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.billing import CREDIT_KINDS, CreditEntry


async def balance(db: AsyncSession, tenant_id: str) -> int:
    return int((await db.execute(select(func.coalesce(func.sum(CreditEntry.delta), 0)).where(CreditEntry.tenant_id == tenant_id))).scalar() or 0)


async def add(db: AsyncSession, *, tenant_id: str, delta: int, kind: str, created_by: str, reason: Optional[str] = None,
              ref: Optional[str] = None) -> CreditEntry:
    """One entry: + for a purchase or a grant, - for usage; refunds and corrections either way. Never below zero."""
    if kind not in CREDIT_KINDS:
        raise ValueError(f"Unknown credit entry: {kind}")
    if delta == 0:
        raise ValueError("A credit entry changes the balance")
    if delta < 0 and await balance(db, tenant_id) + delta < 0:
        raise ValueError("That would take the credits below zero")
    entry = CreditEntry(tenant_id=tenant_id, delta=delta, kind=kind, reason=reason, ref=ref, created_by=created_by)
    db.add(entry)
    await db.flush()
    return entry


async def history(db: AsyncSession, tenant_id: str, limit: int = 50) -> List[CreditEntry]:
    return list((await db.execute(select(CreditEntry).where(CreditEntry.tenant_id == tenant_id)
                                  .order_by(CreditEntry.created_at.desc()).limit(limit))).scalars().all())
