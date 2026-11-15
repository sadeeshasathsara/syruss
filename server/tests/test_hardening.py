"""
Hardening: what every answer carries, the largest request we read, "sign in again" for risky actions, and the settings
a production deployment must not start without.
"""
import time
import pytest
from app.core.auth.dependencies import deployment_problems, production_problems, validate_auth_config
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


READY = {
    "POSTGRES_PASSWORD": "a-real-db-password", "MINIO_ROOT_PASSWORD": "a-real-storage-password",
    "AGENT_TOKEN_SECRET": "a-real-secret-value", "SECRETS_ENCRYPTION_KEY": "a-real-key",
    "OIDC_ISSUER": "https://login.example.com/realms/syruss", "PUBLIC_SITE_URL": "https://www.example.com",
    "PUBLIC_APP_URL": "https://app.example.com", "PUBLIC_ADMIN_URL": "https://admin.example.com",
    "PUBLIC_API_URL": "https://api.example.com", "CORS_ORIGINS": "https://app.example.com,https://www.example.com",
    "MAIL_SERVICE_TOKEN": "a-long-random-service-token-123", "REQUIRE_TRUSTED_DEVICE": True,
}


def test_the_test_server_and_production_refuse_development_defaults(monkeypatch):
    for name, value in READY.items():
        monkeypatch.setattr(settings, name, value)
    assert deployment_problems() == [] and production_problems() == []
    # Each development default is named
    dev = {"POSTGRES_PASSWORD": "postgrespassword", "MINIO_ROOT_PASSWORD": "minioadminpassword", "AGENT_TOKEN_SECRET": "dev-only-change-me",
           "SECRETS_ENCRYPTION_KEY": "", "OIDC_ISSUER": "http://localhost:8080/realms/syruss", "PUBLIC_APP_URL": "http://localhost:3000",
           "PUBLIC_ADMIN_URL": "https://admin.syruss.local", "PUBLIC_API_URL": "http://localhost:8000",
           "CORS_ORIGINS": "https://app.example.com,http://localhost:3000", "MAIL_SERVICE_TOKEN": "short"}
    for name, value in dev.items():
        monkeypatch.setattr(settings, name, value)
        assert any(name in p for p in deployment_problems()), name
        monkeypatch.setattr(settings, name, READY[name])
    # Both real servers refuse them at startup; someone's machine doesn't
    monkeypatch.setattr(settings, "POSTGRES_PASSWORD", "postgrespassword")
    for env in ("test", "production"):
        monkeypatch.setattr(settings, "ENV", env)
        with pytest.raises(RuntimeError, match=f"Not safe to run as {env}: POSTGRES_PASSWORD"):
            validate_auth_config()
    monkeypatch.setattr(settings, "ENV", "prod")
    with pytest.raises(RuntimeError, match="ENV must be one of"):
        validate_auth_config()
    monkeypatch.setattr(settings, "ENV", "development")
    assert settings.is_deployed is False


def test_security_switches_may_be_off_on_the_test_server_but_never_in_production(monkeypatch):
    for name, value in READY.items():
        monkeypatch.setattr(settings, name, value)
    monkeypatch.setattr(settings, "REQUIRE_TRUSTED_DEVICE", False)
    assert deployment_problems() == []
    assert any("REQUIRE_TRUSTED_DEVICE" in p for p in production_problems())
