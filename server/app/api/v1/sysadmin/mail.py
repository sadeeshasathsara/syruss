"""
Email (the admin app's /email page), the staff's side of the mail service (mail/): which accounts Syruss sends and reads mail through,
which account each job uses, jobs of its own, connecting Google or Microsoft, the apps they are connected through, and the delivery log.

Everything goes over Kafka (services/mail_bus.py): what the page shows is the mail service's own directory, and each change is a signed
command the mail service carries out and answers. Owners only (staff.manage): the delivery log shows customers' addresses, and
accounts send as Syruss. Every change needs a recent sign-in and is written to the platform audit log. The mail service keeps the
credentials; a password is sealed to its public key before it leaves this server (and, once the admin app seals it in the browser, never
reaches it at all).
"""
import asyncio
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.sysadmin.access import Staff, record, require_staff
from app.core.config import settings
from app.core.database import get_db_session
from app.services.mail_bus import MailBusError, bus

router = APIRouter(prefix="/mail")
READ = require_staff("staff.manage")
CHANGE = require_staff("staff.manage", recent=True)


Provider = Literal["google", "microsoft"]


def redirect_uri(provider: str = "google") -> str:
    """Where a provider sends the person back to: the admin app's /email/<provider> page, which finishes the connection here.
    It is the address to register as the redirect URI of the provider's OAuth app."""
    return settings.PUBLIC_ADMIN_URL.rstrip("/") + f"/email/{provider}"


async def _run(coro):
    try:
        return await coro
    except MailBusError as e:
        raise HTTPException(status_code=e.status, detail=str(e))


