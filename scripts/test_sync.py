"""Unit tests for syruss sync command and scripts.sync."""
import argparse
from unittest.mock import patch

import pytest

from scripts.cli import build_cli_parser
from scripts.sync import (
    cmd_sync,
    ensure_virtualenv,
    get_venv_dir,
    get_venv_python,
    is_uv_available,
    sync_node,
    sync_python,
)


def test_cli_parser_sync_options():
    parser = build_cli_parser()

    # Default sync
    args = parser.parse_args(["sync"])
    assert args.top_command == "sync"
    assert args.service is None
    assert args.python is False
    assert args.node is False
    assert args.force_pip is False
    assert args.dry_run is False

    # Filter flags
    args_p = parser.parse_args(["sync", "--python", "--dry-run"])
    assert args_p.python is True
    assert args_p.dry_run is True

    args_n = parser.parse_args(["sync", "-n", "--pip"])
    assert args_n.node is True
    assert args_n.force_pip is True

    # Target specific service
    args_svc = parser.parse_args(["sync", "server"])
    assert args_svc.service == "server"

    args_node_svc = parser.parse_args(["sync", "www"])
    assert args_node_svc.service == "www"


def test_venv_paths():
    venv_dir = get_venv_dir()
    assert venv_dir.name == ".venv"
    venv_py = get_venv_python()
    assert "python" in str(venv_py).lower()


def test_is_uv_available_returns_bool():
    res = is_uv_available()
    assert isinstance(res, bool)


def test_sync_dry_run_python():
    rc = sync_python(dry_run=True)
    assert rc == 0


def test_sync_dry_run_node():
    rc = sync_node(dry_run=True)
    assert rc == 0


def test_cmd_sync_dry_run_all():
    parser = build_cli_parser()
    args = parser.parse_args(["sync", "--dry-run"])
    rc = cmd_sync(args)
    assert rc == 0


def test_cmd_sync_service_dry_run():
    parser = build_cli_parser()
    args_py = parser.parse_args(["sync", "server", "--dry-run"])
    assert cmd_sync(args_py) == 0

    args_node = parser.parse_args(["sync", "admin", "--dry-run"])
    assert cmd_sync(args_node) == 0
