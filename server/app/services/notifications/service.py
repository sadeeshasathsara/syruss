"""
The one way to tell a person something: `notify(KIND, recipient, data)`.

The kind (kinds.py) decides which channels exist for the message (email, in-app, or both) and how it reads in each.
The caller supplies who and the values; it may narrow the channels (`channels={"email"}`) but never invents a new
one. A channel the recipient can't use (no account yet for the bell, no address for email) is skipped, and the
returned Delivery says what happened, so the caller can show "couldn't send" without catching exceptions.
"""
import logging
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Literal, Optional
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import AsyncSessionLocal
from app.models.identity import User
from app.services.notifications import inapp, transport
from app.services.notifications.kinds import Kind
from app.services.notifications.render import email_parts, line

logger = logging.getLogger("core.notifications")

Channel = Literal["email", "in_app"]


@dataclass
class Recipient:
    """Who it is for: an address (email), an account (in-app), or both. `tenant_id` is for organization-scoped bells."""
    email: Optional[str] = None
    user_id: Optional[str] = None
    tenant_id: Optional[str] = None


@dataclass
class Delivery:
    email: Literal["sent", "failed", "skipped"] = "skipped"
    email_error: Optional[str] = None
    in_app: Literal["stored", "skipped"] = "skipped"


async def recipient_for_email(db: AsyncSession, email: str) -> Recipient:
    """An address, plus the account behind it when someone with that confirmed address already has one."""
    address = email.strip().lower()
    user = (await db.execute(select(User).where(func.lower(User.email) == address, User.email_verified.is_(True)))).scalar_one_or_none()
    return Recipient(email=address, user_id=user.id if user else None)


async def notify(
    kind: Kind,
    to: Recipient,
    data: Dict[str, Any],
    *,
    channels: Optional[Iterable[Channel]] = None,
    db: Optional[AsyncSession] = None,
) -> Delivery:
    """
    Sends `kind` to `to`. With `db`, the in-app notification joins the caller's transaction (commit, then
    `inapp.push(db)`); without it, it is stored and pushed on its own.
    """
    variables = kind.prepare(data)
    wanted = set(channels) if channels is not None else {c for c, spec in (("email", kind.email), ("in_app", kind.in_app)) if spec}
    delivery = Delivery()

    if "in_app" in wanted and kind.in_app and to.user_id:
        spec = kind.in_app
        tenant = inapp.ACCOUNT if spec.scope == "account" else to.tenant_id
        if tenant:
            fields = dict(
                user_id=to.user_id, tenant_id=tenant, type=spec.type, title=line(spec.title, variables), body=line(spec.body, variables),
                link=line(spec.link, variables) if spec.link else None, severity=spec.severity,
                data={k: variables[k] for k in spec.keep if variables.get(k) is not None}, replace_key=spec.replace_by,
            )
            if db is not None:
                await inapp.create(db, **fields)
            else:
                async with AsyncSessionLocal() as own:
                    await inapp.create(own, **fields)
                    await own.commit()
                    await inapp.push(own)
            delivery.in_app = "stored"
        else:
            logger.warning("Notification '%s' is organization-scoped but has no organization; not stored.", kind.key)

    if "email" in wanted and kind.email and to.email:
        spec = kind.email
        parts = email_parts(spec.template, {**variables, "to": to.email})
        message = transport.EmailMessage(to=to.email, subject=line(spec.subject, variables), text=parts["text"], html=parts["html"])
        try:
            await transport.send(message)
            delivery.email = "sent"
        except transport.EmailError as e:
            logger.warning("Email '%s' to %s failed: %s (%s)", kind.key, to.email, e, e.__cause__)
            delivery.email, delivery.email_error = "failed", str(e)[:300]
    return delivery
