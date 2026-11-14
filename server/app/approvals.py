"""
The second owner's approval, from the server itself: to see it, or to turn it off when the platform no longer has two
owners who can approve (one left, or one locked out). Whoever can run this already controls the deployment.

    python -m app.approvals          show whether it is on and what waits
    python -m app.approvals off      turn it off (written to the platform audit log as done by "the deployment")
"""
import asyncio
import sys
from datetime import datetime, timezone
from sqlalchemy import select
from app.core.database import AsyncSessionLocal
from app.models.platform import PlatformApproval, PlatformAuditEntry
from app.services import platform_controls

SETTING = "approvals"


async def main(command: str = "") -> None:
    async with AsyncSessionLocal() as db:
        stored = await platform_controls.read_key(db, SETTING) or {}
        if command == "off":
            value = {**stored, "on": False}
            await platform_controls.save(db, SETTING, value, by_id="deployment", by_name="The Syruss deployment")
            db.add(PlatformAuditEntry(actor_id="deployment", actor_name="The Syruss deployment", action="approvals.off", target_type="platform",
                                      target_id=SETTING, reason="Turned off from the server (python -m app.approvals off)", details=value))
            await db.commit()
            print("The second owner's approval is off. Owners can turn it on again in the admin app.")
            return
        waiting = (await db.execute(select(PlatformApproval).where(
            PlatformApproval.status == "pending", PlatformApproval.expires_at > datetime.now(timezone.utc)))).scalars().all()
        print(f"The second owner's approval is {'on' if stored.get('on') else 'off'}. {len(waiting)} waiting.")
        for a in waiting:
            print(f"  {a.created_at:%Y-%m-%d %H:%M}  {a.requested_by_name}: {a.summary}")


if __name__ == "__main__":
    if len(sys.argv) > 2 or (len(sys.argv) == 2 and sys.argv[1] != "off"):
        sys.exit("usage: python -m app.approvals [off]")
    asyncio.run(main(sys.argv[1] if len(sys.argv) == 2 else ""))
