"""
Signed webhook: the whole message as JSON to an address you run. Every request carries

    X-Syruss-Timestamp   seconds since 1970 when it was sent
    X-Syruss-Signature   v1=<hex>[,v1=<hex>]  HMAC-SHA256 of "<timestamp>.<raw body>" with the endpoint's signing secret
    X-Syruss-Delivery    this delivery's id        X-Syruss-Event   document.completed | document.corrected
    Idempotency-Key      the same on every retry of this delivery, so the receiver can ignore repeats

While a replaced secret is still within its overlap the signature carries both (new first), so a receiver can switch without a gap.
"""
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from app.services.destinations import http
from app.services.destinations.base import Adapter, Field, SendContext, SendResult, TestResult, register


def sign(secret: str, timestamp: str, body: bytes) -> str:
    return hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256).hexdigest()


def signature_header(secrets: Dict[str, Any], timestamp: str, body: bytes) -> str:
    sigs = [f"v1={sign(secrets.get('signing_secret', ''), timestamp, body)}"]
    previous, until = secrets.get("previous_secret"), secrets.get("previous_until")
    if previous and until and datetime.fromisoformat(until) > datetime.now(timezone.utc):
        sigs.append(f"v1={sign(previous, timestamp, body)}")
    return ",".join(sigs)


def encode(envelope: Dict[str, Any]) -> bytes:
    return json.dumps(envelope, separators=(",", ":"), default=str).encode()


async def post_signed(url: str, secrets: Dict[str, Any], envelope: Dict[str, Any], *, delivery_id: str, idempotency_key: str, event: str,
                      extra_headers: Dict[str, str] | None = None) -> http.Reply:
    body = encode(envelope)
    timestamp = str(int(time.time()))
    headers = {
        "Content-Type": "application/json", "User-Agent": "Syruss-Delivery/1", "X-Syruss-Timestamp": timestamp,
        "X-Syruss-Signature": signature_header(secrets, timestamp, body), "X-Syruss-Delivery": delivery_id, "X-Syruss-Event": event,
        "Idempotency-Key": idempotency_key, **(extra_headers or {}),
    }
    return await http.call("POST", url, headers=headers, content=body)


class WebhookAdapter(Adapter):
    kind = "webhook"
    label = "Webhook"
    blurb = "Send each approved record, signed, to an address you run."
    category = "custom"
    fields = [Field("url", "Address", "url", required=True, placeholder="https://example.com/hooks/syruss", help="Syruss posts the approved record here as JSON.")]

    async def test(self, config: Dict[str, Any], secrets: Dict[str, Any], destination_id: Optional[str] = None) -> TestResult:
        ping = {"id": "test", "type": "ping", "created_at": datetime.now(timezone.utc).isoformat(), "message": "This is a test from Syruss."}
        reply = await post_signed(config["url"], secrets, ping, delivery_id="test", idempotency_key="test", event="ping", extra_headers={"X-Syruss-Test": "true"})
        if reply.ok:
            return TestResult(True, f"The endpoint answered HTTP {reply.status} in {round(reply.elapsed_ms)} ms.")
        return TestResult(False, http.failure_from(reply, "The endpoint")[0], http.excerpt(reply.text, 300) or None)

    async def send(self, ctx: SendContext) -> SendResult:
        reply = await post_signed(ctx.config["url"], ctx.secrets, ctx.envelope, delivery_id=ctx.delivery_id, idempotency_key=ctx.idempotency_key, event=ctx.event)
        shown = f"POST {ctx.config['url']}\nIdempotency-Key: {ctx.idempotency_key}\n\n{http.excerpt(ctx.envelope)}"
        if reply.ok:
            body = reply.json()
            return SendResult(True, reply.status, request=shown, response=http.excerpt(reply.text), external_ref=str(body["id"]) if isinstance(body, dict) and body.get("id") else None)
        error, retryable = http.failure_from(reply, "The endpoint")
        return SendResult(False, reply.status, error, retryable, request=shown, response=http.excerpt(reply.text), retry_after=reply.retry_after)


register(WebhookAdapter())
