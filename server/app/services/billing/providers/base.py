"""
A payment provider: how customers pay, as an adapter. The billing API and the admin app call only these methods, so
connecting a provider (Paddle, Stripe, ...) is one more Provider registered here, and nothing else changes:

    start_purchase   a customer wants a plan, an add-on or credits: a page to pay on, or a request staff complete
    portal           where a customer manages their payment method and invoices, when the provider has one
    handle_webhook   the provider telling us a payment went through, a subscription changed or a payment failed
    publish          staff published a catalog item: create its product and price at the provider
"""
from dataclasses import dataclass
from typing import Dict, Optional
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.models.billing import CatalogItem


@dataclass
class Buyer:
    tenant_id: str
    user_id: str
    name: str
    email: Optional[str] = None


@dataclass
class Purchase:
    """What the customer is told next: go and pay (url), or wait while their request is handled (request_id)."""
    status: str                       # "requested" | "redirect"
    message: str
    url: Optional[str] = None
    request_id: Optional[str] = None


class Provider:
    kind: str = ""
    label: str = ""
    takes_payments: bool = False      # customers pay in the app (rather than staff recording payments)

    async def start_purchase(self, db: AsyncSession, buyer: Buyer, item: CatalogItem, quantity: int, note: Optional[str]) -> Purchase:
        raise NotImplementedError

    async def portal(self, db: AsyncSession, tenant_id: str) -> Optional[str]:
        return None

    async def handle_webhook(self, request: Request) -> None:
        raise NotImplementedError

    async def publish(self, db: AsyncSession, item: CatalogItem) -> None:
        return None

    def describe(self) -> Dict[str, object]:
        return {"kind": self.kind, "label": self.label, "takes_payments": self.takes_payments}


PROVIDERS: Dict[str, Provider] = {}


def register(provider: Provider) -> Provider:
    PROVIDERS[provider.kind] = provider
    return provider


def provider() -> Provider:
    """The provider this deployment uses (BILLING_PROVIDER)."""
    try:
        return PROVIDERS[settings.BILLING_PROVIDER]
    except KeyError:
        raise RuntimeError(f"Unknown BILLING_PROVIDER '{settings.BILLING_PROVIDER}'. Known: {', '.join(sorted(PROVIDERS))}.")
