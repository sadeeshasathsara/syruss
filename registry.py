"""
Syruss Monorepo Service Registry
Defines all services, their directory paths, port bindings, and lifecycle commands.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent


@dataclass
class ServiceConfig:
    name: str
    path: str               # Directory path relative to REPO_ROOT
    description: str
    port: Optional[int]
    dev_cmd: str            # Command to run in development (with watch/reload)
    prod_cmd: str           # Command to run in production

    @property
    def abs_dir(self) -> Path:
        return REPO_ROOT / self.path


SERVICES: Dict[str, ServiceConfig] = {
    "server": ServiceConfig(
        name="server",
        path="server",
        description="FastAPI Core REST API",
        port=8000,
        dev_cmd="uvicorn app.main:app --reload --host 0.0.0.0 --port 8000",
        prod_cmd="uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4",
    ),
    "agent": ServiceConfig(
        name="agent",
        path="agentic_framework",
        description="AI Agent Worker (LangGraph / LangChain)",
        port=None,
        dev_cmd="python -m agentic_framework",
        prod_cmd="python -m agentic_framework",
    ),
    "www": ServiceConfig(
        name="www",
        path="www",
        description="Next.js Web Application",
        port=3000,
        dev_cmd="npm run dev",
        prod_cmd="npm run build && npm start",
    ),
    "site": ServiceConfig(
        name="site",
        path="site",
        description="Landing & Onboarding Portal",
        port=3001,
        dev_cmd="npm run dev",
        prod_cmd="npm run build && npm start",
    ),
    "admin": ServiceConfig(
        name="admin",
        path="admin",
        description="System Admin Dashboard",
        port=3200,
        dev_cmd="npm run dev",
        prod_cmd="npm run build && npm start",
    ),
}


def get_service(name: str) -> Optional[ServiceConfig]:
    """Retrieves service configuration by name."""
    return SERVICES.get(name.lower().strip())


def get_all_services() -> Dict[str, ServiceConfig]:
    """Returns all registered services."""
    return SERVICES


def get_service_names() -> List[str]:
    """Returns list of all registered service names."""
    return sorted(SERVICES.keys())


def detect_changed_services(changed_files: List[str]) -> List[str]:
    """
    Given a list of file paths relative to REPO_ROOT,
    detects which registered services have been modified.
    """
    detected = set()
    for f in changed_files:
        f_norm = f.replace("\\", "/").lstrip("/")
        for s_name, s_cfg in SERVICES.items():
            s_path = s_cfg.path.replace("\\", "/").strip("/")
            if f_norm.startswith(f"{s_path}/") or f_norm == s_path:
                detected.add(s_name)
    return sorted(detected)
