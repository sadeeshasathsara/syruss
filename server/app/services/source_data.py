"""
Reading knowledge sources: parsing files, calling APIs, and finding the record sets inside.

Nothing here knows any domain. A record set is any list of objects in the data (the whole
file, or e.g. `data.items` in an API response); its columns are the objects' keys, with
nested objects flattened to dotted names ("address.city").
"""
import asyncio
import csv
import io
import ipaddress
import json
import socket
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit
import httpx
from app.core.config import settings

# Tests replace this with an httpx.MockTransport.
HTTP_TRANSPORT: Optional[httpx.AsyncBaseTransport] = None

SAMPLE_ROWS = 5
MAX_DEPTH = 4


class SourceError(Exception):
    """A problem a person can act on: shown as it is."""


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_bytes(content: bytes, *, filename: str = "", content_type: str = "") -> Any:
    if len(content) > settings.KNOWLEDGE_SOURCE_MAX_BYTES:
        raise SourceError(f"The data is larger than {settings.KNOWLEDGE_SOURCE_MAX_BYTES // 1_000_000} MB.")
    name, ctype = filename.lower(), content_type.lower()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise SourceError("The data is not UTF-8 text.") from exc
    if name.endswith(".csv") or "csv" in ctype:
        rows = list(csv.DictReader(io.StringIO(text)))
        if not rows:
            raise SourceError("The CSV has no rows under its header.")
        return rows
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise SourceError(f"The data is not valid JSON ({exc.msg}, line {exc.lineno}).") from exc


def flatten(row: Any, prefix: str = "") -> Dict[str, Any]:
    if not isinstance(row, dict):
        return {prefix or "value": row}
    out: Dict[str, Any] = {}
    for key, value in row.items():
        name = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            out.update(flatten(value, name))
        else:
            out[name] = value
    return out


def rows_at(data: Any, path: str) -> List[Dict[str, Any]]:
    node = data
    for part in [p for p in (path or "").split(".") if p]:
        if not isinstance(node, dict) or part not in node:
            raise SourceError(f"There is no '{path}' in the data.")
        node = node[part]
    if not isinstance(node, list):
        raise SourceError(f"'{path or 'the top level'}' is not a list of records.")
    return [flatten(r) for r in node if isinstance(r, dict)]


def columns_of(rows: List[Dict[str, Any]]) -> List[str]:
    seen: Dict[str, None] = {}
    for r in rows[:500]:
        for k in r:
            seen.setdefault(k, None)
    return list(seen)


def record_sets(data: Any) -> List[Dict[str, Any]]:
    """Every list of objects in the data, with its columns and a few sample rows."""
    found: List[Dict[str, Any]] = []

    def walk(node: Any, path: str, depth: int) -> None:
        if isinstance(node, list):
            if any(isinstance(r, dict) for r in node):
                rows = rows_at(data, path)
                found.append({
                    "records_path": path, "count": len(rows), "columns": columns_of(rows),
                    "sample": [{k: _short(v) for k, v in r.items()} for r in rows[:SAMPLE_ROWS]],
                })
            return
        if isinstance(node, dict) and depth < MAX_DEPTH:
            for key, value in node.items():
                walk(value, f"{path}.{key}" if path else str(key), depth + 1)

    walk(data, "", 0)
    return found


def _short(value: Any) -> Any:
    if isinstance(value, str) and len(value) > 80:
        return value[:80] + "…"
    if isinstance(value, list):
        return [_short(v) for v in value[:5]]
    return value


# ---------------------------------------------------------------------------
# Calling an API
# ---------------------------------------------------------------------------

async def _check_host(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise SourceError("The URL must start with http:// or https:// and name a host.")
    if settings.KNOWLEDGE_SOURCES_ALLOW_PRIVATE_HOSTS:
        return
    try:
        infos = await asyncio.to_thread(socket.getaddrinfo, parts.hostname, parts.port or 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise SourceError(f"The host '{parts.hostname}' could not be found.") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise SourceError(f"'{parts.hostname}' is on a private network, which this server does not call.")


async def _read_capped(response: httpx.Response) -> bytes:
    chunks, size = [], 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > settings.KNOWLEDGE_SOURCE_MAX_BYTES:
            raise SourceError(f"The response is larger than {settings.KNOWLEDGE_SOURCE_MAX_BYTES // 1_000_000} MB.")
        chunks.append(chunk)
    return b"".join(chunks)


def _check_response(response: httpx.Response) -> None:
    if response.is_redirect:
        raise SourceError(f"The API answered with a redirect to {response.headers.get('location')}; use that URL instead.")
    if response.status_code in (401, 403):
        raise SourceError(f"The API refused the credentials (HTTP {response.status_code}).")
    if response.status_code >= 400:
        raise SourceError(f"The API answered HTTP {response.status_code}.")


async def _request(client: httpx.AsyncClient, method: str, url: str, **kwargs: Any) -> tuple[bytes, str]:
    """One call, with the host checked first and the body read up to the size limit."""
    await _check_host(url)
    try:
        async with client.stream(method, url, **kwargs) as response:
            _check_response(response)
            return await _read_capped(response), response.headers.get("content-type", "")
    except httpx.TimeoutException as exc:
        raise SourceError("The API did not answer in time.") from exc
    except httpx.HTTPError as exc:
        raise SourceError(f"The API could not be reached: {exc}.") from exc


async def fetch_api(config: Dict[str, Any], secrets: Dict[str, Any]) -> Any:
    """Calls the API as configured and returns its parsed response."""
    auth = config.get("auth") or {}
    headers = {str(k): str(v) for k, v in (secrets.get("headers") or {}).items()}
    params = {str(k): str(v) for k, v in (config.get("query") or {}).items()}
    kind = auth.get("type", "none")

    async with httpx.AsyncClient(
        transport=HTTP_TRANSPORT, timeout=settings.KNOWLEDGE_SOURCE_TIMEOUT_SECONDS, follow_redirects=False,
    ) as client:
        if kind == "bearer":
            headers["Authorization"] = f"Bearer {secrets.get('token', '')}"
        elif kind == "basic":
            client.auth = httpx.BasicAuth(auth.get("username") or "", secrets.get("password") or "")
        elif kind == "api_key":
            if auth.get("key_in") == "query":
                params[auth.get("key_name") or "api_key"] = secrets.get("key_value", "")
            else:
                headers[auth.get("key_name") or "X-API-Key"] = secrets.get("key_value", "")
        elif kind == "oauth2_client_credentials":
            form = {"grant_type": "client_credentials", "client_id": auth.get("client_id") or "",
                    "client_secret": secrets.get("client_secret", "")}
            if auth.get("scope"):
                form["scope"] = auth["scope"]
            token_body, _ = await _request(client, "POST", auth.get("token_url") or "", data=form)
            try:
                headers["Authorization"] = f"Bearer {json.loads(token_body)['access_token']}"
            except (ValueError, KeyError, TypeError) as exc:
                raise SourceError("The token endpoint did not return an access_token.") from exc

        body = config.get("body")
        kwargs: Dict[str, Any] = {"headers": headers, "params": params}
        if config.get("method", "GET") == "POST" and body:
            try:
                kwargs["json"] = json.loads(body)
            except json.JSONDecodeError as exc:
                raise SourceError("The request body is not valid JSON.") from exc
        content, content_type = await _request(client, config.get("method", "GET"), config.get("url") or "", **kwargs)
        return parse_bytes(content, content_type=content_type)
