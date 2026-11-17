#!/usr/bin/env python3
"""
Syruss Monorepo Dependency Synchronization Tool.
Installs dependencies simultaneously in parallel across all Python and Node.js projects.
Automatically provisions a root .venv if one does not exist,
using uv if available or standard pip/venv.
"""

import argparse
import concurrent.futures
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from scripts.registry import REPO_ROOT, SERVICES, get_service_names

NODE_SERVICES = ["admin", "site", "www"]
PYTHON_SERVICES = ["server", "agent", "mail"]


@dataclass
class SyncTask:
    name: str
    category: str           # "Python" or "Node"
    cmd: List[str]
    cwd: Path
    shell: bool = False


@dataclass
class TaskResult:
    task: SyncTask
    returncode: int
    elapsed: float
    output: str


def get_venv_dir() -> Path:
    return REPO_ROOT / ".venv"


def get_venv_python() -> Path:
    venv = get_venv_dir()
    if sys.platform == "win32":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


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


def build_python_tasks(service: Optional[str] = None, force_pip: bool = False) -> List[SyncTask]:
    """Prepares Python sync task(s)."""
    venv_py = get_venv_python()
    use_uv = is_uv_available() and not force_pip

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

    tasks: List[SyncTask] = []
    for req in targets:
        if not req.exists():
            continue
        rel = req.relative_to(REPO_ROOT)
        name = f"python ({rel})"
        if use_uv:
            cmd = ["uv", "pip", "install", "-r", str(req), "--python", str(venv_py)]
        else:
            cmd = [str(venv_py), "-m", "pip", "install", "-r", str(req)]
        tasks.append(SyncTask(name=name, category="Python", cmd=cmd, cwd=REPO_ROOT, shell=False))

    return tasks


def build_node_tasks(service: Optional[str] = None) -> List[SyncTask]:
    """Prepares Node.js npm sync tasks for frontends."""
    npm_path = shutil.which("npm")
    if not npm_path:
        return []

    targets = [service] if service else NODE_SERVICES
    tasks: List[SyncTask] = []
    for svc in targets:
        svc_dir = REPO_ROOT / svc
        if (svc_dir / "package.json").exists():
            cmd = [npm_path, "install"]
            tasks.append(SyncTask(
                name=f"node ({svc})",
                category="Node",
                cmd=cmd,
                cwd=svc_dir,
                shell=(sys.platform == "win32"),
            ))
    return tasks


def execute_task(task: SyncTask, dry_run: bool = False) -> TaskResult:
    """Executes a single sync task and captures its timing and output."""
    if dry_run:
        return TaskResult(
            task=task,
            returncode=0,
            elapsed=0.0,
            output=f"[DRY-RUN] Would run: {' '.join(task.cmd)} in {task.cwd}",
        )

    t0 = time.perf_counter()
    proc = subprocess.run(
        task.cmd,
        cwd=task.cwd,
        shell=task.shell,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    elapsed = time.perf_counter() - t0
    return TaskResult(
        task=task,
        returncode=proc.returncode,
        elapsed=elapsed,
        output=proc.stdout or "",
    )


def run_tasks_parallel(tasks: List[SyncTask], dry_run: bool = False) -> int:
    """Executes all tasks simultaneously in parallel with live status reporting."""
    if not tasks:
        print("[INFO] No tasks to execute.")
        return 0

    print(f"\n[PARALLEL SYNC] Running {len(tasks)} tasks simultaneously:")
    for t in tasks:
        print(f"  * [START] {t.name:<18} -> {' '.join(t.cmd[:4])}...")

    t0 = time.perf_counter()
    failed = False
    results: List[TaskResult] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=len(tasks)) as executor:
        future_map = {executor.submit(execute_task, t, dry_run): t for t in tasks}
        for future in concurrent.futures.as_completed(future_map):
            res = future.result()
            results.append(res)
            if res.returncode == 0:
                print(f"  * [OK]    {res.task.name:<18} finished in {res.elapsed:.1f}s")
            else:
                failed = True
                print(f"  * [FAIL]  {res.task.name:<18} failed in {res.elapsed:.1f}s (code {res.returncode})")

    # If any task failed, display the captured output for debugging
    if failed:
        print("\n------------------------------------------------------------")
        print(" Error Details for Failed Tasks:")
        print("------------------------------------------------------------")
        for res in results:
            if res.returncode != 0:
                print(f"\n--- Output from {res.task.name} ---")
                print(res.output.strip() or "(no output)")
                print("------------------------------------")
        return 1

    total_time = time.perf_counter() - t0
    sum_individual = sum(r.elapsed for r in results)
    saved = max(0.0, sum_individual - total_time)

    if not dry_run and len(tasks) > 1 and sum_individual > 0:
        print(f"\n[SPEEDUP] Finished in {total_time:.1f}s (sequential would have taken ~{sum_individual:.1f}s, saved ~{saved:.1f}s)")

    return 0


