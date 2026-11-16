"""
The mail service's command line, for what has to happen before the admin app can be signed in to (the first owner's set-up
link is an email) and for keys. Run it where the service runs, with the same environment:

    python -m mail keygen        a new master key for MAIL_MASTER_KEYS (put it FIRST; keep the old ones after a comma)
    python -m mail fingerprint   the key secrets are sealed to, as the admin app shows it
    python -m mail jobs          each job and the account that serves it
    python -m mail connect       connect a mail server and say which jobs it serves. The password is asked for here and
                                 sealed straight into the database; it is never written to an environment file or a log.

    python -m mail connect --address no-reply@example.com --smtp-host smtp.example.com --smtp-username no-reply@example.com
    python -m mail connect --address help@example.com --smtp-host smtp.example.com --imap-host imap.example.com --jobs support,documents
    echo "$PASSWORD" | python -m mail connect --address ... --password-stdin       (for a script: one password for SMTP and IMAP)

Everything after that is done in the admin app (Email).
"""
import argparse
import base64
import asyncio
import getpass
import sys
from typing import List, Optional

from mail import sealing
from mail.operations import OperationError, Operations
from mail.router import Router
from mail.store import Store


async def _connect(args: argparse.Namespace) -> int:
    if not args.smtp_host and not args.imap_host:
        print("Give a mail server: --smtp-host to send, --imap-host to read, or both.", file=sys.stderr)
        return 2
    jobs = [j.strip() for j in (args.jobs or ("notifications,accounts" + (",support" if args.imap_host else ""))).split(",") if j.strip()]
    smtp_password = imap_password = ""
    if args.password_stdin:
        smtp_password = imap_password = sys.stdin.readline().rstrip("\r\n")
    else:
        if args.smtp_host:
            smtp_password = getpass.getpass(f"SMTP password for {args.smtp_username or args.address} (Enter for none): ")
        if args.imap_host:
            imap_password = getpass.getpass(f"IMAP password for {args.imap_username or args.smtp_username or args.address} (Enter: the same as SMTP): ") or smtp_password
    config = {k: v for k, v in {
        "smtp_host": args.smtp_host, "smtp_port": args.smtp_port, "smtp_security": args.smtp_security,
        "smtp_username": args.smtp_username or "" if args.smtp_host else "",
        "imap_host": args.imap_host, "imap_port": args.imap_port, "imap_ssl": not args.imap_no_ssl, "imap_folder": args.imap_folder,
        "imap_username": args.imap_username or args.smtp_username or "" if args.imap_host else "",
    }.items() if v not in (None, "")}
    store = Store()
    await store.init()
    try:
        ops = Operations(store, Router(store))
        try:
            row = await ops.add_imap(by="command line", address=args.address, name=args.name, config=config,
                                     secret={"smtp_password": smtp_password, "imap_password": imap_password}, jobs=jobs)
        except OperationError as e:
            print(f"Not connected: {e}", file=sys.stderr)
            return 1
        print(f"Connected {row.address} (can {' and '.join(row.granted)}); it serves: {', '.join(jobs)}.")
        if not args.no_test:
            result = await ops.test(row.id)
            print(("Signed in fine." if result["ok"] else f"It was saved, but signing in failed: {result['detail']}"))
        return 0
    finally:
        await store.close()


async def _jobs() -> int:
    store = Store()
    await store.init()
    try:
        router = Router(store)
        for job in await store.jobs():
            account, routed = await router.account_for(job.key)
            print(f"{job.key:16} {job.direction:5} {account.address}{'' if routed else '  (from the environment)'}")
        return 0
    finally:
        await store.close()


async def _signer() -> int:
    store = Store()
    await store.init()
    try:
        _, public = await store.signer()
        print(base64.b64encode(public).decode())
        return 0
    finally:
        await store.close()


async def _fingerprint() -> int:
    store = Store()
    await store.init()
    try:
        _, public = await store.box()
        print(sealing.fingerprint(public))
        return 0
    finally:
        await store.close()


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m mail", description="The mail service's command line.")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("keygen", help="a new master key")
    sub.add_parser("fingerprint", help="the fingerprint of the key secrets are sealed to")
    sub.add_parser("signer", help="the key this service signs its directory with: give it to the server as MAIL_SERVICE_PUBLIC_KEY")
    sub.add_parser("jobs", help="each job and the account that serves it")
    c = sub.add_parser("connect", help="connect a mail server")
    c.add_argument("--address", required=True, help="the mailbox's address (the From address)")
    c.add_argument("--name", default="Syruss", help="the name mail is sent as")
    c.add_argument("--smtp-host")
    c.add_argument("--smtp-port", type=int, default=587)
    c.add_argument("--smtp-security", choices=["none", "starttls", "ssl"], default="starttls")
    c.add_argument("--smtp-username")
    c.add_argument("--imap-host")
    c.add_argument("--imap-port", type=int, default=993)
    c.add_argument("--imap-no-ssl", action="store_true")
    c.add_argument("--imap-username")
    c.add_argument("--imap-folder", default="INBOX")
    c.add_argument("--jobs", help="comma-separated jobs it serves (default: notifications and accounts, and support when it reads)")
    c.add_argument("--password-stdin", action="store_true", help="read the password from standard input (one for SMTP and IMAP)")
    c.add_argument("--no-test", action="store_true", help="don't try signing in afterwards")
    args = parser.parse_args(argv)
    if args.command == "keygen":
        print(sealing.generate_master_key())
        return 0
    if args.command == "connect":
        return asyncio.run(_connect(args))
    if args.command == "jobs":
        return asyncio.run(_jobs())
    if args.command == "signer":
        return asyncio.run(_signer())
    return asyncio.run(_fingerprint())
