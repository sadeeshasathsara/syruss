"""
Alias to root registry.py
Ensures both 'from registry import ...' and 'from scripts.registry import ...' resolve to root registry.py.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from registry import (
    REPO_ROOT,
    SERVICES,
    ServiceConfig,
    detect_changed_services,
    get_all_services,
    get_service,
    get_service_names,
)

__all__ = [
    "REPO_ROOT",
    "SERVICES",
    "ServiceConfig",
    "detect_changed_services",
    "get_all_services",
    "get_service",
    "get_service_names",
]
