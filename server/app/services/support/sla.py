"""
Service targets for support tickets, checked by the scheduler:

    breached     a ticket past its first-response or resolution target is marked (once), and its assignee, or
                 everyone who answers tickets when nobody is assigned, is told
    auto-solve   a ticket waiting for the customer longer than auto_solve_days is marked solved (and they're told)
    auto-close   a ticket solved longer than auto_close_days is closed for good (a reply then opens a new ticket)
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.core.platform_permissions import PLATFORM_GRANTS
from app.models.identity import User
from app.models.platform import PlatformStaff
from app.models.support import SupportTicket
from app.services.support import mail, tickets

logger = logging.getLogger("services.support.sla")


async def responders(db: AsyncSession) -> List[str]:
    """The addresses of everyone who answers tickets (staff whose role has support.respond)."""
    roles = [r for r, grants in PLATFORM_GRANTS.items() if "support.respond" in grants]
    ids = set((await db.execute(select(PlatformStaff.user_id).where(PlatformStaff.role.in_(roles)))).scalars().all()) | settings.platform_admin_ids
    if not ids:
        return []
    return [u.email for u in (await db.execute(select(User).where(User.id.in_(ids), User.status == "active"))).scalars() if u.email]


async def run(db: AsyncSession, now: Optional[datetime] = None) -> None:
    from app.services import staff_service
    now = now or datetime.now(timezone.utc)
    p = await tickets.policy(db)
    late = (await db.execute(select(SupportTicket).where(
        SupportTicket.breached_at.is_(None), SupportTicket.status.in_(("new", "open")), SupportTicket.merged_into.is_(None),
        or_(SupportTicket.first_response_due < now, SupportTicket.resolve_due < now)))).scalars().all()
    to_tell = []
    for t in late:
        t.breached_at = now
        to_tell.append((t.id, t.number, t.subject, t.assignee_id))
    waiting = (await db.execute(select(SupportTicket).where(
        SupportTicket.status == "pending", SupportTicket.last_staff_at < now - timedelta(days=p["auto_solve_days"])))).scalars().all()
    for t in waiting:
        await tickets.add_message(db, t, body="Marked solved: no reply for a while. Reply to open it again.", author_kind="system",
                                  author_name="Syruss", via="app")
        tickets.set_status(t, "solved")
    old = (await db.execute(select(SupportTicket).where(
        SupportTicket.status == "solved", SupportTicket.solved_at < now - timedelta(days=p["auto_close_days"])))).scalars().all()
    for t in old:
        tickets.set_status(t, "closed")
    await db.commit()
    everyone = await responders(db) if any(not a for *_, a in to_tell) else []
    for ticket_id, number, subject, assignee in to_tell:
        user = await db.get(User, assignee) if assignee else None
        for email in ([user.email] if user and user.email else everyone):
            await staff_service.tell(email, f"{number} is past its target", f"{subject}: it needs an answer.", path=f"/support/tickets/{ticket_id}")
    for t in waiting:
        await mail.send(t, "support_ticket_solved")
