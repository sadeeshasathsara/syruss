"""
The platform's own switches, set by staff in the admin app and obeyed everywhere:

    maintenance   read-only mode: the API refuses changes (people can still look), the agent waits, intake waits
    signups       new accounts and new organizations
    agent         the agent working at all; or paused for some organizations only (agent_paused_orgs)
    intake        documents arriving from other systems (API keys, e-mail, SFTP)

Every request may ask, so the values are cached for a few seconds in each process: a change takes effect everywhere
within CACHE_SECONDS. Staff routes, sign-in and Stripe's webhook are never held by maintenance.
"""
import time
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import AsyncSessionLocal
from app.models.platform import PlatformSetting

CACHE_SECONDS = 5.0
MAINTENANCE, SWITCHES = "maintenance", "switches"
DEFAULT_MAINTENANCE = {"on": False, "message": ""}
DEFAULT_SWITCHES = {"signups": True, "agent": True, "intake": True, "agent_paused_orgs": []}

# Paths (after /api/v1) that keep working while the platform is read-only
MAINTENANCE_OPEN = ("/sysadmin/", "/billing/webhooks/", "/me/device", "/me/email-verification", "/auth/", "/ws/ticket")


@dataclass(frozen=True)
class Controls:
    maintenance: bool = False
    message: str = ""
    signups: bool = True
    agent: bool = True
    intake: bool = True
    agent_paused_orgs: FrozenSet[str] = field(default_factory=frozenset)

    def agent_runs_for(self, tenant_id: str) -> bool:
        return self.agent and not self.maintenance and tenant_id not in self.agent_paused_orgs


_cached: Optional[Controls] = None
_read_at = 0.0


def _build(maintenance: Dict[str, Any], switches: Dict[str, Any]) -> Controls:
    m, s = {**DEFAULT_MAINTENANCE, **(maintenance or {})}, {**DEFAULT_SWITCHES, **(switches or {})}
    return Controls(maintenance=bool(m["on"]), message=str(m.get("message") or ""), signups=bool(s["signups"]),
                    agent=bool(s["agent"]), intake=bool(s["intake"]), agent_paused_orgs=frozenset(s.get("agent_paused_orgs") or []))


async def read(db: Optional[AsyncSession] = None) -> Dict[str, Dict[str, Any]]:
    """The stored values, defaults filled in."""
    async def get(session: AsyncSession) -> Dict[str, Dict[str, Any]]:
        m, s = await session.get(PlatformSetting, MAINTENANCE), await session.get(PlatformSetting, SWITCHES)
        return {MAINTENANCE: {**DEFAULT_MAINTENANCE, **(m.value if m else {})}, SWITCHES: {**DEFAULT_SWITCHES, **(s.value if s else {})}}
    if db is not None:
        return await get(db)
    async with AsyncSessionLocal() as session:
        return await get(session)


async def current() -> Controls:
    """The switches now (at most CACHE_SECONDS old)."""
    global _cached, _read_at
    if _cached is None or time.monotonic() - _read_at > CACHE_SECONDS:
        try:
            values = await read()
            _cached, _read_at = _build(values[MAINTENANCE], values[SWITCHES]), time.monotonic()
        except Exception:  # the settings table can't be read (a migration in progress): everything stays open
            return _cached or Controls()
    return _cached


def forget() -> None:
    """Drops this process's cache (after a change here, and in tests)."""
    global _cached
    _cached = None


async def save(db: AsyncSession, key: str, value: Dict[str, Any], *, by_id: str, by_name: str) -> None:
    row = await db.get(PlatformSetting, key)
    if row is None:
        db.add(PlatformSetting(key=key, value=value, updated_by=by_id, updated_by_name=by_name))
    else:
        row.value, row.updated_by, row.updated_by_name = value, by_id, by_name
    await db.flush()
    forget()


def held_by_maintenance(method: str, path: str, api_prefix: str) -> bool:
    """Whether maintenance holds this request: a change to anything, except the paths that must keep working."""
    if method in ("GET", "HEAD", "OPTIONS") or not path.startswith(api_prefix + "/"):
        return False
    rest = path[len(api_prefix):]
    return not rest.startswith(MAINTENANCE_OPEN)
