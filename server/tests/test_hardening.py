"""
Hardening: what every answer carries, the largest request we read, "sign in again" for risky actions, and the settings
a production deployment must not start without.
"""
import time
import pytest
from app.core.auth.dependencies import production_problems
from app.core.config import settings
from tests.conftest import create_template, ws

API = "/api/v1"


async def test_answers_say_they_are_data_and_api_answers_are_never_cached(client):
    res = await client.get(f"{API}/organization")
    h = res.headers
    assert h["x-content-type-options"] == "nosniff" and h["x-frame-options"] == "DENY" and h["referrer-policy"] == "no-referrer"
    assert h["cache-control"] == "no-store" and h["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"
    assert "strict-transport-security" not in h  # only over HTTPS, in production


async def test_production_pins_the_connection_to_https(client, monkeypatch):
    monkeypatch.setattr(settings, "ENV", "production")
    assert "max-age=31536000" in (await client.get(f"{API}/organization")).headers["strict-transport-security"]


async def test_a_request_bigger_than_the_server_reads_is_refused_before_its_body(client, monkeypatch):
    monkeypatch.setattr(settings, "MAX_REQUEST_MB", 1)
    big = await client.post(f"{API}/organization/invitations", content=b"x" * 2_000_000, headers={"Content-Type": "application/json"})
    assert big.status_code == 413 and "1 MB" in big.json()["detail"]
    assert (await client.get(f"{API}/organization")).status_code == 200


async def test_risky_actions_need_a_sign_in_from_the_last_few_minutes(client, monkeypatch):
    tpl = await create_template(client)
    stale = {**ws(tpl), "X-Auth-Time": str(int(time.time()) - 3600)}
    refused = await client.post(f"{API}/settings/api-keys", headers=stale, json={"name": "ci", "scope": "read"})
    assert refused.status_code == 403 and refused.json()["detail"]["code"] == "reauth_required"
    for method, url in (("post", f"{API}/settings/webhooks"), ("delete", f"{API}/settings/api-keys/x"), ("patch", f"{API}/organization/members/x"), ("delete", f"{API}/workspaces/nothing")):
        res = await getattr(client, method)(url, headers=stale, **({"json": {}} if method in ("post", "patch") else {}))
        assert res.status_code == 403 and res.json()["detail"].get("code") == "reauth_required", (method, url, res.status_code)
    assert (await client.request("DELETE", f"{API}/organization", headers=stale, json={"confirm": "x"})).json()["detail"]["code"] == "reauth_required"

    fresh = await client.post(f"{API}/settings/api-keys", headers=ws(tpl), json={"name": "ci", "scope": "read"})
    assert fresh.status_code == 201
    monkeypatch.setattr(settings, "REQUIRE_RECENT_LOGIN", False)
    key_id = fresh.json()["api_key"]["id"]
    assert (await client.delete(f"{API}/settings/api-keys/{key_id}", headers={**ws(tpl), "X-Auth-Time": stale["X-Auth-Time"]})).status_code == 204  # allowed once the rule is off


def test_a_production_start_refuses_unsafe_settings(monkeypatch):
    assert any("OIDC_ISSUER" in p for p in production_problems())  # the development issuer is http
    monkeypatch.setattr(settings, "OIDC_ISSUER", "https://login.example.com/realms/syruss")
    monkeypatch.setattr(settings, "PUBLIC_SITE_URL", "https://www.example.com")
    monkeypatch.setattr(settings, "AGENT_TOKEN_SECRET", "a-real-secret-value")
    monkeypatch.setattr(settings, "SECRETS_ENCRYPTION_KEY", "a-real-key")
    monkeypatch.setattr(settings, "REQUIRE_TRUSTED_DEVICE", True)
    assert production_problems() == []
    monkeypatch.setattr(settings, "REQUIRE_TRUSTED_DEVICE", False)
    monkeypatch.setattr(settings, "AGENT_TOKEN_SECRET", "dev-only-change-me")
    problems = " ".join(production_problems())
    assert "REQUIRE_TRUSTED_DEVICE" in problems and "AGENT_TOKEN_SECRET" in problems
