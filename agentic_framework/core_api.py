"""
HTTP client for the core server's Agent Tool API.

Every call carries the workspace-scoped token that came with the event, so the agent can
only act inside the workspace that woke it. Business rules are enforced by the server;
refusals come back as ToolRefused with the server's reasons, which the agent reasons about.
"""
import json
from typing import Any, Dict, Optional, Tuple
import httpx
from agentic_framework.config import settings


class ToolRefused(Exception):
    """The core server refused the action (validation, permissions, not found). Not retried."""

    def __init__(self, status_code: int, detail: Any):
        super().__init__(f"{status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class CoreUnavailable(Exception):
    """The core server could not be reached or failed. The whole event is retried later."""


class CoreAPI:
    def __init__(self, token: str, client: Optional[httpx.AsyncClient] = None):
        self._client = client or httpx.AsyncClient(
            base_url=settings.CORE_API_URL,
            timeout=settings.CORE_API_TIMEOUT_SECONDS,
            headers={"Authorization": f"Bearer {token}"},
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            resp = await self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise CoreUnavailable(f"{method} {path}: {exc}") from exc
        if resp.status_code >= 500:
            raise CoreUnavailable(f"{method} {path}: HTTP {resp.status_code}")
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text)
            except ValueError:
                detail = resp.text
            raise ToolRefused(resp.status_code, detail)
        return resp

    async def get(self, path: str, **params) -> Any:
        params = {k: v for k, v in params.items() if v is not None}
        return (await self._request("GET", path, params=params)).json()

    async def post(self, path: str, body: Dict[str, Any]) -> Any:
        return (await self._request("POST", path, json=body)).json()

    async def put(self, path: str, body: Dict[str, Any]) -> Any:
        return (await self._request("PUT", path, json=body)).json()

    async def patch(self, path: str, body: Dict[str, Any]) -> Any:
        return (await self._request("PATCH", path, json=body)).json()

    async def delete(self, path: str) -> None:
        await self._request("DELETE", path)

    async def get_image(self, document_id: str, region: Optional[str] = None) -> Tuple[bytes, Optional[Dict[str, Any]]]:
        """The page as JPEG, and the page budget left after looking at it (if the platform reports one)."""
        params = {"region": region} if region else {}
        resp = await self._request("GET", f"/documents/{document_id}/image", params=params)
        try:
            budget = json.loads(resp.headers.get("X-Page-Budget") or "null")
        except ValueError:
            budget = None
        return resp.content, budget
