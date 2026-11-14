import uuid
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy import String, DateTime, Boolean, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.core.database import Base

TENANT_STATUSES = ("active", "suspended", "closed")


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    # Whether the organization opens at all; `status` says why not: suspended by platform staff, or closed by its owner.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="active", server_default="active", nullable=False)
    status_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status_changed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # When a closed organization's workspaces, documents and files were removed (see services/data_lifecycle.py)
    purged_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    workspaces = relationship("Workspace", back_populates="tenant", cascade="all, delete-orphan")

    def set_status(self, status: str, reason: Optional[str] = None) -> None:
        """Opens or shuts the organization and records why; only an active one opens."""
        if status not in TENANT_STATUSES:
            raise ValueError(f"Unknown organization status: {status}")
        self.status, self.status_reason = status, reason
        self.is_active = status == "active"
        self.status_changed_at = datetime.now(timezone.utc)
