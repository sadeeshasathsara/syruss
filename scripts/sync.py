#!/usr/bin/env python3
"""
Syruss Monorepo Dependency Synchronization Tool.
Installs dependencies across all Python and Node.js projects.
Automatically provisions a root .venv if one does not exist,
using uv if available or standard pip/venv.
"""

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional

from scripts.registry import REPO_ROOT, SERVICES, get_service_names

NODE_SERVICES = ["admin", "site", "www"]
PYTHON_SERVICES = ["server", "agent", "mail"]


def get_venv_dir() -> Path:
    return REPO_ROOT / ".venv"


def get_venv_python() -> Path:
    venv = get_venv_dir()
    if sys.platform == "win32":
        candidate = venv / "Scripts" / "python.exe"
    else:
        candidate = venv / "bin" / "python"
    return candidate


def is_uv_available() -> bool:
    return shutil.which("uv") is not None


def ensure_virtualenv(force_pip: bool = False) -> Path:
    """Ensures .venv exists at the repository root. Creates one if missing."""
    venv_dir = get_venv_dir()
    venv_py = get_venv_python()

    if venv_dir.is_dir() and venv_py.exists():
        return venv_py

    print("[INFO] No virtual environment found at .venv. Creating one...")
    t0 = time.perf_counter()

    use_uv = is_uv_available() and not force_pip
    if use_uv:
        cmd = ["uv", "venv", str(venv_dir)]
        print(f"  -> Running: {' '.join(cmd)}")
        res = subprocess.run(cmd, cwd=REPO_ROOT)
    else:
        cmd = [sys.executable, "-m", "venv", str(venv_dir)]
        print(f"  -> Running: {' '.join(cmd)}")
        res = subprocess.run(cmd, cwd=REPO_ROOT)

    if res.returncode != 0 or not venv_py.exists():
        raise RuntimeError(f"Failed to create virtual environment at {venv_dir}")

    elapsed = time.perf_counter() - t0
    tool = "uv" if use_uv else "python -m venv"
    print(f"[OK] Virtual environment created via {tool} in {elapsed:.1f}s")
    return venv_py


def sync_python(service: Optional[str] = None, force_pip: bool = False, dry_run: bool = False) -> int:
    """Installs Python dependencies using uv (if available) or pip."""
    venv_py = get_venv_python() if dry_run else ensure_virtualenv(force_pip)
    use_uv = is_uv_available() and not force_pip

    # Determine target requirements files
    if service:
        if service == "server":
            req_file = REPO_ROOT / "server" / "requirements.txt"
        elif service in ("agent", "agentic_framework"):
            req_file = REPO_ROOT / "agentic_framework" / "requirements.txt"
        elif service == "mail":
            req_file = REPO_ROOT / "mail" / "requirements.txt"
        else:
            req_file = REPO_ROOT / "requirements.txt"
        targets = [req_file]
    else:
        targets = [REPO_ROOT / "requirements.txt"]

    print(f"\n[SYNC: PYTHON] Using {'uv pip' if use_uv else 'pip'} (venv: {venv_py})")
    for req in targets:
        if not req.exists():
            print(f"[ERROR] Requirements file not found: {req}")
            return 1

        rel_path = req.relative_to(REPO_ROOT)
        print(f"  * Installing dependencies from {rel_path}...")

        if use_uv:
            cmd = ["uv", "pip", "install", "-r", str(req), "--python", str(venv_py)]
        else:
            cmd = [str(venv_py), "-m", "pip", "install", "-r", str(req)]

        if dry_run:
            print(f"    [DRY-RUN] Would execute: {' '.join(cmd)}")
            continue

        t0 = time.perf_counter()
        res = subprocess.run(cmd, cwd=REPO_ROOT)
        if res.returncode != 0:
            print(f"[ERROR] Failed to install Python dependencies from {rel_path}")
            return res.returncode

        # If installing root requirements with uv, also install -e . (editable syruss CLI)
        if req == REPO_ROOT / "requirements.txt":
            print("  * Installing syruss monorepo CLI in editable mode...")
            if use_uv:
                e_cmd = ["uv", "pip", "install", "-e", ".", "--python", str(venv_py)]
            else:
                e_cmd = [str(venv_py), "-m", "pip", "install", "-e", "."]
            res_e = subprocess.run(e_cmd, cwd=REPO_ROOT)
            if res_e.returncode != 0:
                print("[WARNING] Editable syruss CLI install returned non-zero code.")

        elapsed = time.perf_counter() - t0
        print(f"  [OK] Completed {rel_path} in {elapsed:.1f}s")

    return 0


