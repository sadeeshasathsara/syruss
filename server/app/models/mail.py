"""
Mail on its way to the mail service (mail/). Sending an email writes a row here; the relay publishes it on Kafka
(mail.outbound) and the mail service reports how it went (mail.events). The message is sealed while it waits and
dropped once published: what stays is who it was for, what about, and how it went.
"""
import uuid
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class MailOutbox(Base):
    __tablename__ = "mail_outbox"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    purpose: Mapped[str] = mapped_column(String(40), nullable=False)               # notifications | support
    to: Mapped[str] = mapped_column(String(320), nullable=False)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    payload: Mapped[Optional[str]] = mapped_column(Text, nullable=True)            # the job, sealed; None once published
    # queued (waiting to be published) | published | sent | retrying | dead
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="queued", index=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)      # tries by the mail service
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    meta: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), index=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    done_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
