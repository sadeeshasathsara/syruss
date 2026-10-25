"""
Intake channels: how other systems send documents into a workspace. People with `integrations.manage` create a channel, give each sending
system its own key (shown once), watch what arrived and what was refused, and can send a test document to see the whole path work.
"""
import hashlib
import io
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.auth import require_recent_login
from app.core.auth.intake_keys import valid_allowlist
from app.core.config import settings
from app.core.database import get_db_session
from app.core.permissions import Access, require
from app.models.audit import AuditLog
from app.models.intake import IntakeChannel, IntakeEvent
from app.models.workspace_admin import ApiKey
from app.services import intake_email, intake_service, intake_sftp, settings_service
from app.services.intake_rules import SUPPORTED, IntakeRejected

router = APIRouter(prefix="/channels", tags=["Connections: receive"])

MAX_KEYS_PER_CHANNEL = 20


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "channel"


def _audit(db: AsyncSession, access: Access, event: str, channel: IntakeChannel, **details: Any) -> None:
    db.add(AuditLog(tenant_id=access.scope.tenant_id, template_id=access.scope.template_id, event_type=event, entity_type="IntakeChannel",
                    entity_id=channel.id, actor=access.auth.user_id, details={"channel": channel.name, **details}))


def _base() -> str:
    return f"{settings.PUBLIC_API_URL.rstrip('/')}{settings.API_V1_PREFIX}"


def _key_dict(k: ApiKey) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    state = "revoked" if k.revoked_at else "expired" if k.expires_at and k.expires_at < now else "retiring" if k.replaced_by else "active"
    return {"id": k.id, "name": k.name, "prefix": k.prefix, "mode": k.mode, "state": state, "created_by": k.created_by, "created_at": k.created_at,
            "expires_at": k.expires_at, "last_used_at": k.last_used_at, "revoked_at": k.revoked_at, "ip_allowlist": k.ip_allowlist or [],
            "rate_limit_per_minute": k.rate_limit_per_minute, "daily_quota": k.daily_quota, "replaced_by": k.replaced_by}


