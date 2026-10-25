"""
Destinations: the systems approved data is sent to. People with `integrations.manage` connect one (credentials are sealed and never come
back), say what it receives (a mapping) and when (a policy), check it works, and pause or resume it.
"""
import secrets as token
from datetime import datetime, timedelta, timezone
from dataclasses import asdict
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.auth import require_recent_login
from app.core.config import settings
from app.core.database import get_db_session
from app.core.permissions import Access, require
from app.core.secrets import seal, unseal
from app.models.audit import AuditLog
from app.models.delivery import Delivery, Destination
from app.models.document import Document, DocumentRole, DocumentStatus
from app.models.tenant import Tenant
from app.models.workspace import Workspace
from app.services import delivery_service, formula, mapping_service, settings_service
from app.services.destinations import ADAPTERS, http, oauth
from app.services.source_data import SourceError, _check_host

router = APIRouter(prefix="/destinations", tags=["Connections: publish"])

POLICY_KEYS = {"send_updates": bool, "include_test": bool, "max_attempts": int}


def _audit(db: AsyncSession, access: Access, event: str, dest: Destination, **details: Any) -> None:
    db.add(AuditLog(tenant_id=access.scope.tenant_id, template_id=access.scope.template_id, event_type=event, entity_type="Destination",
                    entity_id=dest.id, actor=access.auth.user_id, details={"destination": dest.name, "kind": dest.kind, **details}))


def _adapter(kind: str):
    adapter = ADAPTERS.get(kind)
    if adapter is None:
        raise HTTPException(status_code=422, detail=f"'{kind}' is not a system Syruss can send to. Choose one from the list.")
    return adapter


