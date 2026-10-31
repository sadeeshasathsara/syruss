"""
Confirming an email address with a one-time code, before a Syruss account exists.

Keycloak proves who someone is and (for password sign-ups) can only check an address with a link after the fact.
Here the person types a six-digit code we email them, as the first step of onboarding; nothing in Syruss opens
until that is done. An address Keycloak already vouches for (Google sign-in) skips the step.

Codes live for minutes, are stored only as a keyed hash, allow a few wrong tries, and are single-use.
"""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core import ratelimit
from app.core.auth.context import Identity
from app.core.config import settings
from app.models.identity import EmailVerification, User
from app.services.notifications import EMAIL_CODE, Recipient, notify


def _key() -> bytes:
    return hashlib.sha256(f"email-code:{settings.AGENT_TOKEN_SECRET}".encode()).digest()


def _hash(user_id: str, address: str, code: str) -> str:
    return hmac.new(_key(), f"{user_id}:{address}:{code}".encode(), hashlib.sha256).hexdigest()


def _address(identity: Identity) -> str:
    if not identity.email:
        raise HTTPException(status_code=422, detail="Your sign-in has no email address to confirm.")
    return identity.email.strip().lower()


def _unverified() -> HTTPException:
    return HTTPException(status_code=403, detail={"code": "email_unverified", "message": "Confirm your email address first."})


async def _row(db: AsyncSession, identity: Identity) -> Optional[EmailVerification]:
    return (await db.execute(select(EmailVerification).where(
        EmailVerification.user_id == identity.user_id, EmailVerification.email == _address(identity)))).scalar_one_or_none()


async def is_verified(db: AsyncSession, identity: Identity) -> bool:
    """Keycloak vouches for the address, or this person confirmed it here (or an account already holds it as verified)."""
    if identity.email_verified:
        return True
    if not identity.email:
        return False
    user = await db.get(User, identity.user_id)
    if user is not None and user.email_verified and user.email.lower() == identity.email.lower():
        return True
    row = await _row(db, identity)
    return bool(row and row.verified_at)


async def require_verified(db: AsyncSession, identity: Identity) -> None:
    if settings.REQUIRE_EMAIL_VERIFICATION and not await is_verified(db, identity):
        raise _unverified()


def _wait(row: Optional[EmailVerification]) -> int:
    """Seconds until another code may be sent."""
    if row is None or row.sent_at is None:
        return 0
    return max(0, settings.OTP_RESEND_SECONDS - int((datetime.now(timezone.utc) - row.sent_at).total_seconds()))


def _active(row: Optional[EmailVerification]) -> bool:
    return bool(row and row.code_hash and not row.verified_at and row.expires_at > datetime.now(timezone.utc)
                and row.attempts < settings.OTP_MAX_ATTEMPTS)


async def status(db: AsyncSession, identity: Identity) -> Dict[str, Any]:
    row = await _row(db, identity) if identity.email else None
    return {"verified": await is_verified(db, identity), "email": identity.email, "code_sent": _active(row), "resend_in": _wait(row),
            "code_valid_minutes": settings.OTP_TTL_MINUTES}


async def send_code(db: AsyncSession, identity: Identity) -> Dict[str, Any]:
    address = _address(identity)
    if await is_verified(db, identity):
        return await status(db, identity)
    row = await _row(db, identity)
    wait = _wait(row)
    if wait:  # too soon: not counted against the hourly allowance
        raise HTTPException(status_code=429, detail=f"Wait {wait} seconds before asking for another code.", headers={"Retry-After": str(wait)})
    ratelimit.limit(f"otp-send:{identity.user_id}", settings.OTP_SENDS_PER_HOUR, 3600)
    code = f"{secrets.randbelow(10 ** settings.OTP_LENGTH):0{settings.OTP_LENGTH}d}"
    now = datetime.now(timezone.utc)
    if row is None:
        row = EmailVerification(user_id=identity.user_id, email=address)
        db.add(row)
    row.code_hash, row.attempts = _hash(identity.user_id, address, code), 0
    row.expires_at, row.sent_at = now + timedelta(minutes=settings.OTP_TTL_MINUTES), now
    # The code only counts once it has left us.
    delivery = await notify(EMAIL_CODE, Recipient(email=address), {"code": code, "minutes": settings.OTP_TTL_MINUTES})
    if delivery.email != "sent":
        await db.rollback()
        raise HTTPException(status_code=502, detail="We couldn't send the code just now. Try again in a moment.")
    await db.commit()
    return await status(db, identity)


async def confirm(db: AsyncSession, identity: Identity, code: str) -> Dict[str, Any]:
    address = _address(identity)
    ratelimit.limit(f"otp-confirm:{identity.user_id}", 30, 600)
    if await is_verified(db, identity):
        return await status(db, identity)
    row = await _row(db, identity)
    if not _active(row):
        if row and row.attempts >= settings.OTP_MAX_ATTEMPTS:
            raise HTTPException(status_code=400, detail="Too many wrong codes. Ask for a new one.")
        raise HTTPException(status_code=400, detail="That code has expired or none was sent. Ask for a new one.")
    code = "".join(ch for ch in code if ch.isdigit())
    if not hmac.compare_digest(row.code_hash, _hash(identity.user_id, address, code)):
        row.attempts += 1
        left = settings.OTP_MAX_ATTEMPTS - row.attempts
        await db.commit()
        raise HTTPException(status_code=400, detail=f"That code isn't right. {left} {'try' if left == 1 else 'tries'} left." if left else "Too many wrong codes. Ask for a new one.")
    row.verified_at, row.code_hash = datetime.now(timezone.utc), ""
    await db.commit()
    return await status(db, identity)
