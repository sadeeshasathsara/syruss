"""
Live updates for the admin app's Email page: when the mail service says something changed (a job, an account, its key), the page is
told so it asks again. The event names only what changed ("job:support"), never its content: the page reads that over the usual
owners-only API. It is a channel of the staff live stream (sysadmin/live, scope=mail), so it reaches the browser the same way every
other live page does.
"""
from typing import Any, Dict, Optional

from app.services.live import platform_feed

CHANNEL = "mail:changes"


async def changed(key: str, entry: Optional[Dict[str, Any]]) -> None:
    """A listener on the mail bus's directory (services/mail_bus.py)."""
    platform_feed.broadcast(CHANNEL, {"kind": "mail.changed", "key": key, "removed": entry is None})
