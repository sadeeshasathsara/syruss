"""
Invoice and credit note numbers: one series a year each, with no gaps (many tax authorities ask for that). The next
number is taken with a row lock that holds until the issuing transaction ends, so two invoices issued at once get
consecutive numbers, and an issue that fails gives its number back.
"""
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def next_number(db: AsyncSession, series: str) -> int:
    """The next number in a series ("invoice:2026"), starting at 1. Call it inside the transaction that uses it."""
    return (await db.execute(text(
        "INSERT INTO billing_sequences (series, last) VALUES (:series, 1) "
        "ON CONFLICT (series) DO UPDATE SET last = billing_sequences.last + 1 RETURNING last"), {"series": series})).scalar_one()


def invoice_number(prefix: str, year: int, n: int) -> str:
    """"SYR-2026-00042"."""
    return f"{prefix}-{year}-{n:05d}"
