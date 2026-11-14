"""
Bank transfer: the customer pays into one of Syruss's accounts in the invoice's currency, quoting the invoice number,
and sends the slip; the payment counts once staff have checked it against the account.
"""
from typing import Any, Dict, Optional
from app.models.billing import Invoice
from app.services.billing.methods.base import Method, register


class BankTransfer(Method):
    key, label, needs_proof = "bank_transfer", "Bank transfer", True

    def instructions(self, invoice: Invoice) -> Optional[Dict[str, Any]]:
        if not invoice.bank:
            return None
        return {"reference": invoice.number, "accounts": invoice.bank,
                "text": f"Pay into one of these accounts, quoting {invoice.number} as the reference, then send the bank slip here."}


register(BankTransfer())
