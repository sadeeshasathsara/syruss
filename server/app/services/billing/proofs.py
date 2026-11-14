"""
Proof of payment: the bank slip or transfer confirmation a customer sends for an invoice, which staff check before the
payment counts. A file is accepted only when its content really is a PDF or a photo (the name and the browser's word
for it are not trusted), no bigger than MAX_BYTES; it is kept privately, as a financial record, and never deleted.
Its fingerprint (sha256) lets staff see when the same slip was sent before.
"""
import asyncio
import hashlib
import os
from typing import List, Optional, Tuple
import filetype
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.billing import PaymentFile, PaymentSubmission
from app.services.storage_service import storage_service

ALLOWED = {"application/pdf": "pdf", "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}
MAX_BYTES = 10 * 1024 * 1024
MAX_FILES = 5


def check(data: bytes) -> Tuple[str, str]:
    """(content type, extension) of an acceptable file, or ValueError saying what is wrong with it."""
    if not data:
        raise ValueError("That file is empty.")
    if len(data) > MAX_BYTES:
        raise ValueError(f"A file can be up to {MAX_BYTES // (1024 * 1024)} MB.")
    kind = filetype.guess(data)
    if kind is None or kind.mime not in ALLOWED:
        raise ValueError("Send the slip as a PDF or a photo (JPEG, PNG or WebP).")
    return kind.mime, ALLOWED[kind.mime]


def _clean_name(name: Optional[str], ext: str) -> str:
    base = os.path.basename((name or "").replace("\\", "/")).strip() or f"slip.{ext}"
    return "".join(c for c in base if c.isprintable() and c not in '"<>')[:200] or f"slip.{ext}"


async def store(db: AsyncSession, *, tenant_id: str, filename: Optional[str], data: bytes, uploaded_by: str,
                submission_id: Optional[str] = None, payment_id: Optional[str] = None) -> PaymentFile:
    """Checks and keeps one file, for a customer's submission or a payment staff recorded."""
    content_type, ext = check(data)
    row = PaymentFile(tenant_id=tenant_id, submission_id=submission_id, payment_id=payment_id, filename=_clean_name(filename, ext),
                      content_type=content_type, size=len(data), sha256=hashlib.sha256(data).hexdigest(), uploaded_by=uploaded_by, object_key="")
    db.add(row)
    await db.flush()
    row.object_key = f"billing/{tenant_id}/proofs/{row.id}.{ext}"
    await asyncio.to_thread(storage_service.upload_file, row.object_key, data, content_type)
    await db.flush()
    return row


async def files_of(db: AsyncSession, *, submission_id: Optional[str] = None, payment_id: Optional[str] = None) -> List[PaymentFile]:
    stmt = select(PaymentFile).order_by(PaymentFile.created_at)
    stmt = stmt.where(PaymentFile.submission_id == submission_id) if submission_id else stmt.where(PaymentFile.payment_id == payment_id)
    return list((await db.execute(stmt)).scalars().all())


async def read(f: PaymentFile) -> bytes:
    return await asyncio.to_thread(storage_service.read_object, f.object_key, MAX_BYTES)


async def warnings(db: AsyncSession, submission: PaymentSubmission, amount_due: int) -> List[str]:
    """What staff should look at twice: an amount that isn't what is due, a slip or bank reference sent before."""
    out = []
    if submission.amount_cents != amount_due:
        out.append("The amount sent isn't the amount due." if submission.amount_cents < amount_due else "The amount sent is more than is due.")
    hashes = [f.sha256 for f in await files_of(db, submission_id=submission.id)]
    if hashes and (await db.execute(select(PaymentFile.id).where(PaymentFile.sha256.in_(hashes), PaymentFile.submission_id != submission.id,
                                                                 PaymentFile.submission_id.isnot(None)).limit(1))).first():
        out.append("The same file was sent before, with another payment.")
    if submission.payer_reference and (await db.execute(select(PaymentSubmission.id).where(
            PaymentSubmission.payer_reference == submission.payer_reference, PaymentSubmission.id != submission.id,
            PaymentSubmission.status != "withdrawn").limit(1))).first():
        out.append("The same bank reference was given for another payment.")
    return out
