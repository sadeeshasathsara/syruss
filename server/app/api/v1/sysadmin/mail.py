"""
Email (the admin app's /email page), the staff's side of the mail service (mail/): which accounts Syruss sends and reads mail through,
which account each purpose uses, connecting a Google account, and the delivery log.

Owners only (staff.manage): the delivery log shows customers' addresses, and accounts send as Syruss. Every change needs
a recent sign-in and is written to the platform audit log. The mail service keeps the credentials; nothing here ever
sees a password or a token after it is handed over.
"""
from typing import Any, Dict, List, Literal, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from app.api.v1.sysadmin.access import Staff, record, require_staff
from app.core.config import settings
from app.core.database import get_db_session
from app.services import mail_service

router = APIRouter(prefix="/mail")
READ = require_staff("staff.manage")
CHANGE = require_staff("staff.manage", recent=True)
PURPOSES = ("notifications", "accounts", "support")


def redirect_uri() -> str:
    return settings.PUBLIC_ADMIN_URL.rstrip("/") + "/email/google"


async def _call(coro):
    try:
        return await coro
    except mail_service.MailServiceError as e:
        raise HTTPException(status_code=e.status if e.status < 500 else 502, detail=str(e))


@router.get("/overview")
async def overview(staff: Staff = Depends(READ)) -> Dict[str, Any]:
    """Each purpose's account now, every account (no secrets), and whether Google can be connected."""
    status = await _call(mail_service.call("GET", "/status"))
    accounts = await _call(mail_service.call("GET", "/accounts"))
    return {"purposes": status["purposes"], "google": status["google"], "accounts": accounts, "redirect_uri": redirect_uri()}


@router.get("/jobs")
async def jobs(status: Optional[Literal["queued", "sending", "retrying", "sent", "dead"]] = None, limit: int = Query(100, ge=1, le=500),
               staff: Staff = Depends(READ)) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {"limit": limit}
    if status:
        params["status"] = status
    return await _call(mail_service.call("GET", "/jobs", params=params))


@router.post("/jobs/{job_id}/retry")
async def retry(job_id: str, staff: Staff = Depends(CHANGE), db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    out = await _call(mail_service.call("POST", f"/jobs/{job_id}/retry"))
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


@router.post("/accounts/imap", status_code=201)
async def add_imap(body: ImapIn, staff: Staff = Depends(CHANGE), db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    """A mailbox on any mail server: SMTP to send, IMAP to read (read-only: it is never changed)."""
    made = await _call(mail_service.call("POST", "/accounts/imap", json={**body.model_dump(), "by": staff.user_id}))
    record(db, staff, "mail.account_added", "mail_account", made["id"], details={"address": made["address"], "kind": "smtp_imap",
                                                                                   "granted": made["granted"]})
    await db.commit()
    return made


@router.post("/accounts/{account_id}/test")
async def test(account_id: str, purpose: Optional[str] = None, staff: Staff = Depends(READ)) -> Dict[str, Any]:
    return await _call(mail_service.call("POST", f"/accounts/{account_id}/test", params={"purpose": purpose} if purpose else None))


@router.delete("/accounts/{account_id}", status_code=204)
async def remove(account_id: str, staff: Staff = Depends(CHANGE), db: AsyncSession = Depends(get_db_session)) -> None:
    """Removes it; the purposes it served use the account from the environment again. A Google sign-in is revoked."""
    await _call(mail_service.call("DELETE", f"/accounts/{account_id}"))
    record(db, staff, "mail.account_removed", "mail_account", account_id)
    await db.commit()


class RouteIn(BaseModel):
    account_id: Optional[str] = None  # None: the account from the environment


@router.put("/routes/{purpose}")
async def route(purpose: Literal["notifications", "accounts", "support"], body: RouteIn, staff: Staff = Depends(CHANGE),
                db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    out = await _call(mail_service.call("PUT", f"/routes/{purpose}", json=body.model_dump()))
    record(db, staff, "mail.route_changed", "mail_purpose", purpose, details={"account_id": body.account_id})
    await db.commit()
    return out


class GoogleStartIn(BaseModel):
    purposes: List[Literal["notifications", "accounts", "support"]] = Field(..., min_length=1)


@router.post("/google/start")
async def google_start(body: GoogleStartIn, staff: Staff = Depends(CHANGE)) -> Dict[str, str]:
    """Google's sign-in page, asking only for what the chosen purposes need. It comes back to the admin app's
    /email/google, which finishes here."""
    return await _call(mail_service.call("POST", "/google/start", json={"purposes": body.purposes, "redirect_uri": redirect_uri(),
                                                                       "by": staff.user_id}))


class GoogleFinishIn(BaseModel):
    code: str = Field(..., min_length=1, max_length=2048)
    state: str = Field(..., min_length=1, max_length=4096)


@router.post("/google/finish")
async def google_finish(body: GoogleFinishIn, staff: Staff = Depends(CHANGE), db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    done = await _call(mail_service.call("POST", "/google/finish", json={**body.model_dump(), "redirect_uri": redirect_uri(),
                                                                        "by": staff.user_id}))
    record(db, staff, "mail.google_connected", "mail_account", done["id"],
           details={"address": done["address"], "granted": done["granted"], "purposes": done["purposes"]})
    await db.commit()
    return done