def run_tasks_sequential(tasks: List[SyncTask], dry_run: bool = False) -> int:
    """Executes tasks sequentially one after another."""
    for t in tasks:
        print(f"\n* Running: {t.name}...")
        if dry_run:
            print(f"  [DRY-RUN] Would run: {' '.join(t.cmd)} in {t.cwd}")
            continue

        res = execute_task(t, dry_run=dry_run)
        if res.returncode != 0:
            print(f"[ERROR] {t.name} failed ({res.elapsed:.1f}s):")
            print(res.output)
            return res.returncode
        print(f"[OK] {t.name} finished in {res.elapsed:.1f}s")
        if res.output.strip():
            # Print brief summary of output
            lines = res.output.strip().splitlines()
            for line in lines[-5:]:
                print(f"  | {line}")

    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    """Main handler for `syruss sync` command."""
    service = getattr(args, "service", None)
    python_only = getattr(args, "python", False)
    node_only = getattr(args, "node", False)
    force_pip = getattr(args, "force_pip", False)
    dry_run = getattr(args, "dry_run", False)
    sequential = getattr(args, "sequential", False)

    print("------------------------------------------------------------")
    print(" Syruss Monorepo Dependency Sync [Mode: SIMULTANEOUS]")
    print("------------------------------------------------------------")

    has_uv = is_uv_available()
    print(f" * Package installer: {'uv (detected)' if has_uv and not force_pip else 'pip'}")
    print(f" * Virtual environment: {get_venv_dir()}")
    print("------------------------------------------------------------")

    # Ensure venv exists before scheduling python tasks
    if not dry_run and (not node_only or service in PYTHON_SERVICES):
        ensure_virtualenv(force_pip)

    tasks: List[SyncTask] = []

    # If a specific service was targeted:
    if service:
        if service in PYTHON_SERVICES:
            tasks.extend(build_python_tasks(service=service, force_pip=force_pip))
        elif service in NODE_SERVICES:
            tasks.extend(build_node_tasks(service=service))
        else:
            print(f"[ERROR] Unknown service '{service}'. Choose from: {', '.join(get_service_names())}")
            return 2
    else:
        # Include python unless node_only
        if not node_only:
            tasks.extend(build_python_tasks(force_pip=force_pip))
        # Include node unless python_only
        if not python_only:
            tasks.extend(build_node_tasks())

    if not tasks:
        print("[INFO] No tasks to sync.")
        return 0

    overall_t0 = time.perf_counter()

    if sequential or len(tasks) == 1:
        rc = run_tasks_sequential(tasks, dry_run=dry_run)
    else:
        rc = run_tasks_parallel(tasks, dry_run=dry_run)

    overall_elapsed = time.perf_counter() - overall_t0
    print("------------------------------------------------------------")
    if rc == 0:
        print(f"[SUCCESS] All dependencies synchronized in {overall_elapsed:.1f}s")
    else:
        print(f"[FAILURE] Dependency synchronization failed in {overall_elapsed:.1f}s")
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
        "--sequential",
        action="store_true",
        help="Run sync tasks sequentially instead of simultaneously in parallel",
    )
    subparser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what commands would run without executing them",
    )
