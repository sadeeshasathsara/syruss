"""
Who is sending a document in. An intake key is tied to exactly one channel, and the channel to one workspace, so the key alone says where
a document belongs: senders never name a workspace. These keys are accepted only on /intake/* and nowhere else in the API
(get_current_auth refuses them), so a leaked key can add documents to its channel and read back its own, and do nothing more.
"""
import hashlib
import ipaddress
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core import ratelimit
from app.core.config import settings
from app.core.database import AsyncSessionLocal, get_db_session
from app.models.intake import IntakeChannel
from app.models.tenant import Tenant
from app.models.template import DocumentTemplate
from app.models.workspace_admin import ApiKey
from app.services.intake_rules import problem
from app.services.intake_service import Sender, reject


@dataclass
class IntakeCaller:
    key: ApiKey
    channel: IntakeChannel
    template: DocumentTemplate
    ip: str

    @property
    def test(self) -> bool:
        return self.key.mode == "test"

    def sender(self, via: str = "api") -> Sender:
        return Sender(label=self.key.name, via=via, ip=self.ip, key_id=self.key.id, test=self.test)


def client_ip(request: Request) -> str:
    """The sender's address. X-Forwarded-For is believed only when the API runs behind a proxy you control (TRUST_PROXY_HEADERS)."""
    if settings.TRUST_PROXY_HEADERS:
        forwarded = (request.headers.get("X-Forwarded-For") or "").split(",")[0].strip()
        if forwarded:
            return forwarded
    return request.client.host if request.client else ""


def ip_allowed(ip: str, allowlist: List[str]) -> bool:
    """Whether the address is inside one of the allowed addresses or ranges (an empty list allows any)."""
    if not allowlist:
        return True
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for entry in allowlist:
        try:
            if address in ipaddress.ip_network(entry.strip(), strict=False):
                return True
        except ValueError:
            continue
    return False


def valid_allowlist(entries: List[str]) -> List[str]:
    """The allowlist as stored (addresses and ranges, checked), or ValueError naming the entry that is not one."""
    clean = []
    for entry in entries:
        entry = entry.strip()
        if not entry:
            continue
        try:
            ipaddress.ip_network(entry, strict=False)
        except ValueError:
            raise ValueError(f"'{entry}' is not an IP address or range such as 203.0.113.7 or 203.0.113.0/24.")
        clean.append(entry)
    return clean


def _bearer(request: Request) -> str:
    header = (request.headers.get("Authorization") or "").strip()
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return (request.headers.get("X-API-Key") or "").strip()


def _unauthorized(message: str) -> HTTPException:
    return problem(401, message, headers={"WWW-Authenticate": "Bearer"})


async def intake_caller(request: Request, db: AsyncSession = Depends(get_db_session)) -> IntakeCaller:
    """Checks the key, the sender's address, the rate limit and that the channel and organization are active; answers who is calling."""
    token = _bearer(request)
    if not token.startswith("sk_"):
        raise _unauthorized("Send your intake key as 'Authorization: Bearer sk_...'.")
    key = (await db.execute(select(ApiKey).where(ApiKey.key_hash == hashlib.sha256(token.encode()).hexdigest()))).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if key is None or key.revoked_at or (key.expires_at and key.expires_at < now):
        raise _unauthorized("This key is not valid. It may have expired or been revoked.")
    if key.scope != "intake" or not key.channel_id:
        raise problem(403, "This key cannot send documents. Create an intake key for a channel.", "not_an_intake_key")

    ip = client_ip(request)
    channel = await db.get(IntakeChannel, key.channel_id)
    template = await db.get(DocumentTemplate, key.template_id)
    tenant = await db.get(Tenant, key.tenant_id)
    if channel is None or template is None or tenant is None or not tenant.is_active:
        raise _unauthorized("This key is not valid.")
    caller = IntakeCaller(key=key, channel=channel, template=template, ip=ip)

    if not ip_allowed(ip, key.ip_allowlist or []):
        await reject(tenant_id=key.tenant_id, template_id=key.template_id, channel_id=channel.id, sender=caller.sender(),
                     reason=f"Refused: {ip or 'an unknown address'} is not on this key's list of allowed addresses.")
        raise problem(403, "Requests from this address are not allowed for this key.", "ip_not_allowed")
    try:
        ratelimit.limit(f"intake-key:{key.id}", key.rate_limit_per_minute, 60)
    except HTTPException:
        raise problem(429, f"This key may make {key.rate_limit_per_minute} requests a minute. Slow down and retry.", "rate_limited",
                      headers={"Retry-After": "60"})
    if channel.status != "active":
        raise problem(403, "This channel is paused. Ask the workspace owner to turn it back on.", "channel_paused")

    # Remember the last use without writing on every request.
    if key.last_used_at is None or now - key.last_used_at > timedelta(minutes=1):
        async with AsyncSessionLocal() as own:
            row = await own.get(ApiKey, key.id)
            if row is not None:
                row.last_used_at = now
                await own.commit()
    request.state.intake_caller = caller
    return caller
