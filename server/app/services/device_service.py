"""
Trusted devices: signing in from a browser the account hasn't used before needs a code emailed to the account's address.

How it works, and why:
  * Each browser holds a long random token (a cookie the web app keeps; the API only ever sees its hash).
  * The first browser an account ever signs in from is trusted outright, so nobody is locked out the day this ships and a
    brand-new account isn't asked for a code on top of confirming its email.
  * Any other browser is `pending`: it gets a 6-digit code by email and, until it is typed, the API refuses it everything
    except the pages that finish the job. A stolen password or session token is not enough without that browser's token.
  * "Remember this browser" trusts it for DEVICE_TRUST_DAYS (renewed while used); otherwise for DEVICE_SESSION_HOURS.
  * People can see their browsers, remove any of them, or remove all but this one, and read what happened.
"""
import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from fastapi import HTTPException, Request
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core import ratelimit
from app.core.auth.context import Identity
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.models.identity import SecurityEvent, TrustedDevice, User
from app.services.notifications import DEVICE_ADDED, DEVICE_CODE, Recipient, notify

TOKEN = re.compile(r"^[A-Za-z0-9_-]{32,100}$")

# Pages a browser may open before it is confirmed: the ones that confirm it, and the account steps before an account exists.
EXEMPT = {
    "/me/device", "/me/device/check", "/me/device/send", "/me/device/confirm",
    "/me/email-verification", "/me/email-verification/send", "/me/email-verification/confirm", "/auth/logout",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _code_hash(device_id: str, code: str) -> str:
    key = hashlib.sha256(f"device-code:{settings.AGENT_TOKEN_SECRET}".encode()).digest()
    return hmac.new(key, f"{device_id}:{code}".encode(), hashlib.sha256).hexdigest()


def label_for(user_agent: str) -> str:
    ua = user_agent or ""
    browser = next((name for pattern, name in (("Edg/", "Edge"), ("OPR/", "Opera"), ("Firefox/", "Firefox"), ("Chrome/", "Chrome"), ("Safari/", "Safari")) if pattern in ua), "A browser")
    system = next((name for pattern, name in (("Windows", "Windows"), ("iPhone", "iOS"), ("iPad", "iOS"), ("Android", "Android"), ("Mac OS X", "macOS"), ("Linux", "Linux")) if pattern in ua), "an unknown system")
    return f"{browser} on {system}"


def _token(value: Optional[str]) -> str:
    if not value or not TOKEN.match(value):
        raise HTTPException(status_code=400, detail="This browser didn't identify itself. Reload the page and try again.")
    return value


def _valid(dev: Optional[TrustedDevice]) -> bool:
    return bool(dev and dev.status == "trusted" and (dev.trusted_until is None or dev.trusted_until > _now()))


def _code_active(dev: TrustedDevice) -> bool:
    return bool(dev.code_hash and dev.code_expires_at and dev.code_expires_at > _now() and dev.attempts < settings.OTP_MAX_ATTEMPTS)


def _wait(dev: TrustedDevice) -> int:
    if dev.code_sent_at is None:
        return 0
    return max(0, settings.OTP_RESEND_SECONDS - int((_now() - dev.code_sent_at).total_seconds()))


async def _find(db: AsyncSession, user_id: str, token: str) -> Optional[TrustedDevice]:
    return (await db.execute(select(TrustedDevice).where(TrustedDevice.user_id == user_id, TrustedDevice.token_hash == hash_token(token)))).scalar_one_or_none()


def record(db: AsyncSession, user_id: str, kind: str, summary: str, *, device: str = "", ip: str = "") -> None:
    db.add(SecurityEvent(user_id=user_id, kind=kind, summary=summary[:300], device=device[:120], ip=ip[:64]))


def _mask(address: Optional[str]) -> str:
    if not address or "@" not in address:
        return ""
    name, _, domain = address.partition("@")
    return f"{name[:1]}{'•' * max(1, min(6, len(name) - 1))}@{domain}"


def view(dev: TrustedDevice, identity: Identity, *, sent: Optional[bool] = None) -> Dict[str, Any]:
    if _valid(dev):
        state = "trusted"
    else:
        state = "challenge"
    out = {"status": state, "device": dev.label, "email": _mask(identity.email), "resend_in": _wait(dev), "code_sent": _code_active(dev),
           "code_valid_minutes": settings.OTP_TTL_MINUTES, "trust_days": settings.DEVICE_TRUST_DAYS}
    if sent is not None:
        out["email_sent"] = sent
    return out


# ---- the gate ---------------------------------------------------------------------------------

async def gate(request: Request, identity: Identity) -> None:
    """Refuses a person whose browser hasn't been confirmed. Nothing to protect yet if the account isn't made."""
    if not settings.REQUIRE_TRUSTED_DEVICE:
        return
    path = request.url.path.removeprefix(settings.API_V1_PREFIX).rstrip("/") or "/"
    if path in EXEMPT or path.startswith(("/invitations/", "/org-invitations/")) and request.method == "GET":
        return
    token = request.headers.get("X-Device-Token") or ""
    async with AsyncSessionLocal() as db:
        if await db.get(User, identity.user_id) is None:
            return
        dev = await _find(db, identity.user_id, token) if TOKEN.match(token) else None
        if not _valid(dev):
            raise HTTPException(status_code=403, detail={"code": "device_unverified", "message": "Confirm this browser to continue."})
        now = _now()
        if now - dev.last_seen_at > timedelta(minutes=10):
            dev.last_seen_at = now
            if dev.remember and dev.trusted_until and dev.trusted_until - now < timedelta(days=settings.DEVICE_TRUST_DAYS - 1):
                dev.trusted_until = now + timedelta(days=settings.DEVICE_TRUST_DAYS)  # used, so trusted for the full period again
            await db.commit()


# ---- signing in -------------------------------------------------------------------------------

async def _issue_code(db: AsyncSession, identity: Identity, dev: TrustedDevice) -> bool:
    """Emails a fresh code for this browser. True when it left us."""
    if not identity.email:
        raise HTTPException(status_code=422, detail="Your sign-in has no email address to send a code to.")
    ratelimit.limit(f"device-code:{identity.user_id}", 8, 3600)
    code = f"{secrets.randbelow(10 ** settings.OTP_LENGTH):0{settings.OTP_LENGTH}d}"
    now = _now()
    delivery = await notify(DEVICE_CODE, Recipient(email=identity.email), {
        "code": code, "minutes": settings.OTP_TTL_MINUTES, "device": dev.label, "ip": dev.ip or "unknown", "when": now.strftime("%d %B %Y, %H:%M UTC"),
    })
    if delivery.email != "sent":
        return False
    dev.code_hash, dev.attempts = _code_hash(dev.id, code), 0
    dev.code_expires_at, dev.code_sent_at = now + timedelta(minutes=settings.OTP_TTL_MINUTES), now
    record(db, identity.user_id, "code_sent", f"A sign-in code was emailed for {dev.label}.", device=dev.label, ip=dev.ip)
    return True


async def _prune(db: AsyncSession, user_id: str) -> None:
    rows: List[TrustedDevice] = list((await db.execute(select(TrustedDevice).where(TrustedDevice.user_id == user_id).order_by(TrustedDevice.last_seen_at.desc()))).scalars().all())
    for stale in rows[settings.DEVICE_MAX_PER_USER:]:
        await db.delete(stale)


async def check(db: AsyncSession, identity: Identity, *, token: Optional[str], user_agent: str, ip: str) -> Dict[str, Any]:
    """Called by the web app right after sign-in: is this browser known? A new one gets its code emailed."""
    token = _token(token)
    dev = await _find(db, identity.user_id, token)
    now = _now()
    label = label_for(user_agent)
    if _valid(dev):
        dev.last_seen_at = now
        await db.commit()
        return view(dev, identity)

    history = (await db.execute(select(func.count(TrustedDevice.id)).where(TrustedDevice.user_id == identity.user_id, TrustedDevice.trusted_at.is_not(None)))).scalar() or 0
    if dev is None:
        dev = TrustedDevice(user_id=identity.user_id, token_hash=hash_token(token), label=label)
        db.add(dev)
    dev.label, dev.user_agent, dev.ip, dev.last_seen_at = label, (user_agent or "")[:300], (ip or "")[:64], now
    if not history:  # the first browser this account has used
        dev.status, dev.trusted_at, dev.remember = "trusted", now, True
        dev.trusted_until = now + timedelta(days=settings.DEVICE_TRUST_DAYS)
        record(db, identity.user_id, "first_device", f"{label} was added as your first trusted browser.", device=label, ip=dev.ip)
        await db.commit()
        return view(dev, identity)

    if dev.status != "pending":  # expired or removed: it has to prove itself again
        dev.status, dev.code_hash, dev.attempts = "pending", "", 0
    await db.flush()
    sent = None
    if not _code_active(dev):
        sent = await _issue_code(db, identity, dev)
    await _prune(db, identity.user_id)
    await db.commit()
    return view(dev, identity, sent=sent)


async def status(db: AsyncSession, identity: Identity, token: Optional[str]) -> Dict[str, Any]:
    dev = await _find(db, identity.user_id, _token(token))
    if dev is None:
        return {"status": "unknown", "device": "", "email": _mask(identity.email), "resend_in": 0, "code_sent": False,
                "code_valid_minutes": settings.OTP_TTL_MINUTES, "trust_days": settings.DEVICE_TRUST_DAYS}
    return view(dev, identity)


async def send_code(db: AsyncSession, identity: Identity, token: Optional[str]) -> Dict[str, Any]:
    dev = await _find(db, identity.user_id, _token(token))
    if dev is None:
        raise HTTPException(status_code=404, detail="Sign in again to continue.")
    if _valid(dev):
        return view(dev, identity)
    wait = _wait(dev)
    if wait:
        raise HTTPException(status_code=429, detail=f"Wait {wait} seconds before asking for another code.", headers={"Retry-After": str(wait)})
    if not await _issue_code(db, identity, dev):
        await db.rollback()
        raise HTTPException(status_code=502, detail="We couldn't send the code just now. Try again in a moment.")
    await db.commit()
    return view(dev, identity, sent=True)


async def confirm(db: AsyncSession, identity: Identity, token: Optional[str], code: str, remember: bool) -> Dict[str, Any]:
    dev = await _find(db, identity.user_id, _token(token))
    if dev is None:
        raise HTTPException(status_code=404, detail="Sign in again to continue.")
    ratelimit.limit(f"device-confirm:{identity.user_id}", 30, 600)
    if _valid(dev):
        return view(dev, identity)
    if not _code_active(dev):
        if dev.attempts >= settings.OTP_MAX_ATTEMPTS:
            raise HTTPException(status_code=400, detail="Too many wrong codes. Ask for a new one.")
        raise HTTPException(status_code=400, detail="That code has expired or none was sent. Ask for a new one.")
    digits = "".join(ch for ch in code if ch.isdigit())
    if not hmac.compare_digest(dev.code_hash, _code_hash(dev.id, digits)):
        dev.attempts += 1
        left = settings.OTP_MAX_ATTEMPTS - dev.attempts
        record(db, identity.user_id, "code_failed", f"A wrong sign-in code was entered for {dev.label}.", device=dev.label, ip=dev.ip)
        await db.commit()
        raise HTTPException(status_code=400, detail=f"That code isn't right. {left} {'try' if left == 1 else 'tries'} left." if left else "Too many wrong codes. Ask for a new one.")
    now = _now()
    dev.status, dev.code_hash, dev.remember, dev.trusted_at = "trusted", "", bool(remember), now
    dev.trusted_until = now + (timedelta(days=settings.DEVICE_TRUST_DAYS) if remember else timedelta(hours=settings.DEVICE_SESSION_HOURS))
    record(db, identity.user_id, "device_trusted", f"{dev.label} was confirmed{' and remembered' if remember else ' for this visit'}.", device=dev.label, ip=dev.ip)
    await db.commit()
    await notify(DEVICE_ADDED, Recipient(user_id=identity.user_id), {"device": dev.label, "when": now.strftime("%d %B %Y, %H:%M UTC")})
    return view(dev, identity)


# ---- managing them ----------------------------------------------------------------------------

def _row(dev: TrustedDevice, current_hash: Optional[str]) -> Dict[str, Any]:
    return {"id": dev.id, "label": dev.label, "ip": dev.ip, "remember": dev.remember, "trusted_at": dev.trusted_at, "trusted_until": dev.trusted_until,
            "last_seen_at": dev.last_seen_at, "current": dev.token_hash == current_hash}


async def list_devices(db: AsyncSession, identity: Identity, token: Optional[str]) -> List[Dict[str, Any]]:
    current = hash_token(token) if token and TOKEN.match(token) else None
    rows = (await db.execute(select(TrustedDevice).where(TrustedDevice.user_id == identity.user_id, TrustedDevice.status == "trusted")
                             .order_by(TrustedDevice.last_seen_at.desc()))).scalars().all()
    return [_row(d, current) for d in rows if _valid(d)]


async def revoke(db: AsyncSession, identity: Identity, device_id: str) -> None:
    dev = await db.get(TrustedDevice, device_id)
    if dev is None or dev.user_id != identity.user_id:
        raise HTTPException(status_code=404, detail="Browser not found.")
    dev.status, dev.code_hash = "revoked", ""
    record(db, identity.user_id, "device_removed", f"{dev.label} was removed.", device=dev.label, ip=dev.ip)
    await db.commit()


async def revoke_others(db: AsyncSession, identity: Identity, token: Optional[str]) -> int:
    current = hash_token(_token(token))
    rows = (await db.execute(select(TrustedDevice).where(TrustedDevice.user_id == identity.user_id, TrustedDevice.status == "trusted", TrustedDevice.token_hash != current))).scalars().all()
    for dev in rows:
        dev.status, dev.code_hash = "revoked", ""
    if rows:
        record(db, identity.user_id, "devices_removed", f"{len(rows)} other browser{'s were' if len(rows) != 1 else ' was'} removed.")
    await db.commit()
    return len(rows)


async def events(db: AsyncSession, identity: Identity, *, limit: int = 30, before: Optional[datetime] = None) -> Dict[str, Any]:
    stmt = select(SecurityEvent).where(SecurityEvent.user_id == identity.user_id)
    if before:
        stmt = stmt.where(SecurityEvent.created_at < before)
    rows = list((await db.execute(stmt.order_by(SecurityEvent.created_at.desc()).limit(limit + 1))).scalars().all())
    items = [{"id": e.id, "kind": e.kind, "summary": e.summary, "device": e.device, "ip": e.ip, "created_at": e.created_at} for e in rows[:limit]]
    return {"items": items, "next_before": items[-1]["created_at"] if len(rows) > limit and items else None}
