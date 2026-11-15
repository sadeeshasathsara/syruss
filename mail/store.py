"""
The mail service's own database (MAIL_DB): the accounts connected in the admin app (their credentials sealed), which
account each purpose uses, every job's delivery record, and where each mailbox was last read.

A job's message is kept only until it is sent or given up: the delivery log keeps who, what about and how it went,
never the text (a sign-in code must not outlive its email).
"""
import base64
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy import JSON, Boolean, DateTime, Integer, LargeBinary, String, Text, delete, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from mail import sealing
from mail.config import settings
from mail.purposes import BUILTIN, BUILTIN_USES, Job, Use
from mail.sealing import seal, unseal  # noqa: F401  (the rest of the service imports them from here)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class AccountRow(Base):
    """A mailbox connected in the admin app. The one from the environment isn't stored: it is built from settings."""
    __tablename__ = "mail_accounts"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)          # smtp_imap | gmail | graph
    address: Mapped[str] = mapped_column(String(320), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    config: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)   # hosts and ports: nothing secret
    secret: Mapped[Optional[str]] = mapped_column(Text, nullable=True)        # sealed: passwords, refresh tokens
    granted: Mapped[list] = mapped_column(JSON, nullable=False, default=list)  # ["send", "read"]
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")  # active | needs_reconnect
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class PurposeRow(Base):
    """A job: a named use of mail (built in, or added in the admin app), and the account that serves it (none: nothing is connected for it)."""
    __tablename__ = "mail_purposes"
    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)       # send | read | both
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    settings: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    account_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class UseRow(Base):
    """A function of the application that needs mail, and the job it uses (purposes.Use)."""
    __tablename__ = "mail_uses"
    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    default_job: Mapped[str] = mapped_column(String(40), nullable=False)
    job_key: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)   # chosen in the admin app; none: the default
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now)


class SettingRow(Base):
    """What this service keeps for itself, sealed: its box keypair (what the admin app seals secrets to)."""
    __tablename__ = "mail_settings"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)


class CommandRow(Base):
    """Every command the server sent: the log of who asked for what, and how a repeated one is refused (the id is kept)."""
    __tablename__ = "mail_commands"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    type: Mapped[str] = mapped_column(String(60), nullable=False)
    by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, index=True)


class JobRow(Base):
    """One message to send, by the publisher's id (so a message published twice is sent once)."""
    __tablename__ = "mail_jobs"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    purpose: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    to: Mapped[str] = mapped_column(String(320), nullable=False)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)  # queued | sending | retrying | sent | dead
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    account_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    provider_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    payload: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # the message, sealed; dropped once done
    meta: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, index=True)
    done_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class CursorRow(Base):
    """Where a mailbox was last read, for one purpose (IMAP: UIDVALIDITY and the last UID; Gmail: a history id)."""
    __tablename__ = "mail_cursors"
    account_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    purpose: Mapped[str] = mapped_column(String(40), primary_key=True)
    cursor: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    read_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)


class InboundRow(Base):
    """Mail read from a mailbox, kept until the server collects it. `raw` is sealed (sealing.seal_bytes); rows kept before
    that was done are read as they are."""
    __tablename__ = "mail_inbound"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    purpose: Mapped[str] = mapped_column(String(40), nullable=False)
    account_id: Mapped[str] = mapped_column(String(64), nullable=False)
    raw: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, index=True)


# ---- the database ----------------------------------------------------------------------------------

