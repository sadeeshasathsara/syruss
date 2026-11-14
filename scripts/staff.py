"""
syruss staff add EMAIL [--role ROLE] [--reason TEXT]

Adds someone to the Syruss platform staff: the only way to (the admin app adds nobody, and its sign-in page creates no
accounts). It runs the server's own command (python -m app.invite_staff) with the server's settings, which makes the
person's sign-in in Keycloak if they have none (Keycloak emails them a link to choose a password and set up the
one-time code) and emails the invitation.

The Keycloak admin login is read from KEYCLOAK_ADMIN / KEYCLOAK_ADMIN_PASSWORD for this one command; on a developer
machine it falls back to the local Keycloak's development login, as bridge/keycloak/harden.py does. On a production
server, run the server command inside its container instead (see deploy/prod/README.md).
"""
import argparse
import os
import subprocess
import sys

from scripts.registry import get_service

ROLES = ["owner", "operator", "support", "billing", "viewer"]
DEV_ADMIN = {"KEYCLOAK_ADMIN": "admin", "KEYCLOAK_ADMIN_PASSWORD": "admin-change-me"}


def build_staff_parser(sub: argparse._SubParsersAction) -> None:
    add = sub.add_parser("add", help="Add someone to the platform staff (makes their sign-in and emails the invitation)")
    add.add_argument("email", help="Their email address")
    add.add_argument("--role", default="owner", choices=ROLES, help="Their platform role (default: owner)")
    add.add_argument("--reason", default=None, help="Why they join (kept in the platform audit log)")


def cmd_add(args: argparse.Namespace) -> int:
    env = dict(os.environ)
    if not (env.get("KEYCLOAK_ADMIN") and env.get("KEYCLOAK_ADMIN_PASSWORD")):
        env.update(DEV_ADMIN)
        print("[INFO] KEYCLOAK_ADMIN / KEYCLOAK_ADMIN_PASSWORD not set: using the local development Keycloak login.")
    command = [sys.executable, "-m", "app.invite_staff", args.email, args.role]
    if args.reason:
        command += ["--reason", args.reason]
    return subprocess.call(command, cwd=get_service("server").abs_dir, env=env)
