"""
The functions of this application that use mail, and the mail job each one uses.

A function is declared once, here, as a MailUse. The admin app (Email, Jobs) shows every function and lets an owner choose which job
it uses; until one is chosen it uses its default job. This server doesn't know which mailbox that is: it names the function, and the
mail service looks up the job, then the job's mailbox.

To give a new feature a mailbox of its own to choose:

    1. Declare it:     REMINDERS = MailUse("billing.reminders", "Payment reminders", "Reminders about unpaid invoices.", "send", "notifications")
                       and add it to USES.
    2. To send:        EmailMessage(..., purpose=REMINDERS.key)
    3. To read mail:   in services/notifications/mail_relay.take_in, handle `use == REMINDERS.key` (it is given the use of the job
                       the message was read from).
    4. Done:           it is registered with the mail service at start-up and appears in the admin app with a job dropdown.
"""
import asyncio
import logging
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.services.mail_bus import MailBusError, bus

logger = logging.getLogger("core.mail.uses")


@dataclass(frozen=True)
class MailUse:
    key: str            # a dotted name; a job's key never has a dot
    label: str
    description: str
    direction: str      # send | read | both
    default_job: str    # the job it uses until an owner chooses another


APP_NOTIFICATIONS = MailUse("app.notifications", "Notifications", "Invitations, sign-in codes, billing and staff notices.", "send", "notifications")
DESTINATION_EMAIL = MailUse("destinations.email", "Outbound email destinations", "Approved records sent to email addresses, with the record attached.", "send", "notifications")
SUPPORT_MAIL = MailUse("support.mail", "Help desk", "Support tickets: answers go out, and customers' replies are read.", "both", "support")
DOCUMENTS_INBOUND = MailUse("documents.inbound", "Document inbound", "Documents people email in become documents of a channel.", "read", "documents")

USES: List[MailUse] = [APP_NOTIFICATIONS, DESTINATION_EMAIL, SUPPORT_MAIL, DOCUMENTS_INBOUND]
BY_KEY: Dict[str, MailUse] = {u.key: u for u in USES}
# Job keys older code and mail already queued use directly
LEGACY_JOBS = ("notifications", "support")


def valid(purpose: str) -> bool:
    return purpose in BY_KEY or purpose in LEGACY_JOBS


def job_of(use: MailUse) -> str:
    """The job a function uses now: the one an owner chose (from the mail service's directory), else its default."""
    entry = bus.state.get("use:" + use.key) or {}
    return entry.get("job") or use.default_job


def use_for_job(job_key: str) -> Optional[str]:
    """The function that reads mail from this job (a message read from a job is handed to that function), or None."""
    for use in USES:
        if use.direction in ("read", "both") and job_of(use) == job_key:
            return use.key
    return None


def address_of(use: MailUse) -> Optional[str]:
    """The address mail for a function comes from and arrives at, from the directory."""
    job = next((j for j in bus.entries("job:") if j.get("key") == job_of(use)), None)
    return ((job or {}).get("account") or {}).get("address") or None


def job_entry(use: MailUse) -> Dict[str, Any]:
    return next((j for j in bus.entries("job:") if j.get("key") == job_of(use)), {})


class UseRegistrar:
    """Tells the mail service which functions this server has: at start, and again every few minutes (it is idempotent, and the mail
    service may have been set up again since). A choice an owner made is never changed by it."""

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        if settings.MAIL_BACKEND.lower() == "service":
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    async def register(self) -> None:
        await bus.command("use.register", {"uses": [asdict(u) for u in USES]}, by="system:server")

    async def _run(self) -> None:
        while True:
            delay = 300
            try:
                await bus.ready()
                await self.register()
            except asyncio.CancelledError:
                raise
            except MailBusError as e:
                logger.info("Couldn't tell the mail service about this server's mail functions yet (%s); trying again shortly", e)
                delay = 30
            except Exception:  # noqa: BLE001
                logger.exception("Registering the mail functions failed; trying again shortly")
                delay = 30
            await asyncio.sleep(delay)


registrar = UseRegistrar()
