"""
Tells the people who manage an organization's billing (org.billing.manage) when it is running out: at 80% and 100% of
its plan's pages for the period, and when its prepaid credits run low (under a tenth of the last pack added) or out.
Each alert is sent once a period, by email and in the app. It runs after the work that used the pages committed, in
its own transaction, so a document that was rolled back never sends one.
"""
import logging
from typing import List, Tuple
from sqlalchemy import select
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.org_permissions import ORG_GRANTS
from app.models.billing import CreditEntry
from app.models.identity import OrgMember, User
from app.models.tenant import Tenant
from app.services.billing import credits, service as billing
from app.services.billing.limits import LIMITS
from app.services.notifications import Recipient, inapp, notify
from app.services.notifications.kinds import BILLING_ALERT

logger = logging.getLogger("core.billing.alerts")
BILLERS = [role for role, grants in ORG_GRANTS.items() if "billing.manage" in grants]


async def _due(db, s) -> List[Tuple[str, str, str]]:
    """(alert, title, body) for what is true now; the caller keeps those not sent this period."""
    out = []
    used = await LIMITS[billing.PAGES].used(db, s.where)
    limit = s.limits.get(billing.PAGES)
    renews = f"{s.subscription.period_end:%d %B}"
    balance = await credits.balance(db, s.subscription.tenant_id)
    if limit:
        if used >= limit:
            more = f"Documents now use the {balance:,} prepaid credits left." if balance else "New documents are refused until you add credits or the pages renew."
            out.append(("pages_100", f"All {limit:,} pages for this period are used",
                        f"The {s.plan.name} plan's pages renew on {renews}. {more}"))
        elif used >= 0.8 * limit:
            out.append(("pages_80", f"{used:,} of {limit:,} pages used this period",
                        f"The {s.plan.name} plan's pages renew on {renews}. Add prepaid credits to keep reading beyond them."))
    last = (await db.execute(select(CreditEntry).where(
        CreditEntry.tenant_id == s.subscription.tenant_id).order_by(CreditEntry.created_at.desc()).limit(1))).scalar_one_or_none()
    added = (await db.execute(select(CreditEntry.delta).where(
        CreditEntry.tenant_id == s.subscription.tenant_id, CreditEntry.delta > 0).order_by(CreditEntry.created_at.desc()).limit(1))).scalar()
    if last is not None and last.kind == "usage":
        if balance == 0:
            out.append(("credits_out", "Your prepaid credits are used up",
                        "New documents are refused until you add credits" + (f" or the plan's pages renew on {renews}." if limit else ".")))
        elif added and balance < 0.1 * added:
            out.append(("credits_low", f"{balance:,} prepaid credits left", "Add credits so documents keep being read once the plan's pages are used."))
    return out


async def check(tenant_id: str) -> List[str]:
    """Sends the alerts that became due and weren't sent this period. Returns their names. Never raises."""
    try:
        async with AsyncSessionLocal() as db:
            s = await billing.standing(db, tenant_id)
            sub = s.subscription
            period = sub.period_start.isoformat()
            sent = list((sub.alerts or {}).get("sent", [])) if (sub.alerts or {}).get("period") == period else []
            new = [a for a in await _due(db, s) if a[0] not in sent]
            if not new:
                await db.commit()  # the subscription may have been created or brought up to date
                return []
            tenant = await db.get(Tenant, tenant_id)
            people = (await db.execute(select(User).join(OrgMember, OrgMember.user_id == User.id).where(
                OrgMember.tenant_id == tenant_id, OrgMember.role.in_(BILLERS)))).scalars().all()
            url = f"{settings.PUBLIC_SITE_URL.rstrip('/')}/{tenant.slug}/settings/billing"
            for alert, title, body in new:
                for person in people:
                    await notify(BILLING_ALERT, Recipient(email=person.email, user_id=person.id, tenant_id=tenant_id),
                                 {"organization": tenant.name, "title": title, "body": body, "url": url, "alert": alert}, db=db)
            names = [a for a, _, _ in new]
            if "pages_100" in names:
                names.append("pages_80")  # past 100%, 80% is old news
            sub.alerts = {"period": period, "sent": sorted(set(sent + names))}
            await db.commit()
            await inapp.push(db)
            return [a for a, _, _ in new]
    except Exception:
        logger.exception("Billing alerts for %s failed", tenant_id)
        return []