async def _stats(db: AsyncSession, channel_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """Per channel: documents accepted and refused in the last 24 hours, and when something last arrived."""
    out = {cid: {"accepted_24h": 0, "rejected_24h": 0, "last_received_at": None} for cid in channel_ids}
    if not channel_ids:
        return out
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    rows = (await db.execute(
        select(IntakeEvent.channel_id, IntakeEvent.outcome, func.count(IntakeEvent.id))
        .where(IntakeEvent.channel_id.in_(channel_ids), IntakeEvent.created_at >= since).group_by(IntakeEvent.channel_id, IntakeEvent.outcome)
    )).all()
    for cid, outcome, n in rows:
        if outcome == "accepted":
            out[cid]["accepted_24h"] = n
        elif outcome == "rejected":
            out[cid]["rejected_24h"] = n
    last = (await db.execute(
        select(IntakeEvent.channel_id, func.max(IntakeEvent.created_at)).where(IntakeEvent.channel_id.in_(channel_ids), IntakeEvent.outcome == "accepted")
        .group_by(IntakeEvent.channel_id)
    )).all()
    for cid, at in last:
        out[cid]["last_received_at"] = at
    return out


def _channel_dict(c: IntakeChannel, stats: Dict[str, Any], keys: List[ApiKey]) -> Dict[str, Any]:
    cfg = c.config or {}
    return {
        "id": c.id, "slug": c.slug, "name": c.name, "kind": c.kind, "status": c.status, "created_by": c.created_by, "created_at": c.created_at,
        "config": {"allowed_types": cfg.get("allowed_types"), "max_mb": cfg.get("max_mb"), "process": cfg.get("process"),
                   "sender_allowlist": cfg.get("sender_allowlist", []), "require_authentication": bool(cfg.get("require_authentication"))},
        "endpoint": f"{_base()}/intake/documents" if c.kind == "api" else None,
        "address": intake_email.address_for(c), "sftp": _sftp_info(c),
        "receiver_ready": {"api": True, "email": bool(settings.INTAKE_SMTP_PORT), "sftp": bool(settings.INTAKE_SFTP_PORT)}[c.kind],
        "stats": stats, "keys": [_key_dict(k) for k in keys],
    }


def _sftp_info(c: IntakeChannel) -> Optional[Dict[str, Any]]:
    if c.kind != "sftp":
        return None
    cfg = c.config or {}
    return {"host": intake_sftp.public_host(), "port": settings.INTAKE_SFTP_PORT, "username": cfg.get("sftp_username"), "has_public_key": bool(cfg.get("sftp_public_key"))}


async def _channel(db: AsyncSession, access: Access, channel_id: str) -> IntakeChannel:
    c = await db.get(IntakeChannel, channel_id)
    if c is None or c.template_id != access.scope.template_id:
        raise HTTPException(status_code=404, detail="Channel not found.")
    return c


async def _keys(db: AsyncSession, channel_id: str) -> List[ApiKey]:
    return list((await db.execute(select(ApiKey).where(ApiKey.channel_id == channel_id).order_by(ApiKey.created_at.desc()))).scalars().all())


class ChannelConfig(BaseModel):
    allowed_types: Optional[List[str]] = Field(None, description="Narrow what this channel accepts; empty means the workspace's list")
    max_mb: Optional[int] = Field(None, ge=1, le=500)
    process: Optional[bool] = Field(None, description="Hand documents to the agent on arrival; empty means the workspace's setting")
    sender_allowlist: Optional[List[str]] = Field(None, description="E-mail channels: the addresses or @domains whose mail is accepted; empty accepts anyone")
    require_authentication: Optional[bool] = Field(None, description="E-mail channels: only accept mail your mail gateway recorded as passing SPF or DKIM")


def _clean_config(cfg: ChannelConfig) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if cfg.allowed_types:
        bad = [t for t in cfg.allowed_types if t not in SUPPORTED]
        if bad:
            raise HTTPException(status_code=422, detail=f"{', '.join(bad)} cannot be read yet. Supported: {', '.join(SUPPORTED)}.")
        out["allowed_types"] = cfg.allowed_types
    if cfg.max_mb:
        out["max_mb"] = cfg.max_mb
    if cfg.process is not None:
        out["process"] = cfg.process
    if cfg.sender_allowlist:
        out["sender_allowlist"] = [s.strip().lower() for s in cfg.sender_allowlist if s.strip()][:100]
    if cfg.require_authentication is not None:
        out["require_authentication"] = cfg.require_authentication
    return out


@router.get("/developer-info")
async def developer_info(access: Access = Depends(require("integrations.manage")), db: AsyncSession = Depends(get_db_session)):
    """What a developer connecting a system needs to know: the real address to call and what this workspace accepts."""
    prefs = (await settings_service.get_settings(db, access.scope.template_id)).documents
    return {"base_url": _base(), "intake_url": f"{_base()}/intake/documents", "accepts": [t for t in prefs.allowed_types if t in SUPPORTED],
            "max_file_mb": prefs.max_file_mb, "upload_minutes": settings.INTAKE_UPLOAD_MINUTES, "workspace": access.scope.template.name,
            "trusts_proxy_headers": settings.TRUST_PROXY_HEADERS,
            "email": {"enabled": bool(settings.INTAKE_SMTP_PORT and settings.INTAKE_EMAIL_DOMAIN), "domain": settings.INTAKE_EMAIL_DOMAIN},
            "sftp": {"enabled": bool(settings.INTAKE_SFTP_PORT), "host": intake_sftp.public_host(), "port": settings.INTAKE_SFTP_PORT}}


class ChannelIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    kind: Literal["api", "email", "sftp"] = "api"
    config: ChannelConfig = Field(default_factory=ChannelConfig)


class ChannelPatch(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=120)
    status: Optional[Literal["active", "paused"]] = None
    config: Optional[ChannelConfig] = None


@router.get("")
async def list_channels(access: Access = Depends(require("integrations.manage")), db: AsyncSession = Depends(get_db_session)):
    rows = list((await db.execute(select(IntakeChannel).where(IntakeChannel.template_id == access.scope.template_id)
                                  .order_by(IntakeChannel.created_at))).scalars().all())
    stats = await _stats(db, [c.id for c in rows])
    keys = (await db.execute(select(ApiKey).where(ApiKey.channel_id.in_([c.id for c in rows])).order_by(ApiKey.created_at.desc()))).scalars().all() if rows else []
    return [_channel_dict(c, stats[c.id], [k for k in keys if k.channel_id == c.id]) for c in rows]


@router.post("", status_code=201, dependencies=[Depends(require_recent_login)])
async def create_channel(body: ChannelIn, access: Access = Depends(require("integrations.manage")), db: AsyncSession = Depends(get_db_session)):
    await settings_service.check_limit(db, access.scope.template_id, "channels")
    taken = {s for (s,) in (await db.execute(select(IntakeChannel.slug).where(IntakeChannel.template_id == access.scope.template_id))).all()}
    slug, n = _slug(body.name), 2
    while slug in taken:
        slug, n = f"{_slug(body.name)}-{n}", n + 1
    config = _clean_config(body.config)
    password = None
    if body.kind == "email":
        if not settings.INTAKE_SMTP_PORT or not settings.INTAKE_EMAIL_DOMAIN:
            raise HTTPException(status_code=409, detail="E-mail intake is not switched on for this server. Ask whoever runs it to set INTAKE_SMTP_PORT and INTAKE_EMAIL_DOMAIN.")
        config["email_token"] = f"{slug[:16].strip('-') or 'inbox'}-{secrets.token_hex(4)}"
    elif body.kind == "sftp":
        if not settings.INTAKE_SFTP_PORT:
            raise HTTPException(status_code=409, detail="SFTP intake is not switched on for this server. Ask whoever runs it to set INTAKE_SFTP_PORT.")
        credentials = intake_sftp.new_credentials(slug)
        password = credentials.pop("password")
        config.update(credentials)
    channel = IntakeChannel(tenant_id=access.scope.tenant_id, template_id=access.scope.template_id, slug=slug, name=body.name.strip(), kind=body.kind,
                            config=config, created_by=access.auth.username or access.auth.user_id)
    db.add(channel)
    await db.flush()
    _audit(db, access, "INTAKE_CHANNEL_CREATED", channel, kind=channel.kind)
    await db.commit()
    out = _channel_dict(channel, (await _stats(db, [channel.id]))[channel.id], [])
    if password:
        out["sftp_password"] = password          # shown once
    return out


@router.patch("/{channel_id}", dependencies=[Depends(require_recent_login)])
async def update_channel(channel_id: str, body: ChannelPatch, access: Access = Depends(require("integrations.manage")), db: AsyncSession = Depends(get_db_session)):
    channel = await _channel(db, access, channel_id)
    changed = []
    if body.name is not None:
        channel.name = body.name.strip()
        changed.append("name")
    if body.status is not None and body.status != channel.status:
        channel.status = body.status
        changed.append(body.status)
    if body.config is not None:
        channel.config = {**(channel.config or {}), **_clean_config(body.config)}
        # Fields sent as null/empty clear the override and fall back to the workspace's setting.
        sent = body.config.model_fields_set
        for field in ("allowed_types", "max_mb", "process", "sender_allowlist", "require_authentication"):
            if field in sent and not getattr(body.config, field) and getattr(body.config, field) is not False:
                channel.config.pop(field, None)
        changed.append("rules")
    _audit(db, access, "INTAKE_CHANNEL_UPDATED", channel, changed=changed)
    await db.commit()
    return _channel_dict(channel, (await _stats(db, [channel.id]))[channel.id], await _keys(db, channel.id))


@router.delete("/{channel_id}", status_code=204, dependencies=[Depends(require_recent_login)])
async def delete_channel(channel_id: str, access: Access = Depends(require("integrations.manage")), db: AsyncSession = Depends(get_db_session)):
    """Removes the channel and its keys. Documents that came through it stay, marked as from a removed channel."""
    channel = await _channel(db, access, channel_id)
    _audit(db, access, "INTAKE_CHANNEL_DELETED", channel)
    await db.delete(channel)
    await db.commit()


# --- keys ---------------------------------------------------------------------------------------------------

class KeyIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120, description="The system that will use it, e.g. 'Warehouse scanner'")
    mode: Literal["live", "test"] = "live"
    ip_allowlist: List[str] = Field(default_factory=list, description="Addresses or ranges allowed to use the key; empty allows any")
    rate_limit_per_minute: int = Field(60, ge=1, le=6000)
    daily_quota: Optional[int] = Field(None, ge=1, le=1_000_000)
    expires_in_days: Optional[int] = Field(None, ge=1, le=730)