async def _command(kind: str, staff: Staff, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return await _run(bus.command(kind, payload, by=staff.user_id))


async def _settled() -> None:
    """The directory follows a change a moment after its answer: give it that moment before reading it back."""
    await asyncio.sleep(0.4)


@router.get("/overview")
async def overview(staff: Staff = Depends(READ)) -> Dict[str, Any]:
    """Each job and its account now, every account (no secrets), the key secrets are sealed to, and whether Google can be connected."""
    if not await bus.ready():
        raise HTTPException(status_code=503, detail="The mail service hasn't been heard from. Is it running?")
    out = bus.overview()
    for provider, info in out["providers"].items():
        info["redirect_uri"] = redirect_uri(provider)
    return {**out, "redirect_uri": redirect_uri("google")}


@router.get("/jobs")
async def delivery_log(status: Optional[Literal["queued", "sending", "retrying", "sent", "dead"]] = None, limit: int = Query(100, ge=1, le=500),
                       staff: Staff = Depends(READ)) -> List[Dict[str, Any]]:
    """The delivery log: each message sent (who, what about, how it went; never its text)."""
    return (await _command("jobs.log", staff, {"status": status, "limit": limit})).get("jobs", [])


@router.post("/jobs/{job_id}/retry")
async def retry(job_id: str, staff: Staff = Depends(CHANGE), db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    out = await _command("job.retry", staff, {"id": job_id})
    record(db, staff, "mail.retried", "mail_job", job_id)
    await db.commit()
    return out


class ImapIn(BaseModel):
    address: str = Field(..., min_length=3, max_length=320)
    name: str = Field("Syruss", max_length=120)
    smtp_host: Optional[str] = None
    smtp_port: int = Field(587, ge=1, le=65535)
    smtp_security: Literal["none", "starttls", "ssl"] = "starttls"
    smtp_username: str = ""
    smtp_password: str = ""
    imap_host: Optional[str] = None
    imap_port: int = Field(993, ge=1, le=65535)
    imap_ssl: bool = True
    imap_folder: str = "INBOX"
    imap_username: str = ""
    imap_password: str = ""
    # The passwords sealed to the mail service's key by the caller (the admin app does this in the browser); when given, the
    # plain password fields above are not used.
    sealed: Optional[str] = Field(None, max_length=4096)
    jobs: List[str] = Field(default_factory=list, max_length=20)   # the jobs it serves from the start


@router.post("/accounts/imap", status_code=201)
async def add_imap(body: ImapIn, staff: Staff = Depends(CHANGE), db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    """A mailbox on any mail server: SMTP to send, IMAP to read (read-only: it is never changed)."""
    payload = {k: v for k, v in body.model_dump().items() if k not in ("smtp_password", "imap_password", "sealed")}
    payload["sealed"] = body.sealed or (await _run(_seal({"smtp_password": body.smtp_password, "imap_password": body.imap_password}))
                                        if (body.smtp_password or body.imap_password) else None)
    if not payload["sealed"]:
        payload.pop("sealed")
    made = await _command("account.add_imap", staff, payload)
    record(db, staff, "mail.account_added", "mail_account", made["id"], details={"address": made["address"], "kind": "smtp_imap",
                                                                                   "granted": made["granted"]})
    await db.commit()
    await _settled()
    return made


async def _seal(secrets: Dict[str, Any]) -> str:
    await bus.ready()
    return bus.seal(secrets)


@router.post("/accounts/{account_id}/test")
async def test(account_id: str, purpose: Optional[str] = None, staff: Staff = Depends(READ)) -> Dict[str, Any]:
    return await _command("account.test", staff, {"account_id": account_id, "purpose": purpose})


@router.delete("/accounts/{account_id}", status_code=204)
async def remove(account_id: str, staff: Staff = Depends(CHANGE), db: AsyncSession = Depends(get_db_session)) -> None:
    """Removes it; the jobs it served use the account from the environment again. A Google sign-in is revoked."""
    await _command("account.remove", staff, {"account_id": account_id})
    record(db, staff, "mail.account_removed", "mail_account", account_id)
    await db.commit()


class RouteIn(BaseModel):
    account_id: Optional[str] = None  # None: the account from the environment


@router.put("/routes/{purpose}")
async def route(purpose: str, body: RouteIn, staff: Staff = Depends(CHANGE), db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    await _command("job.route", staff, {"purpose": purpose, "account_id": body.account_id})
    record(db, staff, "mail.route_changed", "mail_purpose", purpose, details={"account_id": body.account_id})
    await db.commit()
    await _settled()
    return bus.overview()


class JobIn(BaseModel):
    key: str = Field(..., min_length=2, max_length=39)
    label: str = Field(..., min_length=1, max_length=120)
    direction: Literal["send", "read", "both"]
    description: str = Field("", max_length=500)
    settings: Dict[str, Any] = Field(default_factory=dict)


class JobChange(BaseModel):
    label: Optional[str] = Field(None, min_length=1, max_length=120)
    description: Optional[str] = Field(None, max_length=500)
    settings: Optional[Dict[str, Any]] = None


@router.post("/purposes", status_code=201)
async def create_job(body: JobIn, staff: Staff = Depends(CHANGE), db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    """A job of our own (the built-in ones are notifications, document inbound, promotions, help and account emails)."""
    out = await _command("job.create", staff, body.model_dump())
    record(db, staff, "mail.job_created", "mail_purpose", body.key, details={"label": body.label, "direction": body.direction})
    await db.commit()
    await _settled()
    return out


@router.put("/purposes/{key}")
async def update_job(key: str, body: JobChange, staff: Staff = Depends(CHANGE), db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    out = await _command("job.update", staff, {"key": key, **body.model_dump(exclude_none=True)})
    record(db, staff, "mail.job_changed", "mail_purpose", key, details={k: v for k, v in body.model_dump(exclude_none=True).items() if k != "settings"})
    await db.commit()
    await _settled()
    return out


@router.delete("/purposes/{key}", status_code=204)
async def delete_job(key: str, staff: Staff = Depends(CHANGE), db: AsyncSession = Depends(get_db_session)) -> None:
    await _command("job.delete", staff, {"key": key})
    record(db, staff, "mail.job_removed", "mail_purpose", key)
    await db.commit()


class UseIn(BaseModel):
    job: Optional[str] = Field(None, max_length=40)   # None: the function's default job


@router.put("/uses/{key}")
async def assign_use(key: str, body: UseIn, staff: Staff = Depends(CHANGE), db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    """Chooses the job an application function uses (the mail service refuses a job that can't do what the function does)."""
    await _command("use.assign", staff, {"key": key, "job": body.job})
    record(db, staff, "mail.use_assigned", "mail_use", key, details={"job": body.job})
    await db.commit()
    await _settled()
    return bus.overview()


class OAuthStartIn(BaseModel):
    purposes: List[str] = Field(..., min_length=1, max_length=20)


@router.post("/{provider}/start")
async def oauth_start(provider: Provider, body: OAuthStartIn, staff: Staff = Depends(CHANGE)) -> Dict[str, str]:
    """The provider's sign-in page, asking only for what the chosen jobs need. It comes back to the admin app's /email/<provider>, which finishes here."""
    return await _command(f"oauth.{provider}.start", staff, {"jobs": body.purposes, "redirect_uri": redirect_uri(provider)})


class OAuthFinishIn(BaseModel):
    code: str = Field(..., min_length=1, max_length=2048)
    state: str = Field(..., min_length=1, max_length=4096)


@router.post("/{provider}/finish")
async def oauth_finish(provider: Provider, body: OAuthFinishIn, staff: Staff = Depends(CHANGE),
                       db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    done = await _command(f"oauth.{provider}.finish", staff, {"state": body.state, "redirect_uri": redirect_uri(provider),
                                                               "sealed": await _run(_seal({"code": body.code}))})
    record(db, staff, f"mail.{provider}_connected", "mail_account", done["id"],
           details={"address": done["address"], "granted": done["granted"], "purposes": done["purposes"]})
    await db.commit()
    await _settled()
    return done


class AppIn(BaseModel):
    client_id: str = Field("", max_length=300)                    # blank: keep the one already set (it is never sent back to the browser)
    tenant: str = Field("", max_length=100)                       # Microsoft only: common, organizations, consumers, or a directory
    client_secret: str = Field("", max_length=500)                # plain: only for a caller that cannot seal it itself
    sealed: Optional[str] = Field(None, max_length=4096)           # {"client_secret": ...} sealed to the mail service's key (the admin app's browser does this)


@router.put("/apps/{provider}")
async def set_app(provider: Provider, body: AppIn, staff: Staff = Depends(CHANGE), db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    """The OAuth app this provider gave Syruss (its client id and secret). The secret is sealed to the mail service's key, never kept
    here, never shown again. Leave it blank to keep the one already set (when only the client id or tenant changed)."""
    payload: Dict[str, Any] = {"provider": provider, "client_id": body.client_id.strip(), "tenant": body.tenant.strip()}
    sealed = body.sealed or (await _run(_seal({"client_secret": body.client_secret})) if body.client_secret else None)
    if sealed:
        payload["sealed"] = sealed
    await _command("provider_app.set", staff, payload)
    record(db, staff, "mail.app_set", "mail_provider", provider)
    await db.commit()
    await _settled()
    return await overview(staff)


@router.delete("/apps/{provider}", status_code=204)
async def clear_app(provider: Provider, staff: Staff = Depends(CHANGE), db: AsyncSession = Depends(get_db_session)) -> None:
    await _command("provider_app.clear", staff, {"provider": provider})
    record(db, staff, "mail.app_cleared", "mail_provider", provider)
    await db.commit()
