"""
The mail service's own database (MAIL_DB): the accounts connected in the admin app (their credentials sealed), which
account each purpose uses, every job's delivery record, and where each mailbox was last read.

A job's message is kept only until it is sent or given up: the delivery log keeps who, what about and how it went,
never the text (a sign-in code must not outlive its email).
"""
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import JSON, DateTime, Integer, String, Text, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from mail.config import settings


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class AccountRow(Base):
    """A mailbox connected in the admin app. The one from the environment isn't stored: it is built from settings."""
    __tablename__ = "mail_accounts"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)          # smtp_imap | gmail
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


class RouteRow(Base):
    """Which account a purpose sends (and reads) through. No row: the account from the environment."""
    __tablename__ = "mail_routes"
    purpose: Mapped[str] = mapped_column(String(40), primary_key=True)
    account_id: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)


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


# ---- sealing ---------------------------------------------------------------------------------------

def _fernet() -> Fernet:
    if settings.MAIL_SECRETS_KEY:
        return Fernet(settings.MAIL_SECRETS_KEY.encode())
    # Development only (check_config refuses a real server without a key)
    import base64
    import hashlib
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"syruss-mail-development-key").digest()))


def seal(values: Dict[str, Any]) -> str:
    return _fernet().encrypt(json.dumps(values).encode()).decode()


def unseal(token: Optional[str]) -> Dict[str, Any]:
    if not token:
        return {}
    try:
        return json.loads(_fernet().decrypt(token.encode()))
    except InvalidToken as e:
        raise ValueError("A sealed mail secret couldn't be opened (MAIL_SECRETS_KEY changed?).") from e


# ---- the database ----------------------------------------------------------------------------------

class Store:
    def __init__(self, database_url: Optional[str] = None):
        self._engine = create_async_engine(database_url or settings.DATABASE_URL, pool_size=5, max_overflow=5)
        self.session = async_sessionmaker(self._engine, expire_on_commit=False, class_=AsyncSession)

    async def init(self) -> None:
        await self._ensure_database()
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

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
            return {r.purpose: r.account_id for r in (await s.execute(select(RouteRow))).scalars()}

    async def account(self, account_id: str) -> Optional[AccountRow]:
        async with self.session() as s:
            return await s.get(AccountRow, account_id)