class Store:
    def __init__(self, database_url: Optional[str] = None):
        self._engine = create_async_engine(database_url or settings.DATABASE_URL, pool_size=5, max_overflow=5)
        self.session = async_sessionmaker(self._engine, expire_on_commit=False, class_=AsyncSession)

    async def init(self) -> None:
        await self._ensure_database()
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await self._fold_routes(conn)
        await self.seed_jobs()
        await self.register_uses(BUILTIN_USES)

    @staticmethod
    async def _fold_routes(conn) -> None:
        """Older installs kept which account serves a job in a table of its own (mail_routes). It is now a column of the job: copy
        it over once, then drop the table. Safe to run on every start."""
        await conn.execute(text("ALTER TABLE mail_purposes ADD COLUMN IF NOT EXISTS account_id VARCHAR(64)"))
        if await conn.scalar(text("SELECT to_regclass('public.mail_routes') IS NOT NULL")):
            await conn.execute(text("UPDATE mail_purposes p SET account_id = r.account_id FROM mail_routes r WHERE r.purpose = p.key AND p.account_id IS NULL"))
            await conn.execute(text("DROP TABLE mail_routes"))

    async def _ensure_database(self) -> None:
        url = self._engine.url
        admin = create_async_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
        try:
            async with admin.connect() as conn:
                exists = await conn.scalar(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": url.database})
                if not exists:
                    await conn.execute(text(f'CREATE DATABASE "{url.database}"'))
        finally:
            await admin.dispose()

    async def close(self) -> None:
        await self._engine.dispose()

    async def accounts(self) -> List[AccountRow]:
        async with self.session() as s:
            return list((await s.execute(select(AccountRow).order_by(AccountRow.created_at))).scalars())

    async def routes(self) -> Dict[str, str]:
        async with self.session() as s:
            return {r.key: r.account_id for r in (await s.execute(select(PurposeRow).where(PurposeRow.account_id.is_not(None)))).scalars()}

    async def set_route(self, purpose: str, account_id: Optional[str]) -> None:
        """The account that serves a job (None: nothing is connected for it)."""
        async with self.session() as s:
            await s.execute(update(PurposeRow).where(PurposeRow.key == purpose).values(account_id=account_id))
            await s.commit()

    async def account(self, account_id: str) -> Optional[AccountRow]:
        async with self.session() as s:
            return await s.get(AccountRow, account_id)

    async def lost(self, account_id: str, why: str) -> None:
        """The provider stopped accepting this account's sign-in: it needs connecting again."""
        async with self.session() as s:
            await s.execute(update(AccountRow).where(AccountRow.id == account_id).values(status="needs_reconnect", last_error=why))
            await s.commit()

    async def save_secret(self, account_id: str, secret: Dict[str, Any]) -> None:
        """Keeps a new sealed secret for an account (a provider that rotates refresh tokens gives a new one after a refresh)."""
        async with self.session() as s:
            await s.execute(update(AccountRow).where(AccountRow.id == account_id).values(secret=seal(secret)))
            await s.commit()

    # ---- jobs ------------------------------------------------------------------------------------------

    async def seed_jobs(self) -> None:
        """The built-in jobs, once (a later start never undoes a change an owner made to one)."""
        async with self.session() as s:
            for j in BUILTIN:
                await s.execute(pg_insert(PurposeRow).values(key=j.key, label=j.label, direction=j.direction, description=j.description,
                                                              system=True, settings={}, created_at=_now()).on_conflict_do_nothing())
            await s.commit()

    @staticmethod
    def _job(row: PurposeRow) -> Job:
        return Job(key=row.key, label=row.label, direction=row.direction, description=row.description or "", system=row.system,
                   settings=dict(row.settings or {}))

    async def jobs(self) -> List[Job]:
        async with self.session() as s:
            rows = (await s.execute(select(PurposeRow).order_by(PurposeRow.system.desc(), PurposeRow.created_at, PurposeRow.key))).scalars()
            return [self._job(r) for r in rows]

    async def job(self, key: str) -> Optional[Job]:
        async with self.session() as s:
            row = await s.get(PurposeRow, key)
            return self._job(row) if row is not None else None

    async def save_job(self, job: Job) -> None:
        async with self.session() as s:
            row = await s.get(PurposeRow, job.key)
            if row is None:
                s.add(PurposeRow(key=job.key, label=job.label, direction=job.direction, description=job.description, system=job.system,
                                 settings=job.settings))
            else:
                row.label, row.description, row.settings = job.label, job.description, job.settings
            await s.commit()

    # ---- functions (uses) -------------------------------------------------------------------------------------

    @staticmethod
    def _use(row: UseRow) -> Use:
        return Use(key=row.key, label=row.label, description=row.description or "", direction=row.direction, default_job=row.default_job,
                   job=row.job_key or "")

    async def uses(self) -> List[Use]:
        async with self.session() as s:
            return [self._use(r) for r in (await s.execute(select(UseRow).order_by(UseRow.created_at, UseRow.key))).scalars()]

    async def use(self, key: str) -> Optional[Use]:
        async with self.session() as s:
            row = await s.get(UseRow, key)
            return self._use(row) if row is not None else None

    async def register_uses(self, uses: List[Use]) -> None:
        """Adds a function, or refreshes what it says about itself. The job an owner chose for it is never touched."""
        async with self.session() as s:
            for u in uses:
                row = await s.get(UseRow, u.key)
                if row is None:
                    s.add(UseRow(key=u.key, label=u.label, description=u.description, direction=u.direction, default_job=u.default_job))
                else:
                    row.label, row.description, row.direction, row.default_job = u.label, u.description, u.direction, u.default_job
            await s.commit()

    async def set_use_job(self, key: str, job_key: Optional[str]) -> None:
        async with self.session() as s:
            await s.execute(update(UseRow).where(UseRow.key == key).values(job_key=job_key or None))
            await s.commit()

    async def resolve(self, name: str) -> str:
        """The job a message goes through. `name` is a function's key (looked up), or a job's key (used as it is)."""
        async with self.session() as s:
            row = await s.get(UseRow, name)
            return (row.job_key or row.default_job) if row is not None else name

    async def delete_job(self, key: str) -> None:
        async with self.session() as s:
            await s.execute(delete(CursorRow).where(CursorRow.purpose == key))
            await s.execute(update(UseRow).where(UseRow.job_key == key).values(job_key=None))   # its functions go back to their default
            await s.execute(delete(PurposeRow).where(PurposeRow.key == key))
            await s.commit()

    # ---- the box keypair and the command log -----------------------------------------------------------

    async def box(self) -> Tuple[bytes, bytes]:
        """(private, public): made on first use, kept sealed. People seal secrets to the public half."""
        async with self.session() as s:
            row = await s.get(SettingRow, "box")
            if row is None:
                private, public = sealing.new_box_keypair()
                row = SettingRow(key="box", value=seal({"private": base64.b64encode(private).decode(), "public": base64.b64encode(public).decode()}))
                s.add(row)
                try:
                    await s.commit()
                except Exception:  # another process made it first
                    await s.rollback()
                    row = await s.get(SettingRow, "box")
            kept = unseal(row.value)
        return base64.b64decode(kept["private"]), base64.b64decode(kept["public"])

    async def open_box(self, blob: bytes) -> bytes:
        private, public = await self.box()
        return sealing.box_open(private, public, blob)

    async def record_command(self, command_id: str, type_: str, by: Optional[str]) -> bool:
        """False when this command id was seen before (a replay)."""
        async with self.session() as s:
            result = await s.execute(pg_insert(CommandRow).values(id=command_id[:100], type=type_[:60], by=(by or "")[:255], ok=False, at=_now())
                                     .on_conflict_do_nothing(index_elements=["id"]).returning(CommandRow.id))
            await s.commit()
            return result.first() is not None

    async def finish_command(self, command_id: str, ok: bool, error: Optional[str] = None) -> None:
        async with self.session() as s:
            await s.execute(update(CommandRow).where(CommandRow.id == command_id[:100]).values(ok=ok, error=error[:500] if error else None))
            await s.commit()

    async def prune_commands(self, keep_days: int = 30) -> None:
        async with self.session() as s:
            await s.execute(delete(CommandRow).where(CommandRow.at < _now() - timedelta(days=keep_days)))
            await s.commit()