def sync_node(service: Optional[str] = None, dry_run: bool = False) -> int:
    """Installs Node dependencies across www, site, and admin via npm."""
    npm_path = shutil.which("npm")
    if not npm_path:
        print("[ERROR] 'npm' was not found in PATH. Please install Node.js (v22+).")
        return 1

    targets = [service] if service else NODE_SERVICES
    print(f"\n[SYNC: NODE] Installing packages via npm...")

    for svc in targets:
        svc_dir = REPO_ROOT / svc
        pkg_json = svc_dir / "package.json"
        if not pkg_json.exists():
            print(f"[SKIP] No package.json found in {svc}/")
            continue

        print(f"  * Syncing Node service: {svc} ({svc}/)...")
        # On Windows, npm is a cmd script: npm.cmd
        cmd = [npm_path, "install"]
        if dry_run:
            print(f"    [DRY-RUN] Would execute: {' '.join(cmd)} in {svc_dir}")
            continue

        t0 = time.perf_counter()
        # shell=True required for npm on Windows
        res = subprocess.run(cmd, cwd=svc_dir, shell=(sys.platform == "win32"))
        if res.returncode != 0:
            print(f"[ERROR] npm install failed for {svc}")
            return res.returncode
        elapsed = time.perf_counter() - t0
        print(f"  [OK] Completed {svc} in {elapsed:.1f}s")

    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    """Main handler for `syruss sync` command."""
    service = getattr(args, "service", None)
    python_only = getattr(args, "python", False)
    node_only = getattr(args, "node", False)
    force_pip = getattr(args, "force_pip", False)
    dry_run = getattr(args, "dry_run", False)

    print("------------------------------------------------------------")
    print(" Syruss Monorepo Dependency Sync")
    print("------------------------------------------------------------")

    has_uv = is_uv_available()
    print(f" * Package installer: {'uv (detected)' if has_uv and not force_pip else 'pip'}")
    print(f" * Virtual environment: {get_venv_dir()}")
    print("------------------------------------------------------------")

    overall_t0 = time.perf_counter()

    # If a specific service was targeted:
    if service:
        if service in PYTHON_SERVICES:
            rc = sync_python(service=service, force_pip=force_pip, dry_run=dry_run)
            return rc
        elif service in NODE_SERVICES:
            rc = sync_node(service=service, dry_run=dry_run)
            return rc
        else:
            print(f"[ERROR] Unknown service '{service}'. Choose from: {', '.join(get_service_names())}")
            return 2

    # If flags filter to python or node:
    if python_only and not node_only:
        rc = sync_python(force_pip=force_pip, dry_run=dry_run)
    elif node_only and not python_only:
        rc = sync_node(dry_run=dry_run)
    else:
        # Sync both
        rc_py = sync_python(force_pip=force_pip, dry_run=dry_run)
        if rc_py != 0:
            return rc_py
        rc_node = sync_node(dry_run=dry_run)
        if rc_node != 0:
            return rc_node
        rc = 0

    overall_elapsed = time.perf_counter() - overall_t0
    print("------------------------------------------------------------")
    print(f"[SUCCESS] All dependencies synchronized in {overall_elapsed:.1f}s")
    print("------------------------------------------------------------")
    return rc


def build_sync_parser(subparser: argparse.ArgumentParser) -> None:
    all_service_names = get_service_names()
    subparser.add_argument(
        "service",
        nargs="?",
        choices=all_service_names,
        help=f"Optional specific service to sync: {', '.join(all_service_names)}",
    )
    subparser.add_argument(
        "-p", "--python",
        action="store_true",
        help="Sync Python virtual environment and dependencies only",
    )
    subparser.add_argument(
        "-n", "--node",
        action="store_true",
        help="Sync Node.js (npm) dependencies only",
    )
    subparser.add_argument(
        "--pip",
        dest="force_pip",
        action="store_true",
        help="Force using pip even if uv is available",
    )
    subparser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what commands would run without executing them",
    )
