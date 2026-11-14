"""
Adds someone to the platform staff. It is the only way to: the admin app adds nobody, and its sign-in page creates no
accounts. Run it on the server (or locally with `syruss staff add`), with the Keycloak admin login in the environment
for this one command:

    KEYCLOAK_ADMIN=... KEYCLOAK_ADMIN_PASSWORD=... python -m app.invite_staff you@company.com            an owner
    KEYCLOAK_ADMIN=... KEYCLOAK_ADMIN_PASSWORD=... python -m app.invite_staff ana@company.com support    another role
        --reason "Joins the support team"                                                              for the audit log

For an address with no Syruss sign-in yet, it creates one in Keycloak, and Keycloak emails a one-time link to choose a
password and set up the one-time code, which then opens the invitation. Syruss emails the invitation too (its link is
printed here as well). They accept it in the admin app, signed in with that address. Running it again for an address
with an invitation waiting sends both again, with fresh links. Written to the platform audit log as done by "the
deployment". Keycloak has to be able to send mail (bridge/keycloak/harden.py --smtp).
"""
import argparse
import asyncio
import re
import sys
from typing import Optional
from fastapi import HTTPException
from sqlalchemy import select
from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.platform_permissions import PLATFORM_ROLES
from app.keycloak_admin import KeycloakAdmin, KeycloakAdminError
from app.models.platform import PlatformAuditEntry, PlatformStaffInvitation
from app.services import staff_service

DEPLOYMENT = {"actor_id": "deployment", "actor_name": "The Syruss deployment"}
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


async def main(email: str, role: str = "owner", reason: Optional[str] = None, keycloak: Optional[KeycloakAdmin] = None) -> int:
    email = email.strip().lower()
    if not EMAIL.match(email) or role not in PLATFORM_ROLES:
        print(f"usage: python -m app.invite_staff EMAIL [{'|'.join(PLATFORM_ROLES)}] [--reason TEXT]", file=sys.stderr)
        return 2
    keycloak = keycloak or KeycloakAdmin.from_environment()
    if keycloak is None:
        print("Set KEYCLOAK_ADMIN and KEYCLOAK_ADMIN_PASSWORD for this command: it creates the person's sign-in in Keycloak.", file=sys.stderr)
        return 2
    reason = (reason or "").strip() or "Added from the server"

    async with AsyncSessionLocal() as db:
        pending = (await db.execute(select(PlatformStaffInvitation).where(
            PlatformStaffInvitation.email == email, PlatformStaffInvitation.status == "pending"))).scalar_one_or_none()
        try:
            if pending is not None:  # again: fresh links, the role this command names
                row, plain, action = pending, staff_service.renew(pending), "staff.invitation_resent"
                row.role = role
            else:
                row, plain = await staff_service.invite(db, email=email, role=role, by_id=DEPLOYMENT["actor_id"], by_name=DEPLOYMENT["actor_name"],
                                                        reason=reason)
                action = "staff.invited"
        except HTTPException as exc:
            print(exc.detail if isinstance(exc.detail, str) else exc.detail.get("message"), file=sys.stderr)
            return 1

        created = None
        try:
            user = await keycloak.find_user(email)
            if user is None:
                created = await keycloak.create_user(email)
            needs_setup = created is not None or "UPDATE_PASSWORD" in (user or {}).get("requiredActions", [])
            if needs_setup:
                await keycloak.send_setup(created or user["id"], client_id=settings.OIDC_ADMIN_CLIENT, redirect_uri=staff_service.join_url(plain),
                                          lifespan_seconds=staff_service.INVITE_DAYS * 86400)
        except KeycloakAdminError as exc:
            await db.rollback()
            if created:  # nothing half-made is left behind
                try:
                    await keycloak.delete_user(created)
                except KeycloakAdminError:
                    pass
            print(f"Nothing was added. {exc}", file=sys.stderr)
            if "execute-actions-email" in str(exc):
                print("Keycloak couldn't send its email: give it the mail server first (python bridge/keycloak/harden.py --smtp).", file=sys.stderr)
            return 1

        db.add(PlatformAuditEntry(**DEPLOYMENT, action=action, target_type="invitation", target_id=row.id, reason=reason,
                                  details={"email": row.email, "role": role, "sign_in_created": created is not None}))
        await db.commit()
        invitation_id = row.id

    sent = await staff_service.send(invitation_id, plain, new_signin=needs_setup)
    print(f"Invited {email} as {role}.")
    if needs_setup:
        print("Keycloak emailed them a link to choose a password and set up the one-time code; it leads to the invitation.")
    else:
        print("They already have a Syruss sign-in: they sign in to the admin app with it to accept.")
    print("The invitation was emailed." if sent else "The invitation email could NOT be sent: give them the link yourself.")
    print(f"Link (valid {staff_service.INVITE_DAYS} days): {staff_service.join_url(plain)}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="python -m app.invite_staff", description="Add someone to the Syruss platform staff.")
    parser.add_argument("email")
    parser.add_argument("role", nargs="?", default="owner", choices=list(PLATFORM_ROLES))
    parser.add_argument("--reason", default=None, help="Why they join (kept in the platform audit log)")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.email, args.role, args.reason)))