def _new_key(access: Access, channel: IntakeChannel, body: KeyIn) -> tuple[ApiKey, str]:
    try:
        allow = valid_allowlist(body.ip_allowlist)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    plain = f"sk_{body.mode}_{secrets.token_urlsafe(32)}"
    key = ApiKey(
        tenant_id=access.scope.tenant_id, template_id=access.scope.template_id, name=body.name.strip(), prefix=plain[:12], key_hash=_hash(plain),
        scope="intake", channel_id=channel.id, mode=body.mode, ip_allowlist=allow, rate_limit_per_minute=body.rate_limit_per_minute,
        daily_quota=body.daily_quota, created_by=access.auth.username or access.auth.user_id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=body.expires_in_days) if body.expires_in_days else None,
    )
    return key, plain


@router.post("/{channel_id}/keys", status_code=201, dependencies=[Depends(require_recent_login)])
async def create_key(channel_id: str, body: KeyIn, access: Access = Depends(require("integrations.manage")), db: AsyncSession = Depends(get_db_session)):
    """The key is returned once; only a fingerprint is kept. Send it as `Authorization: Bearer <key>`."""
    channel = await _channel(db, access, channel_id)
    if channel.kind != "api":
        raise HTTPException(status_code=409, detail="Keys are for API channels. E-mail and SFTP channels are reached by their own address and sign-in.")
    live = [k for k in await _keys(db, channel.id) if not k.revoked_at]
    if len(live) >= MAX_KEYS_PER_CHANNEL:
        raise HTTPException(status_code=409, detail=f"A channel can have {MAX_KEYS_PER_CHANNEL} keys. Revoke one you no longer use.")
    key, plain = _new_key(access, channel, body)
    db.add(key)
    await db.flush()
    _audit(db, access, "INTAKE_KEY_CREATED", channel, key=key.name, mode=key.mode, key_id=key.id)
    await db.commit()
    return {"key": _key_dict(key), "secret": plain}


