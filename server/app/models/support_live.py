"""
Who on the Syruss staff can take a live chat right now. The admin app sends a heartbeat while someone is online; a
status older than services/support/live.PRESENCE_SECONDS counts as offline.
"""
from datetime import datetime, timezone
from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class SupportPresence(Base):
    __tablename__ = "support_presence"

    user_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="offline")  # online | away | offline
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
