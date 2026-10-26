#!/usr/bin/env python3
"""
Syruss Monorepo Command Center CLI
Unified entry point for service orchestration, lifecycle management, and changelog versioning.
"""

import argparse
import difflib
import sys
from typing import List, Optional

from scripts.changeset import build_changeset_parser, cmd_add, cmd_apply, cmd_check, cmd_status, cmd_table, cmd_write
from registry import get_all_services, get_service_names
from scripts.runner import run_services


def suggest_correction(word: str, candidates: List[str], entity_type: str = "command") -> str:
    """Finds close matches for a mistyped command or service name using difflib."""
    matches = difflib.get_close_matches(word.lower(), candidates, n=3, cutoff=0.5)
    if matches:
        quoted = [f"'{m}'" for m in matches]
        return f"\n[INFO] Did you mean: {' or '.join(quoted)}?"
    return ""


class SyrussArgumentParser(argparse.ArgumentParser):
    """Custom ArgumentParser providing typo suggestions on unrecognized arguments."""
    def error(self, message: str):
        # Check if the error involves an invalid choice
        invalid_choice_prefix = "invalid choice: "
        if invalid_choice_prefix in message:
            start = message.find(invalid_choice_prefix) + len(invalid_choice_prefix)
            rest = message[start:]
            bad_val = rest.split()[0].strip("'\",")
            # Extract choices if possible
            if "choose from" in rest:
                choices_raw = rest.split("choose from")[1].strip(" :()")
                choices = [c.strip("'\" ") for c in choices_raw.split(",")]
                suggestion = suggest_correction(bad_val, choices)
                sys.stderr.write(f"[ERROR] {message}{suggestion}\n")
                sys.exit(2)
        sys.stderr.write(f"[ERROR] {message}\n")
        sys.exit(2)


def handle_start(args: argparse.Namespace) -> int:
    """Handles 'syruss start [service]' (production mode)."""
    svcs = [args.service] if args.service else None
    return run_services(mode="prod", service_names=svcs, console=args.console)


def handle_run_dev(args: argparse.Namespace) -> int:
    """Handles 'syruss run dev [service]' (development mode)."""
    svcs = [args.service] if args.service else None
    return run_services(mode="dev", service_names=svcs, console=args.console)


def build_cli_parser() -> SyrussArgumentParser:
    all_service_names = get_service_names()
    services_doc = ", ".join(all_service_names)

    parser = SyrussArgumentParser(
        prog="syruss",
        description="Syruss Monorepo Developer Command Center",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Registered Services:\n"
            + "\n".join([f"  * {s:<8} - {get_all_services()[s].description}" for s in all_service_names])
            + "\n\nExamples:\n"
            f"  syruss run dev               Start all services with live reload\n"
            f"  syruss run dev --console     Start all services in separate terminal windows\n"
            f"  syruss run dev server        Start only the FastAPI server in dev mode\n"
            f"  syruss start                 Start all services in production mode\n"
            f"  syruss changeset write -b www=minor -m \"Feature description\"\n"
            f"  syruss changeset status      Check pending changesets and projected versions\n"
        ),
    )

    top_sub = parser.add_subparsers(dest="top_command")

    # 1. start [service] [--console]
    p_start = top_sub.add_parser("start", help="Start service(s) in production mode")
    p_start.add_argument("service", nargs="?", choices=all_service_names, help=f"Optional specific service: {services_doc}")
    p_start.add_argument("--console", action="store_true", help="Launch in independent console windows per service")

    # 2. run dev [service] [--console]
    p_run = top_sub.add_parser("run", help="Run workflows (e.g. 'run dev')")
    run_sub = p_run.add_subparsers(dest="run_action")
    p_dev = run_sub.add_parser("dev", help="Start service(s) in development mode with live reload")
    p_dev.add_argument("service", nargs="?", choices=all_service_names, help=f"Optional specific service: {services_doc}")
    p_dev.add_argument("--console", action="store_true", help="Launch in independent console windows per service")

    # 3. changeset <subcommand>
    p_cs = top_sub.add_parser("changeset", help="Manage changesets, versioning, and CHANGELOG.md")
    cs_sub = p_cs.add_subparsers(dest="changeset_action")
    build_changeset_parser(cs_sub)

    return parser


def main(custom_args: Optional[List[str]] = None) -> int:
    raw_args = custom_args if custom_args is not None else sys.argv[1:]

    # Early check for typo in top-level command
    known_top = ["start", "run", "changeset", "--help", "-h"]
    if raw_args and not raw_args[0].startswith("-") and raw_args[0] not in known_top:
        suggestion = suggest_correction(raw_args[0], ["start", "run", "changeset"])
        sys.stderr.write(f"[ERROR] Unknown command '{raw_args[0]}'.{suggestion}\n")
        return 2

    # Early check for typo in 'run' subcommand (e.g. 'syruss run deev')
    if len(raw_args) >= 2 and raw_args[0] == "run":
        if raw_args[1] not in ["dev", "--help", "-h"]:
            suggestion = suggest_correction(raw_args[1], ["dev"])
            sys.stderr.write(f"[ERROR] Unknown run action '{raw_args[1]}'.{suggestion}\n")
            return 2

    # Early check for typo in service name when running 'syruss start <service>' or 'syruss run dev <service>'
    all_service_names = get_service_names()
    if len(raw_args) >= 2 and raw_args[0] == "start" and not raw_args[1].startswith("-"):
        if raw_args[1] not in all_service_names:
            suggestion = suggest_correction(raw_args[1], all_service_names, "service")
            sys.stderr.write(f"[ERROR] Unknown service '{raw_args[1]}'.{suggestion}\n")
            return 2
    if len(raw_args) >= 3 and raw_args[0] == "run" and raw_args[1] == "dev" and not raw_args[2].startswith("-"):
        if raw_args[2] not in all_service_names:
            suggestion = suggest_correction(raw_args[2], all_service_names, "service")
            sys.stderr.write(f"[ERROR] Unknown service '{raw_args[2]}'.{suggestion}\n")
            return 2

    parser = build_cli_parser()

    if not raw_args:
        parser.print_help()
        return 0

    args = parser.parse_args(raw_args)

    if args.top_command == "start":
        return handle_start(args)
    elif args.top_command == "run":
        if getattr(args, "run_action", None) == "dev":
            return handle_run_dev(args)
        parser.parse_args(["run", "--help"])
        return 0
    elif args.top_command == "changeset":
        action = getattr(args, "changeset_action", None)
        if not action:
            parser.parse_args(["changeset", "--help"])
            return 0
        dispatch = {
            "add": cmd_add,
            "write": cmd_write,
            "check": cmd_check,
            "status": cmd_status,
            "apply": cmd_apply,
            "table": cmd_table,
        }
        handler = dispatch.get(action)
        if handler:
            return handler(args)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
