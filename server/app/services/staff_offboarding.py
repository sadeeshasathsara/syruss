"""
Platform staff access that isn't used, or no longer needed, doesn't linger.

    offboarding   unused access is taken away: someone who hasn't used the admin app for inactive_days is told
                  warn_days before, then removed from the staff (never the last owner; owners set by the deployment
                  aren't touched). Using the admin app again in between starts the clock again.
    reviews       every review_days an owner confirms each staff member still needs their access (POST
                  /sysadmin/staff/{id}/review); the owners are reminded, at most weekly, while any review is due.

The policy is a platform setting owners change in the admin app (0 turns a part off). The scheduler runs `run`.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.models.identity import User
from app.models.platform import PlatformAuditEntry, PlatformStaff
from app.services import platform_controls

logger = logging.getLogger("services.staff_offboarding")

POLICY = "staff_policy"
REMINDED = "staff_review_reminded"
DEFAULT_POLICY = {"inactive_days": 60, "warn_days": 7, "review_days": 90}
REMIND_EVERY = timedelta(days=7)
SYSTEM = {"actor_id": "system", "actor_name": "Syruss (automatic)"}


async def policy(db: AsyncSession) -> Dict[str, int]:
    return {**DEFAULT_POLICY, **(await platform_controls.read_key(db, POLICY) or {})}


def last_active(row: PlatformStaff) -> datetime:
    return row.last_active_at or row.created_at


def review_due_at(row: PlatformStaff, p: Dict[str, int]) -> Optional[datetime]:
    return (row.reviewed_at or row.created_at) + timedelta(days=p["review_days"]) if p["review_days"] else None


def removal_at(row: PlatformStaff, p: Dict[str, int]) -> Optional[datetime]:
    return last_active(row) + timedelta(days=p["inactive_days"]) if p["inactive_days"] else None


async def _owner_emails(db: AsyncSession, without: Optional[str] = None) -> List[str]:
    stored = (await db.execute(select(PlatformStaff.user_id).where(PlatformStaff.role == "owner"))).scalars().all()
    ids = (set(stored) | settings.platform_admin_ids) - {without}
    if not ids:
        return []
    return [u.email for u in (await db.execute(select(User).where(User.id.in_(ids), User.status == "active"))).scalars() if u.email]


async def _owners_left(db: AsyncSession, without: str) -> int:
    stored = await db.scalar(select(func.count()).select_from(PlatformStaff).where(PlatformStaff.role == "owner", PlatformStaff.user_id != without))
    return (stored or 0) + len(settings.platform_admin_ids - {without})


async def run(db: AsyncSession, now: Optional[datetime] = None) -> None:
    from app.services.staff_service import tell
    now = now or datetime.now(timezone.utc)
    p = await policy(db)
    rows = (await db.execute(select(PlatformStaff, User).join(User, User.id == PlatformStaff.user_id))).all()
    removed, warned = [], []
    for row, user in rows:
        if row.user_id in settings.platform_admin_ids or not p["inactive_days"]:
            continue
        ends = removal_at(row, p)
        if now >= ends:
            if row.role == "owner" and await _owners_left(db, row.user_id) == 0:
                continue  # never the last owner: the others are told below, through the review reminder
            db.add(PlatformAuditEntry(**SYSTEM, action="staff.offboarded", target_type="user", target_id=row.user_id,
                                      reason=f"Didn't use the admin app for {p['inactive_days']} days",
                                      details={"name": user.display_name, "email": user.email, "role": row.role,
                                               "last_active_at": last_active(row).isoformat()}))
            await db.delete(row)
            removed.append(user)
        elif p["warn_days"] and row.offboard_warned_at is None and now >= ends - timedelta(days=p["warn_days"]):
            row.offboard_warned_at = now
            warned.append((user, ends))
    await db.commit()
    for user, ends in warned:
        await tell(user.email, "Your platform staff access ends soon",
                   f"You haven't used the Syruss admin app for a while. Unless you sign in to it by {ends:%d %B %Y}, you'll be taken off "
                   "the platform staff (a platform owner can invite you again).")
    if removed:
        for user in removed:
            await tell(user.email, "You're no longer on the Syruss platform staff",
                       f"You hadn't used the admin app for {p['inactive_days']} days, so your access was taken away. "
                       "Ask a platform owner if you still need it.", with_link=False)
        names = ", ".join(f"{u.display_name} ({u.email})" for u in removed)
        for email in await _owner_emails(db):
            await tell(email, "Unused platform staff access was taken away",
                       f"Taken off the staff after {p['inactive_days']} days without using the admin app: {names}.", path="/staff")
    await _remind_reviews(db, p, now)


async def _remind_reviews(db: AsyncSession, p: Dict[str, int], now: datetime) -> None:
    from app.services.staff_service import tell
    if not p["review_days"]:
        return
    rows = (await db.execute(select(PlatformStaff))).scalars().all()
    due = [r for r in rows if r.user_id not in settings.platform_admin_ids and review_due_at(r, p) <= now]
    if not due:
        return
    last = (await platform_controls.read_key(db, REMINDED) or {}).get("at")
    if last and now - datetime.fromisoformat(last) < REMIND_EVERY:
        return
    await platform_controls.save(db, REMINDED, {"at": now.isoformat(), "due": len(due)}, by_id=SYSTEM["actor_id"], by_name=SYSTEM["actor_name"])
    await db.commit()
    for email in await _owner_emails(db):
        await tell(email, f"Access review: {len(due)} on the platform staff to confirm",
                   f"{len(due)} {'person has' if len(due) == 1 else 'people have'} had platform access for {p['review_days']} days or more since "
                   "anyone confirmed they still need it. Open the Staff page and confirm each one, or remove them.", path="/staff")


def describe(row: PlatformStaff, p: Dict[str, int], now: Optional[datetime] = None) -> Dict[str, Any]:
    """What the staff list shows about one member's access: when they last used it, and the dates that follow from that."""
    now = now or datetime.now(timezone.utc)
    due = review_due_at(row, p)
    return {"last_active_at": row.last_active_at, "reviewed_at": row.reviewed_at, "reviewed_by_name": row.reviewed_by_name,
            "review_due_at": due, "review_due": bool(due and due <= now), "removal_at": removal_at(row, p)}
