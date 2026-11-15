"""
The mail service's internal API. Only the server calls it (Bearer MAIL_SERVICE_TOKEN), on the internal network; the
admin app's Email page reaches it through the server, which checks who may do what and keeps the audit log.
No answer ever carries a password or a token.

    GET    /status                     each purpose: the account it uses now, whether that is its own, can it read
    GET    /accounts                   the account from the environment and the connected ones
    POST   /accounts/imap              connect a mailbox on any mail server (SMTP to send, IMAP to read)
    POST   /accounts/{id}/test         sign in and check, for what a purpose needs
    DELETE /accounts/{id}              remove it (and its routes; a Google sign-in is revoked)
    PUT    /routes/{purpose}           which account a purpose uses (refused when it can't do what the purpose needs)
    POST   /google/start, /google/finish   connect a Google account (google.py)
    GET    /jobs, POST /jobs/{id}/retry    the delivery log, and sending one that was given up again
    GET    /inbound                    mail read from mailboxes, waiting for the server
    GET    /inbound/{id}               one, as it arrived (message/rfc822)
    DELETE /inbound/{id}               the server has taken it in
"""
import hmac
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from mail import google
from mail.accounts import ENV_ACCOUNT_ID, account_from_row
from mail.accounts.base import Capability, MailNotPermitted
from mail.config import settings
from mail.router import PURPOSES, Router
from mail.store import AccountRow, CursorRow, InboundRow, JobRow, RouteRow, Store, seal, unseal