def _policy(raw: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, kind in POLICY_KEYS.items():
        if key in raw and raw[key] is not None:
            try:
                out[key] = kind(raw[key])
            except (TypeError, ValueError):
                raise HTTPException(status_code=422, detail=f"The setting '{key}' is not valid.")
    if "max_attempts" in out and not 1 <= out["max_attempts"] <= 20:
        raise HTTPException(status_code=422, detail="Tries must be between 1 and 20.")
    return out


async def _check_addresses(adapter, config: Dict[str, Any]) -> None:
    for f in adapter.fields:
        if f.kind == "url" and config.get(f.key):
            try:
                await _check_host(config[f.key])
            except SourceError as exc:
                raise HTTPException(status_code=422, detail=f"{f.label}: {exc}")


def _check_mapping(adapter, mapping: Dict[str, Any], schema: Dict[str, Any]) -> None:
    if adapter.targets and (mapping or {}).get("mode") != "mapped":
        raise HTTPException(status_code=422, detail=f"{adapter.label} needs its fields mapped. Choose which field of your documents fills each one.")
    if mapping:
        problems = mapping_service.check(mapping, schema, [t.key for t in adapter.targets if not t.line] if adapter.targets else None)
        if problems:
            raise HTTPException(status_code=422, detail={"message": "The mapping has problems.", "problems": problems})


async def _counts(db: AsyncSession, template_id: str) -> Dict[str, Dict[str, int]]:
    since = datetime.now(timezone.utc) - timedelta(hours=24)
    rows = (await db.execute(select(Delivery.destination_id, Delivery.status, func.count(Delivery.id)).where(Delivery.template_id == template_id)
                             .group_by(Delivery.destination_id, Delivery.status))).all()
    recent = dict((await db.execute(select(Delivery.destination_id, func.count(Delivery.id)).where(
        Delivery.template_id == template_id, Delivery.status == "delivered", Delivery.delivered_at >= since).group_by(Delivery.destination_id))).all())
    out: Dict[str, Dict[str, int]] = {}
    for dest_id, status, n in rows:
        c = out.setdefault(dest_id, {"delivered": 0, "held": 0, "waiting": 0, "dead": 0, "delivered_24h": 0})
        key = {"delivered": "delivered", "held": "held", "dead": "dead"}.get(status, "waiting" if status in ("pending", "retrying", "sending") else None)
        if key:
            c[key] += n
    for dest_id, n in recent.items():
        out.setdefault(dest_id, {"delivered": 0, "held": 0, "waiting": 0, "dead": 0, "delivered_24h": 0})["delivered_24h"] = n
    return out


def health_of(d: Destination, counts: Dict[str, int]) -> Dict[str, str]:
    if d.status == "paused":
        return {"tone": "warning" if d.paused_by == "system" else "neutral", "label": "Paused automatically" if d.paused_by == "system" else "Paused"}
    if counts.get("dead"):
        return {"tone": "negative", "label": f"{counts['dead']} could not be sent"}
    if d.consecutive_failures:
        return {"tone": "warning", "label": "Having trouble"}
    return {"tone": "positive", "label": "Working" if d.last_success_at else "Ready"}


def _dest_dict(d: Destination, counts: Optional[Dict[str, int]] = None) -> Dict[str, Any]:
    adapter = ADAPTERS.get(d.kind)
    stored = unseal(d.secrets)
    counts = counts or {"delivered": 0, "held": 0, "waiting": 0, "dead": 0, "delivered_24h": 0}
    return {
        "id": d.id, "name": d.name, "kind": d.kind, "label": adapter.label if adapter else d.kind, "category": adapter.category if adapter else "custom",
        "status": d.status, "paused_reason": d.paused_reason, "paused_by": d.paused_by, "config": d.config or {},
        "secrets_set": sorted(k for k, v in stored.items() if v and k in {f.key for f in (adapter.fields if adapter else [])} | {"signing_secret", "access_token", "refresh_token"}),
        "mapping": d.mapping or {}, "policy": {"send_updates": True, "include_test": False, "max_attempts": settings.DELIVERY_MAX_ATTEMPTS, **(d.policy or {})},
        "consecutive_failures": d.consecutive_failures, "last_success_at": d.last_success_at, "last_failure_at": d.last_failure_at, "last_error": d.last_error,
        "created_by": d.created_by, "created_at": d.created_at, "counts": counts, "health": health_of(d, counts),
        "connected": bool(stored.get("refresh_token") or stored.get("access_token")) if adapter and adapter.uses_oauth else None,
    }


async def _dest(db: AsyncSession, access: Access, dest_id: str) -> Destination:
    d = await db.get(Destination, dest_id)
    if d is None or d.template_id != access.scope.template_id:
        raise HTTPException(status_code=404, detail="Destination not found.")
    return d


@router.get("/catalogue")
async def catalogue(access: Access = Depends(require("integrations.manage"))):
    """Every kind of system Syruss can send to, with what connecting to it asks for."""
    return [{**a.describe(), "redirect_uri": _redirect_uri() if a.uses_oauth else None} for a in ADAPTERS.values()]


@router.get("/sources")
async def sources(access: Access = Depends(require("integrations.manage")), db: AsyncSession = Depends(get_db_session)):
    """What a mapping can read from: this workspace's objects and fields, and a few approved documents to try a mapping on."""
    schema = access.scope.template.schema_definition or {}
    docs = (await db.execute(select(Document).where(Document.template_id == access.scope.template_id, Document.role == DocumentRole.DOCUMENT,
                                                    Document.status == DocumentStatus.COMPLETED).order_by(Document.updated_at.desc()).limit(15))).scalars().all()
    return {
        "objects": [{"type": o["type"], "multiple": bool(o.get("multiple")), "fields": [{"name": f["name"], "type": f.get("type", "string")} for f in o.get("fields", [])]}
                    for o in schema.get("objects", [])],
        "documents": [{"id": d.id, "filename": d.original_filename, "external_id": d.external_id} for d in docs],
        "functions": formula.functions_help(),
    }


class DestinationIn(BaseModel):
    kind: str
    name: str = Field(..., min_length=1, max_length=120)
    config: Dict[str, Any] = Field(default_factory=dict)
    secrets: Dict[str, Any] = Field(default_factory=dict)
    mapping: Dict[str, Any] = Field(default_factory=dict)
    policy: Dict[str, Any] = Field(default_factory=dict)


class DestinationPatch(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=120)
    config: Optional[Dict[str, Any]] = None
    secrets: Optional[Dict[str, Any]] = None
    mapping: Optional[Dict[str, Any]] = None
    policy: Optional[Dict[str, Any]] = None


class TryIn(BaseModel):
    kind: str
    config: Dict[str, Any] = Field(default_factory=dict)
    secrets: Dict[str, Any] = Field(default_factory=dict)
    destination_id: Optional[str] = Field(None, description="When editing: keep the stored secrets for anything not sent")


@router.get("")
async def list_destinations(access: Access = Depends(require("integrations.manage")), db: AsyncSession = Depends(get_db_session)):
    rows = (await db.execute(select(Destination).where(Destination.template_id == access.scope.template_id).order_by(Destination.created_at))).scalars().all()
    counts = await _counts(db, access.scope.template_id)
    return [_dest_dict(d, counts.get(d.id)) for d in rows]


@router.get("/{dest_id}")
async def get_destination(dest_id: str, access: Access = Depends(require("integrations.manage")), db: AsyncSession = Depends(get_db_session)):
    d = await _dest(db, access, dest_id)
    return _dest_dict(d, (await _counts(db, access.scope.template_id)).get(d.id))


@router.post("", status_code=201, dependencies=[Depends(require_recent_login)])
async def create_destination(body: DestinationIn, access: Access = Depends(require("integrations.manage")), db: AsyncSession = Depends(get_db_session)):
    """Connects a system. A webhook's signing secret is returned once, here."""
    adapter = _adapter(body.kind)
    await settings_service.check_limit(db, access.scope.template_id, "destinations")
    config = adapter.normalise(body.config)
    stored = {k: v for k, v in body.secrets.items() if v not in (None, "")}
    if adapter.kind == "webhook":
        stored["signing_secret"] = f"whsec_{token.token_urlsafe(32)}"
    problems = adapter.required_problems(config, stored)
    if problems:
        raise HTTPException(status_code=422, detail={"message": "Some details are missing.", "problems": problems})
    await _check_addresses(adapter, config)
    mapping = body.mapping or ({} if not adapter.targets else {})
    _check_mapping(adapter, mapping, access.scope.template.schema_definition or {})
    dest = Destination(tenant_id=access.scope.tenant_id, template_id=access.scope.template_id, name=body.name.strip(), kind=adapter.kind, config=config,
                       secrets=seal(stored), mapping=mapping, policy=_policy(body.policy), created_by=access.auth.username or access.auth.user_id)
    if adapter.uses_oauth:
        # Nothing can be sent until the account is connected: it waits, paused, and starts by itself once connected.
        dest.status, dest.paused_reason, dest.paused_by = "paused", "Connect your account to start sending.", "setup"
    db.add(dest)
    await db.flush()
    _audit(db, access, "DESTINATION_CREATED", dest)
    await db.commit()
    out = _dest_dict(dest)
    if adapter.kind == "webhook":
        out["signing_secret"] = stored["signing_secret"]
    return out


@router.patch("/{dest_id}", dependencies=[Depends(require_recent_login)])
async def update_destination(dest_id: str, body: DestinationPatch, access: Access = Depends(require("integrations.manage")), db: AsyncSession = Depends(get_db_session)):
    d = await _dest(db, access, dest_id)
    adapter = _adapter(d.kind)
    changed: List[str] = []
    if body.name is not None:
        d.name = body.name.strip()
        changed.append("name")
    stored = unseal(d.secrets)
    if body.config is not None:
        d.config = adapter.normalise({**(d.config or {}), **body.config})
        changed.append("connection")
    if body.secrets is not None:
        stored.update({k: v for k, v in body.secrets.items() if v not in (None, "")})
        d.secrets = seal(stored)
        changed.append("credentials")
    if body.config is not None or body.secrets is not None:
        problems = adapter.required_problems(d.config or {}, stored)
        if problems:
            raise HTTPException(status_code=422, detail={"message": "Some details are missing.", "problems": problems})
        await _check_addresses(adapter, d.config or {})
    if body.mapping is not None:
        _check_mapping(adapter, body.mapping, access.scope.template.schema_definition or {})
        d.mapping = body.mapping
        changed.append("mapping")
    if body.policy is not None:
        d.policy = {**(d.policy or {}), **_policy(body.policy)}
        changed.append("rules")
    _audit(db, access, "DESTINATION_UPDATED", d, changed=changed)
    await db.commit()
    return _dest_dict(d, (await _counts(db, access.scope.template_id)).get(d.id))


@router.delete("/{dest_id}", status_code=204, dependencies=[Depends(require_recent_login)])
async def delete_destination(dest_id: str, access: Access = Depends(require("integrations.manage")), db: AsyncSession = Depends(get_db_session)):
    """Removes the destination and its delivery history."""
    d = await _dest(db, access, dest_id)
    _audit(db, access, "DESTINATION_DELETED", d)
    await db.delete(d)
    await db.commit()


@router.post("/{dest_id}/pause")
async def pause(dest_id: str, access: Access = Depends(require("integrations.manage")), db: AsyncSession = Depends(get_db_session)):
    d = await _dest(db, access, dest_id)
    await delivery_service.pause_destination(db, d, "Paused by " + (access.auth.username or "a person") + ". Deliveries wait until you resume it.", "user")
    _audit(db, access, "DESTINATION_PAUSED", d)
    await db.commit()
    await db.refresh(d)
    return _dest_dict(d, (await _counts(db, access.scope.template_id)).get(d.id))


@router.post("/{dest_id}/resume")
async def resume(dest_id: str, access: Access = Depends(require("integrations.manage")), db: AsyncSession = Depends(get_db_session)):
    d = await _dest(db, access, dest_id)
    await delivery_service.resume_destination(db, d)
    _audit(db, access, "DESTINATION_RESUMED", d)
    await db.commit()
    delivery_service.delivery_worker.notify()
    return _dest_dict(d, (await _counts(db, access.scope.template_id)).get(d.id))


class RotateIn(BaseModel):
    overlap_hours: int = Field(24, ge=0, le=168)


@router.post("/{dest_id}/rotate-secret", dependencies=[Depends(require_recent_login)])
async def rotate_signing_secret(dest_id: str, body: RotateIn, access: Access = Depends(require("integrations.manage")), db: AsyncSession = Depends(get_db_session)):
    """A new webhook signing secret. For the overlap, requests carry signatures from both, so the receiver can switch without a gap."""
    d = await _dest(db, access, dest_id)
    if d.kind != "webhook":
        raise HTTPException(status_code=409, detail="Only webhooks are signed with a secret.")
    stored = unseal(d.secrets)
    stored["previous_secret"] = stored.get("signing_secret")
    stored["previous_until"] = (datetime.now(timezone.utc) + timedelta(hours=body.overlap_hours)).isoformat() if body.overlap_hours else None
    stored["signing_secret"] = f"whsec_{token.token_urlsafe(32)}"
    d.secrets = seal(stored)
    _audit(db, access, "DESTINATION_SECRET_ROTATED", d, overlap_hours=body.overlap_hours)
    await db.commit()
    return {"signing_secret": stored["signing_secret"], "overlap_hours": body.overlap_hours}


@router.post("/try")
async def try_connection(body: TryIn, access: Access = Depends(require("integrations.manage")), db: AsyncSession = Depends(get_db_session)):
    """Checks a connection that is not saved yet (or, when editing, with the stored credentials filling in what is not sent)."""
    adapter = _adapter(body.kind)
    config, stored = adapter.normalise(body.config), {k: v for k, v in body.secrets.items() if v not in (None, "")}
    if body.destination_id:
        existing = await _dest(db, access, body.destination_id)
        stored = {**unseal(existing.secrets), **stored}
        config = {**(existing.config or {}), **config}
    if adapter.kind == "webhook":
        stored.setdefault("signing_secret", "whsec_test")
    problems = adapter.required_problems(config, stored)
    if problems:
        return {"ok": False, "message": " ".join(problems), "detail": None}
    await _check_addresses(adapter, config)
    result = await adapter.test(config, stored, body.destination_id)
    return {"ok": result.ok, "message": result.message, "detail": result.detail}


class PreviewIn(BaseModel):
    kind: str
    mapping: Dict[str, Any] = Field(default_factory=dict)
    document_id: str


@router.post("/preview")
async def preview(body: PreviewIn, access: Access = Depends(require("integrations.manage")), db: AsyncSession = Depends(get_db_session)):
    """Exactly what a destination would be sent for a real document, with this mapping. Nothing is sent."""
    adapter = _adapter(body.kind)
    doc = await db.get(Document, body.document_id)
    if doc is None or doc.template_id != access.scope.template_id or doc.extraction is None:
        raise HTTPException(status_code=404, detail="That document has no record to preview.")
    result = doc.extraction
    check = mapping_service.check(body.mapping, access.scope.template.schema_definition or {}, [t.key for t in adapter.targets if not t.line] if adapter.targets else None) if body.mapping else []
    fake = Delivery(id="preview", revision=max(result.revision, 1), event="document.completed", snapshot=delivery_service._snapshot(doc, result, "preview"))
    stand_in = Destination(kind=body.kind, mapping=body.mapping)
    envelope, problems = delivery_service.build_envelope(fake, stand_in, {"id": access.scope.template_id, "name": access.scope.template.name})
    return {"envelope": envelope, "problems": check + problems}


class SuggestIn(BaseModel):
    kind: str


@router.post("/suggest")
async def suggest_mapping(body: SuggestIn, access: Access = Depends(require("integrations.manage"))):
    """A first mapping for a system that wants specific fields: each one filled from the field of this template that looks most like it."""
    adapter = _adapter(body.kind)
    if not adapter.targets:
        return {"mode": "record"}
    return mapping_service.guess(access.scope.template.schema_definition or {}, [asdict(t) for t in adapter.targets])


# --- connecting an account (QuickBooks, Xero) ---------------------------------------------------------------

def _redirect_uri() -> str:
    return f"{(settings.OAUTH_REDIRECT_BASE or settings.PUBLIC_API_URL).rstrip('/')}{settings.API_V1_PREFIX}/destinations/oauth/callback"


@router.post("/{dest_id}/oauth/start", dependencies=[Depends(require_recent_login)])
async def oauth_start(dest_id: str, access: Access = Depends(require("integrations.manage")), db: AsyncSession = Depends(get_db_session)):
    """The address of the vendor's own page where a person approves access. Syruss never sees their password there."""
    d = await _dest(db, access, dest_id)
    adapter = _adapter(d.kind)
    stored = unseal(d.secrets)
    if not adapter.uses_oauth or d.kind not in oauth.PROVIDERS:
        raise HTTPException(status_code=409, detail="This kind of destination doesn't sign in this way.")
    if not (d.config or {}).get("client_id") or not stored.get("client_secret"):
        raise HTTPException(status_code=422, detail="Save the client ID and client secret first.")
    state, nonce = oauth.new_state(d.id, d.tenant_id)
    stored["oauth_nonce"] = nonce
    d.secrets = seal(stored)
    await db.commit()
    return {"url": oauth.authorize_url(d.kind, d.config["client_id"], _redirect_uri(), state)}


@router.get("/oauth/callback", include_in_schema=False)
async def oauth_callback(code: Optional[str] = None, state: str = "", realmId: Optional[str] = None, error: Optional[str] = None, db: AsyncSession = Depends(get_db_session)):
    """Where the vendor sends the person back. Not signed in: the sealed, single-use state says whose connection this is."""
    from fastapi.responses import RedirectResponse
    claim = oauth.read_state(state)
    d = await db.get(Destination, claim["d"]) if claim else None
    if d is None or d.tenant_id != claim["t"]:
        return RedirectResponse(f"{settings.PUBLIC_SITE_URL.rstrip('/')}/", status_code=303)
    stored = unseal(d.secrets)
    tenant, workspace = await db.get(Tenant, d.tenant_id), await db.get(Workspace, d.template_id)
    back = f"{settings.PUBLIC_SITE_URL.rstrip('/')}/{tenant.slug if tenant else ''}/w/{workspace.slug if workspace else ''}/connections/publish/{d.id}"

    def leave(message: str):
        from urllib.parse import quote
        return RedirectResponse(f"{back}?connect_error={quote(message)}", status_code=303)

    if stored.get("oauth_nonce") != claim["n"]:
        return leave("That sign-in link was already used or has expired. Try connecting again.")
    stored.pop("oauth_nonce", None)
    if error or not code:
        d.secrets = seal(stored)
        await db.commit()
        return leave("Access was not given." if error == "access_denied" else f"The sign-in did not finish ({error or 'no code'}).")
    tokens, problem = await oauth.exchange_code(d.kind, (d.config or {}).get("client_id", ""), stored.get("client_secret", ""), code, _redirect_uri())
    if problem or tokens is None:
        d.secrets = seal(stored)
        await db.commit()
        return leave(f"Could not finish signing in: {problem}")
    config = dict(d.config or {})
    if d.kind == "quickbooks":
        if not realmId:
            return leave("QuickBooks did not say which company was connected. Try again.")
        config["realm_id"] = realmId
    elif d.kind == "xero":
        reply = await http.call("GET", "https://api.xero.com/connections", headers={"Authorization": f"Bearer {tokens['access_token']}", "Accept": "application/json"})
        orgs = reply.json() if reply.ok and isinstance(reply.json(), list) else []
        if not orgs:
            return leave("Xero did not list an organisation for this connection. Choose one when you approve access.")
        config["tenant_id"] = orgs[0]["tenantId"]
    d.config = config
    d.secrets = seal({**stored, **tokens})
    if d.status == "paused" and d.paused_by == "setup":
        d.status, d.paused_reason, d.paused_by = "active", None, None
    db.add(AuditLog(tenant_id=d.tenant_id, template_id=d.template_id, event_type="DESTINATION_CONNECTED", entity_type="Destination", entity_id=d.id, actor="oauth",
                    details={"destination": d.name, "kind": d.kind}))
    await db.commit()
    delivery_service.delivery_worker.notify()
    return RedirectResponse(f"{back}?connected=1", status_code=303)
