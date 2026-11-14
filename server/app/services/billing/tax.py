"""
The tax on an invoice, worked out from its lines, the buyer and the rates staff set; no database here, so it is tested
directly. Catalog prices are before tax. A buyer's country picks its rate (or the "*" rate when it has none); a rate
marked reverse charge charges nothing to a buyer who gave a tax ID, and the invoice says why.
"""
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence


@dataclass
class Rate:
    country: str            # ISO alpha-2, or "*"
    name: str               # "VAT"
    rate_bp: int            # basis points: 1800 is 18%
    reverse_charge_with_tax_id: bool = False


@dataclass
class Tax:
    rate_bp: int                    # what each line is taxed at
    lines: List[Dict[str, object]]  # what the invoice prints: [{"name", "rate_bp", "taxable_cents", "tax_cents", "note"}]
    total_cents: int


def rate_for(country: Optional[str], rates: Sequence[Rate]) -> Optional[Rate]:
    """The buyer's country's rate, else the default ("*"), else none."""
    by_country = {r.country.upper(): r for r in rates}
    return by_country.get((country or "").upper()) or by_country.get("*")


def tax_for(subtotal_cents: int, country: Optional[str], tax_id: Optional[str], rates: Sequence[Rate]) -> Tax:
    """The tax on a subtotal for a buyer. Rounded half up to the cent, once, on the whole subtotal."""
    rate = rate_for(country, rates)
    if rate is None or rate.rate_bp <= 0:
        return Tax(0, [], 0)
    if rate.reverse_charge_with_tax_id and (tax_id or "").strip():
        return Tax(0, [{"name": rate.name, "rate_bp": 0, "taxable_cents": subtotal_cents, "tax_cents": 0,
                        "note": f"Reverse charge: {rate.name} to be accounted for by the buyer ({tax_id.strip()})"}], 0)
    tax = (subtotal_cents * rate.rate_bp + 5000) // 10000
    return Tax(rate.rate_bp, [{"name": rate.name, "rate_bp": rate.rate_bp, "taxable_cents": subtotal_cents, "tax_cents": tax, "note": None}], tax)
