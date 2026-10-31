"""Any REST API: the approved record as JSON, with the sign-in the API wants (none, bearer token, basic, API key, or OAuth2 client credentials)."""
import base64
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
from app.services.destinations import http
from app.services.destinations.base import Adapter, Field, SendContext, SendResult, TestResult, register

METHODS = [{"value": m, "label": m} for m in ("POST", "PUT", "PATCH")]
AUTH = [{"value": "none", "label": "None"}, {"value": "bearer", "label": "Bearer token"}, {"value": "basic", "label": "Username and password"},
        {"value": "api_key", "label": "API key"}, {"value": "oauth2", "label": "OAuth2 client credentials"}]


def parse_headers(text: str) -> Dict[str, str]:
    """'Name: value' lines."""
    out = {}
    for line in (text or "").splitlines():
        name, sep, value = line.partition(":")
        if sep and name.strip():
            out[name.strip()] = value.strip()
    return out


async def authorise(config: Dict[str, Any], secrets: Dict[str, Any]) -> Tuple[Dict[str, str], Dict[str, str], Optional[str]]:
    """(headers, query params, problem) that sign the request."""
    headers, params = {}, {}
    kind = config.get("auth_type", "none")
    if kind == "bearer":
        headers["Authorization"] = f"Bearer {secrets.get('token', '')}"
    elif kind == "basic":
        raw = f"{config.get('username', '')}:{secrets.get('password', '')}".encode()
        headers["Authorization"] = "Basic " + base64.b64encode(raw).decode()
    elif kind == "api_key":
        (params if config.get("key_in") == "query" else headers)[config.get("key_name") or "X-API-Key"] = secrets.get("key_value", "")
    elif kind == "oauth2":
        token, problem = await http.bearer_from_client_credentials(config.get("token_url", ""), config.get("client_id", ""), secrets.get("client_secret", ""), config.get("scope", ""))
        if problem:
            return headers, params, problem
        headers["Authorization"] = f"Bearer {token}"
    return headers, params, None


class RestAdapter(Adapter):
    kind = "rest"
    label = "REST API"
    blurb = "Call any HTTP API with the approved record, signing in the way it expects."
    category = "custom"
    fields = [
        Field("url", "Address", "url", required=True, placeholder="https://api.example.com/v1/invoices"),
        Field("method", "Method", "select", options=METHODS, default="POST"),
        Field("auth_type", "Sign-in", "select", options=AUTH, default="none"),
        Field("token", "Token", "password", secret=True, required=True, show_if={"auth_type": "bearer"}),
        Field("username", "Username", "text", required=True, show_if={"auth_type": "basic"}),
        Field("password", "Password", "password", secret=True, required=True, show_if={"auth_type": "basic"}),
        Field("key_name", "Key name", "text", required=True, placeholder="X-API-Key", show_if={"auth_type": "api_key"}),
        Field("key_in", "Send the key in", "select", options=[{"value": "header", "label": "A header"}, {"value": "query", "label": "The address"}], default="header", show_if={"auth_type": "api_key"}),
        Field("key_value", "Key", "password", secret=True, required=True, show_if={"auth_type": "api_key"}),
        Field("token_url", "Token address", "url", required=True, show_if={"auth_type": "oauth2"}),
        Field("client_id", "Client id", "text", required=True, show_if={"auth_type": "oauth2"}),
        Field("client_secret", "Client secret", "password", secret=True, required=True, show_if={"auth_type": "oauth2"}),
        Field("scope", "Scope", "text", show_if={"auth_type": "oauth2"}),
        Field("extra_headers", "Extra headers", "textarea", secret=True, help="One per line, as Name: value. Kept secret.", placeholder="X-Tenant: acme"),
        Field("payload", "What to send", "select", default="envelope", options=[
            {"value": "envelope", "label": "The whole message (ids, document and record)"}, {"value": "record", "label": "Only the record’s data"}]),
    ]

    def _body(self, config: Dict[str, Any], envelope: Dict[str, Any]) -> Any:
        return envelope["record"]["data"] if config.get("payload") == "record" and "record" in envelope else envelope

    async def _call(self, config: Dict[str, Any], secrets: Dict[str, Any], body: Any, extra: Dict[str, str]) -> Tuple[http.Reply, str]:
        headers, params, problem = await authorise(config, secrets)
        if problem:
            return http.Reply(error=problem, retryable=False), ""
        headers = {"Content-Type": "application/json", "User-Agent": "Syruss-Delivery/1", **parse_headers(secrets.get("extra_headers", "")), **headers, **extra}
        method = config.get("method") or "POST"
        shown = f"{method} {config['url']}\n{http.redact(headers)}\n\n{http.excerpt(body)}"
        return await http.call(method, config["url"], headers=headers, json_body=body, params=params or None), shown

    async def test(self, config: Dict[str, Any], secrets: Dict[str, Any], destination_id: Optional[str] = None) -> TestResult:
        ping = {"id": "test", "type": "ping", "created_at": datetime.now(timezone.utc).isoformat(), "message": "This is a test from Syruss."}
        reply, _ = await self._call(config, secrets, ping, {"X-Syruss-Test": "true"})
        if reply.ok:
            return TestResult(True, f"The API answered HTTP {reply.status} in {round(reply.elapsed_ms)} ms.")
        return TestResult(False, http.failure_from(reply, "The API")[0], http.excerpt(reply.text, 300) or None)

    async def send(self, ctx: SendContext) -> SendResult:
        reply, shown = await self._call(ctx.config, ctx.secrets, self._body(ctx.config, ctx.envelope), {"Idempotency-Key": ctx.idempotency_key, "X-Syruss-Event": ctx.event})
        if reply.ok:
            body = reply.json()
            return SendResult(True, reply.status, request=shown, response=http.excerpt(reply.text), external_ref=str(body["id"]) if isinstance(body, dict) and body.get("id") else None)
        error, retryable = http.failure_from(reply, "The API")
        return SendResult(False, reply.status, error, retryable, request=shown, response=http.excerpt(reply.text), retry_after=reply.retry_after)


register(RestAdapter())