def build_api(store: Store, router: Router) -> FastAPI:
    app = FastAPI(title="Syruss mail service", docs_url=None, redoc_url=None, openapi_url=None)

    def caller(authorization: str = Header("")) -> None:
        expected = settings.MAIL_SERVICE_TOKEN
        token = authorization.removeprefix("Bearer ").strip()
        if not expected or not hmac.compare_digest(token.encode(), expected.encode()):
            raise HTTPException(status_code=401, detail="Not the server.")

    guard = [Depends(caller)]

    async def describe(row: Optional[AccountRow], routes: Dict[str, str]) -> Dict[str, Any]:
        if row is None:
            a = router.fallback
            return {**a.describe(), "status": "active", "from_environment": True, "last_error": None,
                    "purposes": [p for p in PURPOSES if p not in routes]}
        return {"id": row.id, "kind": row.kind, "address": row.address, "name": row.name, "granted": row.granted, "status": row.status,
                "last_error": row.last_error, "checked_at": row.checked_at, "created_at": row.created_at, "from_environment": False,
                "config": {k: v for k, v in (row.config or {}).items() if "password" not in k},
                "purposes": [p for p, a in routes.items() if a == row.id]}

    @app.get("/health")
    async def health() -> Dict[str, str]:
        return {"status": "ok"}

    @app.get("/status", dependencies=guard)
    async def status() -> Dict[str, Any]:
        out = {}
        for purpose, needs in PURPOSES.items():
            account, routed = await router.account_for(purpose)
            out[purpose] = {"account_id": account.id, "address": account.address, "routed": routed, "needs": needs.names(),
                            "reads": Capability.READ in needs and Capability.READ in account.granted}
        return {"purposes": out, "google": google.configured()}

    @app.get("/accounts", dependencies=guard)
    async def accounts() -> List[Dict[str, Any]]:
        routes = await store.routes()
        return [await describe(None, routes)] + [await describe(r, routes) for r in await store.accounts()]

    class ImapIn(BaseModel):
        address: str = Field(..., min_length=3, max_length=320)
        name: str = Field("Syruss", max_length=120)
        smtp_host: Optional[str] = None
        smtp_port: int = 587
        smtp_security: Literal["none", "starttls", "ssl"] = "starttls"
        smtp_username: str = ""
        smtp_password: str = ""
        imap_host: Optional[str] = None
        imap_port: int = 993
        imap_ssl: bool = True
        imap_folder: str = "INBOX"
        imap_username: str = ""
        imap_password: str = ""
        by: str = ""

    @app.post("/accounts/imap", dependencies=guard, status_code=201)
    async def add_imap(body: ImapIn) -> Dict[str, Any]:
        if not body.smtp_host and not body.imap_host:
            raise HTTPException(status_code=422, detail="Give an SMTP server to send, an IMAP server to read, or both.")
        config = {k: v for k, v in body.model_dump().items() if k.startswith(("smtp_", "imap_")) and "password" not in k}
        row = AccountRow(id=f"imap-{uuid.uuid4().hex[:12]}", kind="smtp_imap", address=body.address.lower(), name=body.name, config=config,
                         secret=seal({"smtp_password": body.smtp_password, "imap_password": body.imap_password}), created_by=body.by,
                         status="active")
        row.granted = account_from_row(row, {}).granted.names()
        async with store.session() as s:
            s.add(row)
            await s.commit()
        return await describe(row, await store.routes())

    async def account_or_404(account_id: str):
        if account_id == ENV_ACCOUNT_ID:
            return None, router.fallback
        row = await store.account(account_id)
        if row is None:
            raise HTTPException(status_code=404, detail="No such mail account.")
        return row, account_from_row(row, unseal(row.secret), on_lost=store.lost)

    @app.post("/accounts/{account_id}/test", dependencies=guard)
    async def test(account_id: str, purpose: Optional[str] = Query(None)) -> Dict[str, Any]:
        row, account = await account_or_404(account_id)
        needs = PURPOSES.get(purpose or "", account.granted) if purpose else account.granted
        try:
            result = await account.open(needs).test()
        except MailNotPermitted as e:
            return {"ok": False, "detail": str(e)}
        if row is not None:
            async with store.session() as s:
                r = await s.get(AccountRow, row.id)
                r.checked_at, r.last_error = datetime.now(timezone.utc), None if result.ok else result.detail
                if result.ok and r.status == "needs_reconnect" and r.kind != "gmail":
                    r.status = "active"
                await s.commit()
        return {"ok": result.ok, "detail": result.detail}

    @app.delete("/accounts/{account_id}", dependencies=guard, status_code=204)
    async def remove(account_id: str) -> None:
        row, _ = await account_or_404(account_id)
        if row is None:
            raise HTTPException(status_code=409, detail="The account from the environment is changed in mail.env, not here.")
        if row.kind == "gmail":
            await google.revoke(unseal(row.secret).get("refresh_token", ""))
        async with store.session() as s:
            await s.execute(delete(RouteRow).where(RouteRow.account_id == row.id))
            await s.execute(delete(CursorRow).where(CursorRow.account_id == row.id))
            await s.execute(delete(AccountRow).where(AccountRow.id == row.id))
            await s.commit()

    class RouteIn(BaseModel):
        account_id: Optional[str] = None  # None: the account from the environment

    @app.put("/routes/{purpose}", dependencies=guard)
    async def set_route(purpose: str, body: RouteIn) -> Dict[str, Any]:
        if purpose not in PURPOSES:
            raise HTTPException(status_code=404, detail="No such purpose.")
        async with store.session() as s:
            if body.account_id in (None, ENV_ACCOUNT_ID):
                await s.execute(delete(RouteRow).where(RouteRow.purpose == purpose))
            else:
                row, account = await account_or_404(body.account_id)
                try:
                    Router.check(account, purpose)
                except MailNotPermitted as e:
                    raise HTTPException(status_code=422, detail=f"{e} The {purpose} purpose needs {', '.join(PURPOSES[purpose].names())}.")
                await s.merge(RouteRow(purpose=purpose, account_id=row.id))
            await s.commit()
        return await status()

    class StartIn(BaseModel):
        purposes: List[str]
        redirect_uri: str
        by: str

    @app.post("/google/start", dependencies=guard)
    async def google_start(body: StartIn) -> Dict[str, str]:
        try:
            return {"url": google.start(body.purposes, body.redirect_uri, body.by)}
        except google.ConnectError as e:
            raise HTTPException(status_code=422, detail=str(e))

    class FinishIn(BaseModel):
        code: str
        state: str
        redirect_uri: str
        by: str

    @app.post("/google/finish", dependencies=guard)
    async def google_finish(body: FinishIn) -> Dict[str, Any]:
        try:
            done = await google.finish(store, code=body.code, state=body.state, redirect_uri=body.redirect_uri, by=body.by)
        except google.ConnectError as e:
            raise HTTPException(status_code=422, detail=str(e))
        async with store.session() as s:  # it serves what it was connected for
            for purpose in done["purposes"]:
                await s.merge(RouteRow(purpose=purpose, account_id=done["id"]))
            await s.commit()
        return done

    @app.get("/jobs", dependencies=guard)
    async def jobs(status: Optional[str] = None, limit: int = Query(100, ge=1, le=500)) -> List[Dict[str, Any]]:
        async with store.session() as s:
            stmt = select(JobRow).order_by(JobRow.created_at.desc()).limit(limit)
            if status:
                stmt = stmt.where(JobRow.status == status)
            rows = (await s.execute(stmt)).scalars().all()
        return [{"id": r.id, "purpose": r.purpose, "to": r.to, "subject": r.subject, "status": r.status, "attempts": r.attempts,
                 "last_error": r.last_error, "account_id": r.account_id, "created_at": r.created_at, "done_at": r.done_at,
                 "next_attempt_at": r.next_attempt_at, "retryable": r.status == "dead" and r.payload is not None} for r in rows]

    @app.post("/jobs/{job_id}/retry", dependencies=guard)
    async def retry(job_id: str) -> Dict[str, Any]:
        async with store.session() as s:
            row = await s.get(JobRow, job_id)
            if row is None or row.status not in ("dead", "retrying"):
                raise HTTPException(status_code=409, detail="Only mail that was given up or is waiting can be sent again now.")
            if row.payload is None:
                raise HTTPException(status_code=409, detail="This message isn't kept any more; it can't be sent again.")
            row.status, row.attempts, row.next_attempt_at = "retrying", 0, datetime.now(timezone.utc)
            await s.commit()
        return {"id": job_id, "status": "retrying"}

    @app.get("/inbound", dependencies=guard)
    async def inbound() -> List[Dict[str, Any]]:
        async with store.session() as s:
            rows = (await s.execute(select(InboundRow.id, InboundRow.purpose, InboundRow.account_id).order_by(InboundRow.created_at).limit(100))).all()
        return [{"id": r.id, "purpose": r.purpose, "account_id": r.account_id} for r in rows]

    @app.get("/inbound/{item_id}", dependencies=guard)
    async def inbound_one(item_id: str) -> Response:
        async with store.session() as s:
            row = await s.get(InboundRow, item_id)
        if row is None:
            raise HTTPException(status_code=404, detail="Not waiting here.")
        return Response(content=row.raw, media_type="message/rfc822", headers={"X-Purpose": row.purpose})

    @app.delete("/inbound/{item_id}", dependencies=guard, status_code=204)
    async def inbound_done(item_id: str) -> None:
        async with store.session() as s:
            await s.execute(delete(InboundRow).where(InboundRow.id == item_id))
            await s.commit()

    return app
