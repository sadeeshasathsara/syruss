"""
Who an invoice is from and who it is to: Syruss as the seller (billing settings, its bank accounts and the tax rates
staff keep) and the organization (its billing profile). An invoice copies both in when it is issued.
"""
from typing import Any, Dict, List, Optional
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.billing import BankAccount, BillingProfile, BillingSettings, TaxRate
from app.services.billing.tax import Rate

SELLER_REQUIRED = {"legal_name": "legal name", "address": "address", "country": "country", "email": "billing email"}
PROFILE_REQUIRED = {"legal_name": "legal name", "email": "billing email", "address_line1": "address", "city": "city", "country": "country"}


class NotReady(ValueError):
    """An invoice can't be issued yet: one side's billing details are missing (`who`: "seller" or "buyer")."""

    def __init__(self, who: str, missing: List[str], message: str):
        super().__init__(message)
        self.who, self.missing = who, missing


async def seller(db: AsyncSession) -> BillingSettings:
    """Syruss's billing settings (the one row, made empty the first time)."""
    row = await db.get(BillingSettings, 1)
    if row is None:
        await db.execute(insert(BillingSettings).values(id=1, legal_name="", address="", country="", email="", invoice_prefix="SYR",
                                                        payment_terms_days=14, renewal_lead_days=7, grace_days=7, reminder_days=[3, 0, -3])
                         .on_conflict_do_nothing(index_elements=["id"]))
        row = await db.get(BillingSettings, 1)
    return row


def seller_missing(s: BillingSettings) -> List[str]:
    return [label for field, label in SELLER_REQUIRED.items() if not (getattr(s, field) or "").strip()]


def seller_snapshot(s: BillingSettings) -> Dict[str, Any]:
    return {"legal_name": s.legal_name, "address": s.address, "country": s.country, "tax_id": s.tax_id, "email": s.email, "footer": s.footer}


async def profile(db: AsyncSession, tenant_id: str) -> Optional[BillingProfile]:
    return await db.get(BillingProfile, tenant_id)


def profile_missing(p: Optional[BillingProfile]) -> List[str]:
    if p is None:
        return list(PROFILE_REQUIRED.values())
    return [label for field, label in PROFILE_REQUIRED.items() if not (getattr(p, field) or "").strip()]


def buyer_snapshot(p: BillingProfile) -> Dict[str, Any]:
    return {"legal_name": p.legal_name, "email": p.email, "address_line1": p.address_line1, "address_line2": p.address_line2, "city": p.city,
            "region": p.region, "postal_code": p.postal_code, "country": p.country, "tax_id": p.tax_id, "po_number": p.po_number}


def bank_dict(a: BankAccount) -> Dict[str, Any]:
    return {"id": a.id, "bank": a.bank, "account_name": a.account_name, "account_number": a.account_number, "branch": a.branch,
            "swift": a.swift, "currency": a.currency, "instructions": a.instructions, "active": a.active}


async def bank_accounts(db: AsyncSession, currency: Optional[str] = None) -> List[BankAccount]:
    """The active accounts customers pay into, in one currency when given."""
    stmt = select(BankAccount).where(BankAccount.active.is_(True)).order_by(BankAccount.created_at)
    return list((await db.execute(stmt.where(BankAccount.currency == currency.upper()) if currency else stmt)).scalars().all())


async def rates(db: AsyncSession) -> List[Rate]:
    rows = (await db.execute(select(TaxRate).where(TaxRate.active.is_(True)).order_by(TaxRate.created_at))).scalars().all()
    return [Rate(r.country, r.name, r.rate_bp, r.reverse_charge_with_tax_id) for r in rows]


async def ready(db: AsyncSession, tenant_id: str) -> None:
    """Refuses (NotReady) when either side's billing details are incomplete."""
    missing = seller_missing(await seller(db))
    if missing:
        raise NotReady("seller", missing, "Syruss isn't ready to invoice yet: its billing details aren't filled in. Please try again later.")
    missing = profile_missing(await profile(db, tenant_id))
    if missing:
        raise NotReady("buyer", missing, f"Add the organization's billing details first ({', '.join(missing)}): invoices are made out to them.")
