"""
Syruss Monorepo Service Registry (Root Re-export)
Exposes registry definitions from scripts.registry for root-level scripts and tools.
"""

from scripts.registry import (
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
