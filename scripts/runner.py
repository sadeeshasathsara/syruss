"""
Syruss Monorepo Process Runner
Handles execution of services in development and production modes,
with support for unified terminal multiplexing or independent console windows.
"""

import os
from pathlib import Path
import platform
import shutil
import signal
import subprocess
import sys
import threading
import time
from typing import Dict, List, Optional

from scripts.registry import ServiceConfig, get_all_services, get_service

# Color formatting for terminal outputs (disabled if not supported)
USE_COLORS = sys.stdout.isatty() and platform.system() != "Windows" or os.environ.get("ANSICON") or os.environ.get("WT_SESSION")
SERVICE_COLORS = [
    "\033[94m",  # Blue
    "\033[92m",  # Green
    "\033[93m",  # Yellow
    "\033[95m",  # Magenta
    "\033[96m",  # Cyan
]
COLOR_RESET = "\033[0m" if USE_COLORS else ""


def _stream_output(proc: subprocess.Popen, prefix: str, color: str) -> None:
    """Streams stdout/stderr lines from a process with a service prefix."""
    try:
        assert proc.stdout is not None
        for line in iter(proc.stdout.readline, ""):
            if not line:
                break
            cleaned = line.rstrip()
            if cleaned:
                tag = f"{color}{prefix}{COLOR_RESET}" if USE_COLORS else prefix
                print(f"{tag} {cleaned}", flush=True)
    except Exception:
        pass


def _launch_console_window(svc: ServiceConfig, command: str) -> bool:
    """
    Launches a service command in a new, independent console window.
    Supports Windows, macOS, and Linux terminal emulators.
    """
    system = platform.system()
    working_dir = str(svc.abs_dir)

    if system == "Windows":
        title = f"Syruss - {svc.name} ({svc.port or 'worker'})"
        # cmd.exe /c start "Title" cmd.exe /k "cd /d <dir> && <command>"
        win_cmd = f'cmd.exe /c start "{title}" cmd.exe /k "cd /d {working_dir} && {command}"'
        subprocess.Popen(win_cmd, shell=True)
        return True

    elif system == "Darwin":  # macOS
        apple_script = (
            f'tell application "Terminal" to do script '
            f'"cd \\"{working_dir}\\" && echo \\"=== Syruss: {svc.name} ===\\" && {command}"'
        )
        subprocess.Popen(["osascript", "-e", apple_script])
        return True

    elif system == "Linux":
        # Check available desktop terminal emulators
        terminals = [
            ("gnome-terminal", ["gnome-terminal", f"--title=Syruss - {svc.name}", "--", "bash", "-c", f"cd '{working_dir}' && {command}; exec bash"]),
            ("konsole", ["konsole", "-p", f"tabtitle=Syruss - {svc.name}", "-e", "bash", "-c", f"cd '{working_dir}' && {command}; exec bash"]),
            ("xfce4-terminal", ["xfce4-terminal", f"--title=Syruss - {svc.name}", "-e", f"bash -c 'cd \"{working_dir}\" && {command}; exec bash'"]),
            ("xterm", ["xterm", "-title", f"Syruss - {svc.name}", "-e", f"bash -c 'cd \"{working_dir}\" && {command}; exec bash'"]),
        ]
        for term_bin, term_args in terminals:
            if shutil.which(term_bin):
                subprocess.Popen(term_args)
                return True

        print(f"[WARN] No supported GUI terminal found on Linux. Falling back to multiplexed process.")
        return False

    return False


def run_services(
    mode: str,
    service_names: Optional[List[str]] = None,
    console: bool = False,
) -> int:
    """
    Runs services in 'dev' or 'prod' mode.
    If console=True, spawns separate terminal windows for each service.
    If console=False, runs in the current terminal with formatted log tags.
    """
    all_svcs = get_all_services()
    selected_names = service_names if service_names else sorted(all_svcs.keys())

    targets: List[ServiceConfig] = []
    for name in selected_names:
        cfg = get_service(name)
        if not cfg:
            print(f"[ERROR] Service '{name}' is not registered.")
            return 1
        targets.append(cfg)

    # Validate directories
    active_targets: List[ServiceConfig] = []
    for cfg in targets:
        if not cfg.abs_dir.exists():
            print(f"[WARN] Directory '{cfg.path}' does not exist for service '{cfg.name}'. Skipping.")
            continue
        active_targets.append(cfg)

    if not active_targets:
        print("[ERROR] No valid service directories found to run.")
        return 1

    print("------------------------------------------------------------")
    print(f" Syruss Service Orchestrator [Mode: {mode.upper()}]")
    print("------------------------------------------------------------")
    for cfg in active_targets:
        port_info = f":{cfg.port}" if cfg.port else "worker"
        print(f" * {cfg.name:<8} ({port_info:<8}) -> {cfg.path}")
    print("------------------------------------------------------------\n")

    # Mode 1: Separate Console Windows
    if console:
        print("[INFO] Launching services in independent console windows...\n")
        for cfg in active_targets:
            cmd = cfg.dev_cmd if mode == "dev" else cfg.prod_cmd
            port_info = f" on port {cfg.port}" if cfg.port else ""
            print(f"[START] {cfg.name}{port_info} via new window: {cmd}")
            _launch_console_window(cfg, cmd)
            time.sleep(0.3)
        print("\n[OK] All requested services launched in separate windows.")
        return 0

    # Mode 2: Multiplexed in Current Terminal
    print("[INFO] Starting services in unified console (Press Ctrl+C to stop all)...\n")
    processes: List[subprocess.Popen] = []
    threads: List[threading.Thread] = []

    for idx, cfg in enumerate(active_targets):
        cmd = cfg.dev_cmd if mode == "dev" else cfg.prod_cmd
        color = SERVICE_COLORS[idx % len(SERVICE_COLORS)] if USE_COLORS else ""
        prefix = f"[{cfg.name}]"

        try:
            proc = subprocess.Popen(
                cmd,
                cwd=cfg.abs_dir,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            processes.append(proc)

            t = threading.Thread(target=_stream_output, args=(proc, prefix, color), daemon=True)
            t.start()
            threads.append(t)
            print(f"[START] {cfg.name} (PID: {proc.pid}) -> {cmd}")
        except Exception as e:
            print(f"[ERROR] Failed to start '{cfg.name}': {e}")

    # Wait for termination or Ctrl+C
    def _shutdown():
        print("\n\n[INFO] Gracefully shutting down all services...")
        for p in processes:
            try:
                if platform.system() == "Windows":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
                else:
                    p.terminate()
            except Exception:
                pass
        print("[OK] All services stopped.")

    try:
        while True:
            # Check if any non-daemon process died unexpectedly
            time.sleep(1)
            for p in processes:
                if p.poll() is not None:
                    # Process exited
                    pass
    except KeyboardInterrupt:
        _shutdown()
        return 0
    except Exception as e:
        print(f"[ERROR] Runner exception: {e}")
        _shutdown()
        return 1