class RotateIn(BaseModel):
    overlap_hours: int = Field(24, ge=0, le=720, description="How long the old key keeps working so the sender can switch over")


@router.post("/{channel_id}/keys/{key_id}/rotate", status_code=201, dependencies=[Depends(require_recent_login)])
async def rotate_key(channel_id: str, key_id: str, body: RotateIn, access: Access = Depends(require("integrations.manage")), db: AsyncSession = Depends(get_db_session)):
    """Makes a replacement with the same settings. The old key stops after the overlap (immediately when 0)."""
    channel = await _channel(db, access, channel_id)
    old = await db.get(ApiKey, key_id)
    if old is None or old.channel_id != channel.id or old.revoked_at:
        raise HTTPException(status_code=404, detail="Key not found.")
    key, plain = _new_key(access, channel, KeyIn(
        name=old.name, mode=old.mode, ip_allowlist=old.ip_allowlist or [], rate_limit_per_minute=old.rate_limit_per_minute, daily_quota=old.daily_quota))
    db.add(key)
    await db.flush()
    now = datetime.now(timezone.utc)
    old.replaced_by = key.id
    if body.overlap_hours == 0:
        old.revoked_at = now
    else:
        old.expires_at = min(old.expires_at, now + timedelta(hours=body.overlap_hours)) if old.expires_at else now + timedelta(hours=body.overlap_hours)
    _audit(db, access, "INTAKE_KEY_ROTATED", channel, key=old.name, old_key_id=old.id, new_key_id=key.id, overlap_hours=body.overlap_hours)
    await db.commit()
    return {"key": _key_dict(key), "secret": plain, "old_key": _key_dict(old)}


@router.delete("/{channel_id}/keys/{key_id}", status_code=204, dependencies=[Depends(require_recent_login)])
async def revoke_key(channel_id: str, key_id: str, access: Access = Depends(require("integrations.manage")), db: AsyncSession = Depends(get_db_session)):
    channel = await _channel(db, access, channel_id)
    key = await db.get(ApiKey, key_id)
    if key is None or key.channel_id != channel.id:
        raise HTTPException(status_code=404, detail="Key not found.")
    key.revoked_at = key.revoked_at or datetime.now(timezone.utc)
    _audit(db, access, "INTAKE_KEY_REVOKED", channel, key=key.name, key_id=key.id)
    await db.commit()


# --- SFTP sign-in --------------------------------------------------------------------------------------------

class PublicKeyIn(BaseModel):
    public_key: Optional[str] = Field(None, description="One or more OpenSSH public keys, one per line; empty removes them")


async def _sftp_channel(db: AsyncSession, access: Access, channel_id: str) -> IntakeChannel:
    channel = await _channel(db, access, channel_id)
    if channel.kind != "sftp":
        raise HTTPException(status_code=409, detail="This is not an SFTP channel.")
    return channel


@router.post("/{channel_id}/sftp/password", dependencies=[Depends(require_recent_login)])
async def reset_sftp_password(channel_id: str, access: Access = Depends(require("integrations.manage")), db: AsyncSession = Depends(get_db_session)):
    """A new password for the SFTP user. The old one stops working at once. Shown once."""
    channel = await _sftp_channel(db, access, channel_id)
    password = secrets.token_urlsafe(18)
    channel.config = {**(channel.config or {}), "sftp_secret_hash": intake_sftp.hash_password(password)}
    _audit(db, access, "INTAKE_SFTP_PASSWORD_RESET", channel)
    await db.commit()
    return {"password": password}


