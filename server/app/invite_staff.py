"""
Invites someone to the platform staff from the server itself: for the very first owner, when there is nobody yet who
could do it from the admin app (or to recover when nobody can sign in there).

    python -m app.invite_staff you@company.com            an owner
    python -m app.invite_staff ana@company.com support    another role

The invitation is emailed, and its link printed here as well (valid for 7 days). Whoever opens it signs in to the admin
app with that address. It is written to the platform audit log as done by "the deployment".
"""
import asyncio
import sys
from app.core.database import AsyncSessionLocal
from app.core.platform_permissions import PLATFORM_ROLES
from app.models.platform import PlatformAuditEntry
from app.services import staff_service


async def main(email: str, role: str) -> None:
    async with AsyncSessionLocal() as db:
        row, plain = await staff_service.invite(db, email=email, role=role, by_id="deployment", by_name="The Syruss deployment",
                                                reason="Invited from the server (python -m app.invite_staff)")
        db.add(PlatformAuditEntry(actor_id="deployment", actor_name="The Syruss deployment", action="staff.invited", target_type="invitation",
                                  target_id=row.id, reason="Invited from the server", details={"email": row.email, "role": role}))
        await db.commit()
        invitation_id = row.id
    sent = await staff_service.send(invitation_id, plain)
    print(f"Invited {email} as {role}. " + ("The invitation was emailed." if sent else "The email could NOT be sent: give them the link yourself."))
    print(f"Link (valid 7 days): {staff_service.join_url(plain)}")


if __name__ == "__main__":
    if len(sys.argv) < 2 or (len(sys.argv) > 2 and sys.argv[2] not in PLATFORM_ROLES):
        sys.exit(f"usage: python -m app.invite_staff EMAIL [{'|'.join(PLATFORM_ROLES)}]")
    asyncio.run(main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "owner"))
