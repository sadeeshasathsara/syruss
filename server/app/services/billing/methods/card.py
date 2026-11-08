"""
Card, through Stripe: the customer pays an invoice on Stripe's own Checkout page (card details never reach Syruss), and
Stripe tells us it went through by a signed webhook event, which records the payment. A customer can keep the card for
renewals, which are then charged by themselves; Stripe's billing portal manages the kept card.

The calls to Stripe are the small functions at the bottom (a refund among them), and nothing else in the code talks to Stripe.
"""
from datetime import datetime, timezone
from typing import Any, Dict, Optional
import stripe
from app.core.config import settings
from app.models.billing import Invoice
from app.services.billing.methods.base import Method, register


class Card(Method):
    key, label, online = "card", "Card", True

    def available(self) -> bool:
        return bool(settings.STRIPE_SECRET_KEY and settings.STRIPE_WEBHOOK_SECRET)

    def instructions(self, invoice: Invoice) -> Optional[Dict[str, Any]]:
        return {"text": "Pay by card on Stripe's secure page. It's switched on as soon as the payment goes through."}


register(Card())


# ---- Stripe ------------------------------------------------------------------------------------

def _key() -> str:
    return settings.STRIPE_SECRET_KEY


def create_checkout(*, amount_cents: int, currency: str, name: str, invoice_id: str, tenant_id: str, customer: Optional[str],
                    email: Optional[str], save_card: bool, success_url: str, cancel_url: str) -> Dict[str, Any]:
    """A Checkout page for one amount. Returns {"id", "url", "expires_at"} (a timestamp)."""
    metadata = {"invoice_id": invoice_id, "tenant_id": tenant_id, "via": "checkout"}
    intent: Dict[str, Any] = {"metadata": metadata}
    if save_card:
        intent["setup_future_usage"] = "off_session"
    params: Dict[str, Any] = {
        "mode": "payment", "client_reference_id": invoice_id, "metadata": metadata, "payment_intent_data": intent,
        "line_items": [{"quantity": 1, "price_data": {"currency": currency.lower(), "unit_amount": amount_cents, "product_data": {"name": name}}}],
        "success_url": success_url, "cancel_url": cancel_url,
    }
    if customer:
        params["customer"] = customer
    elif email:
        params["customer_email"] = email
    if save_card and not customer:
        params["customer_creation"] = "always"
    session = stripe.checkout.Session.create(api_key=_key(), **params)
    return {"id": session["id"], "url": session["url"], "expires_at": session["expires_at"]}


def card_of(payment_intent_id: str) -> Dict[str, Optional[str]]:
    """The customer and card a payment was made with: {"customer", "payment_method", "label"}."""
    intent = stripe.PaymentIntent.retrieve(payment_intent_id, api_key=_key(), expand=["payment_method"])
    method = intent.get("payment_method") or {}
    card = method.get("card") or {}
    label = f"{str(card.get('brand', 'card')).title()} ending {card.get('last4')}" if card.get("last4") else None
    return {"customer": intent.get("customer"), "payment_method": method.get("id"), "label": label}


def charge(*, amount_cents: int, currency: str, customer: str, payment_method: str, invoice_id: str, tenant_id: str) -> Dict[str, Any]:
    """Charges a kept card without the customer present. Returns {"id", "status", "amount_received"}; a declined card raises."""
    intent = stripe.PaymentIntent.create(api_key=_key(), amount=amount_cents, currency=currency.lower(), customer=customer,
                                         payment_method=payment_method, off_session=True, confirm=True,
                                         metadata={"invoice_id": invoice_id, "tenant_id": tenant_id, "via": "saved_card"})
    return {"id": intent["id"], "status": intent["status"], "amount_received": intent.get("amount_received") or 0}


def refund(*, payment_intent: str, amount_cents: int) -> str:
    """Gives back part or all of a card payment. Returns Stripe's refund id."""
    return stripe.Refund.create(api_key=_key(), payment_intent=payment_intent, amount=amount_cents)["id"]


def portal(*, customer: str, return_url: str) -> str:
    return stripe.billing_portal.Session.create(api_key=_key(), customer=customer, return_url=return_url)["url"]


def event_from(payload: bytes, signature: str) -> Dict[str, Any]:
    """The event Stripe sent, once its signature checks out (ValueError otherwise)."""
    try:
        return stripe.Webhook.construct_event(payload, signature, settings.STRIPE_WEBHOOK_SECRET).to_dict()
    except (stripe.SignatureVerificationError, ValueError) as exc:
        raise ValueError("The event isn't signed by Stripe.") from exc


def when(timestamp: int) -> datetime:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc)