@router.put("/{channel_id}/sftp/public-key", dependencies=[Depends(require_recent_login)])
async def set_sftp_public_key(channel_id: str, body: PublicKeyIn, access: Access = Depends(require("integrations.manage")), db: AsyncSession = Depends(get_db_session)):
    """SSH keys that may sign in as the SFTP user, in addition to the password."""
    channel = await _sftp_channel(db, access, channel_id)
    lines = [l.strip() for l in (body.public_key or "").splitlines() if l.strip()]
    for line in lines:
        problem = intake_sftp.valid_public_key(line)
        if problem:
            raise HTTPException(status_code=422, detail=problem)
    config = dict(channel.config or {})
    if lines:
        config["sftp_public_key"] = "\n".join(lines[:10])
    else:
        config.pop("sftp_public_key", None)
    channel.config = config
    _audit(db, access, "INTAKE_SFTP_KEY_SET", channel, keys=len(lines))
    await db.commit()
    return _channel_dict(channel, (await _stats(db, [channel.id]))[channel.id], [])


# --- what arrived, and a test ------------------------------------------------------------------------------

@router.get("/{channel_id}/activity")
async def activity(
    channel_id: str,
    outcome: Optional[Literal["accepted", "rejected"]] = None,
    before: Optional[int] = Query(None, description="Return events older than this id"),
    limit: int = Query(50, ge=1, le=200),
    access: Access = Depends(require("integrations.manage")), db: AsyncSession = Depends(get_db_session),
):
    """Every attempt to send a document through this channel, newest first, with the reason when it was refused."""
    channel = await _channel(db, access, channel_id)
    stmt = select(IntakeEvent).where(IntakeEvent.channel_id == channel.id)
    if outcome:
        stmt = stmt.where(IntakeEvent.outcome == outcome)
    if before:
        stmt = stmt.where(IntakeEvent.id < before)
    rows = list((await db.execute(stmt.order_by(IntakeEvent.id.desc()).limit(limit + 1))).scalars().all())
    more = len(rows) > limit
    return {"items": [{"id": e.id, "outcome": e.outcome, "reason": e.reason, "filename": e.filename, "size_bytes": e.size_bytes, "external_id": e.external_id,
                       "document_id": e.document_id, "sender": e.sender, "ip": e.ip, "via": e.via, "at": e.created_at} for e in rows[:limit]],
            "next": rows[limit - 1].id if more else None}


def _sample_image(workspace: str) -> bytes:
    from PIL import Image, ImageDraw
    image = Image.new("RGB", (640, 360), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle([12, 12, 627, 347], outline="black", width=2)
    for i, line in enumerate(["TEST DOCUMENT", f"Sent from the web app to {workspace[:36]}", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                              "Not a real document. It is never published."]):
        draw.text((32, 40 + i * 44), line, fill="black")
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


class TestIn(BaseModel):
    process: bool = Field(False, description="Also let the agent read it (uses the agent; the document is marked as a test)")


@router.post("/{channel_id}/test-document", status_code=201)
async def send_test_document(channel_id: str, body: TestIn, access: Access = Depends(require("integrations.manage")), db: AsyncSession = Depends(get_db_session)):
    """Sends a small generated picture through the channel's real path, as a test: it is counted nowhere and never published."""
    channel = await _channel(db, access, channel_id)  # (channel_id stays the plain text id from the path)
    template = access.scope.template
    tenant_id, template_id = template.tenant_id, template.id
    sender = intake_service.Sender(label=f"Test from {access.auth.username or 'the web app'}", via="api", test=True)
    content = _sample_image(template.name)
    try:
        done = await intake_service.accept(
            db, channel=channel, template=template, content=content, filename="test-document.png", sender=sender, process=body.process,
            actor_id=access.auth.user_id, actor_type="user",
        )
        await db.commit()
    except IntakeRejected as rejected:
        await db.rollback()
        await intake_service.reject(tenant_id=tenant_id, template_id=template_id, channel_id=channel_id, sender=sender,
                                    reason=rejected.reason, filename="test-document.png", size=len(content))
        raise HTTPException(status_code=rejected.status, detail=rejected.reason)
    await intake_service.finish(done, channel, sender)
    return {"document_id": done.document.id, "status": intake_service.sender_status(done.document)}
