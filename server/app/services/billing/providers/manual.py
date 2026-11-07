"""
No payment provider: a customer's purchase becomes a billing request, and platform staff complete it from the admin app,
recording the payment they received (by invoice or bank transfer). Nothing is charged in the app.
"""
from typing import Optional
from fastapi import HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.billing import CatalogItem
from app.services.billing import service as billing
from app.services.billing.providers.base import Buyer, Provider, Purchase, register


class ManualProvider(Provider):
    kind, label = "manual", "Invoices from Syruss"

    async def start_purchase(self, db: AsyncSession, buyer: Buyer, item: CatalogItem, quantity: int, note: Optional[str]) -> Purchase:
        request = await billing.create_request(db, buyer.tenant_id, item, quantity=quantity, note=note, user_id=buyer.user_id, user_name=buyer.name)
        return Purchase(status="requested", request_id=request.id,
                        message="Your request is with Syruss. We'll send an invoice and switch it on when it's paid.")

    async def handle_webhook(self, request: Request) -> None:
        raise HTTPException(status_code=404, detail="No payment provider is connected.")


register(ManualProvider())
