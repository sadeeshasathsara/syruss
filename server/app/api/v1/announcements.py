"""What Syruss is telling the people using it right now: announcements staff posted, and whether it is under maintenance."""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.auth import AuthContext, get_current_auth
from app.core.database import get_db_session
from app.models.platform import PlatformAnnouncement
from app.services import platform_controls

router = APIRouter(tags=["Announcements"])


@router.get("/announcements")
async def announcements(auth: AuthContext = Depends(get_current_auth), db: AsyncSession = Depends(get_db_session)):
    """Live announcements for everyone and for your organization, newest and most serious first, and the maintenance notice."""
    now = datetime.now(timezone.utc)
    rows = (await db.execute(select(PlatformAnnouncement).where(
        PlatformAnnouncement.starts_at <= now,
        or_(PlatformAnnouncement.ends_at.is_(None), PlatformAnnouncement.ends_at > now),
        or_(PlatformAnnouncement.tenant_id.is_(None), PlatformAnnouncement.tenant_id == auth.tenant_id),
    ).order_by(PlatformAnnouncement.starts_at.desc()).limit(5))).scalars().all()
    order = {"critical": 0, "warning": 1, "info": 2}
    controls = await platform_controls.current()
    return {
        "maintenance": {"on": controls.maintenance, "message": controls.message} if controls.maintenance else None,
        "items": [{"id": a.id, "title": a.title, "body": a.body, "severity": a.severity, "starts_at": a.starts_at, "ends_at": a.ends_at}
                  for a in sorted(rows, key=lambda a: order.get(a.severity, 3))],
    }
