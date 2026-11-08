"""
A way an invoice gets paid, as an adapter. The invoice is always the same; only how the money arrives differs:

    instructions(invoice)   what the customer is told on the invoice (the bank accounts to pay into, ...)
    customer_can_use        whether customers pay this way themselves (cash or a cheque is recorded by staff)
    needs_proof             the customer sends proof (a bank slip) that staff check before the payment counts
    online                  paid in the app (a card), confirmed by the processor rather than by staff

The billing API and the admin app use only these, so adding a way to pay is one more Method registered here.
BILLING_METHODS says which ones this deployment offers.
"""
from typing import Any, Dict, List, Optional
from app.core.config import settings
from app.models.billing import Invoice


class Method:
    key: str = ""
    label: str = ""
    customer_can_use: bool = True
    needs_proof: bool = False
    online: bool = False

    def instructions(self, invoice: Invoice) -> Optional[Dict[str, Any]]:
        """What the customer sees on the invoice for paying this way; None when there is nothing to show."""
        return None

    def describe(self) -> Dict[str, Any]:
        return {"key": self.key, "label": self.label, "customer_can_use": self.customer_can_use, "needs_proof": self.needs_proof,
                "online": self.online}


METHODS: Dict[str, Method] = {}


def register(method: Method) -> Method:
    METHODS[method.key] = method
    return method


def enabled() -> List[Method]:
    """The ways to pay this deployment offers (BILLING_METHODS), in that order."""
    keys = [k.strip() for k in settings.BILLING_METHODS.split(",") if k.strip()]
    unknown = [k for k in keys if k not in METHODS]
    if unknown:
        raise RuntimeError(f"Unknown BILLING_METHODS: {', '.join(unknown)}. Known: {', '.join(sorted(METHODS))}.")
    return [METHODS[k] for k in keys]


def method(key: str) -> Method:
    """An offered way to pay, or ValueError."""
    for m in enabled():
        if m.key == key:
            return m
    raise ValueError(f"Payments by '{key}' aren't taken here.")
