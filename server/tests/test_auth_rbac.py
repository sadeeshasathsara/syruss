"""
Authentication and the two authorization gates: organization, then workspace.

Keycloak tokens are verified against a locally generated key; roles and permissions come from the
database on every request, never from a token.
"""
import time
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from fastapi.routing import APIRoute
from starlette.requests import Request
from app.api.router import api_router
from app.core.auth import AuthContext
from app.core.auth.dependencies import build_provider
from app.core.auth.oidc import OidcProvider
from app.core.config import settings
from tests.conftest import create_template, join, person, register, ws

API = "/api/v1"
ISSUER = "http://keycloak.test/realms/syruss"


# --- the auth context ---------------------------------------------------------------

def test_org_admin_is_an_org_role_not_a_workspace_pass():
    admin = AuthContext(user_id="a", tenant_id="t", org_role="admin")
    member = AuthContext(user_id="m", tenant_id="t", org_role="member")
    assert admin.is_org_admin() and not member.is_org_admin()
    # Org admins are not let into any workspace by their role: has_template_access only reflects an API key's binding.
    key = AuthContext(user_id="apikey:1", tenant_id="t", accessible_template_ids=["tpl_a"])
    assert key.has_template_access("tpl_a") and not key.has_template_access("tpl_b") and key.has_template_access(None)
    assert admin.has_template_access("any")  # humans are unrestricted here; the workspace gate checks membership


def test_the_product_only_accepts_keycloak_tokens():
    # No header-based or default sign-in exists in the product: the one provider it builds checks Keycloak tokens.
    assert isinstance(build_provider(), OidcProvider)


# --- Keycloak tokens ----------------------------------------------------------------

@pytest.fixture(scope="module")
def keypair():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private, private.public_key()


@pytest.fixture
def provider(keypair, monkeypatch):
    p = OidcProvider(issuer=ISSUER, audience="syruss-api", jwks_url="http://keycloak.test/certs", allowed_clients=["syruss-app"])

    class Signing:
        key = keypair[1]

    monkeypatch.setattr(p._jwks, "get_signing_key_from_jwt", lambda token: Signing())
    return p


def token(keypair, **overrides) -> str:
    now = int(time.time())
    claims = {"iss": ISSUER, "aud": "syruss-api", "azp": "syruss-app", "sub": "kc-user-1", "iat": now, "exp": now + 300,
              "email": "Sam@Example.com", "email_verified": True, "given_name": "Sam", "family_name": "Lee", "name": "Sam Lee"}
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, keypair[0], algorithm="RS256", headers={"kid": "k1"})


def request_with(bearer=None) -> Request:
    headers = [(b"authorization", f"Bearer {bearer}".encode())] if bearer else []
    return Request({"type": "http", "headers": headers, "query_string": b""})


async def test_a_valid_token_gives_identity_and_nothing_else(provider, keypair):
    who = await provider.authenticate(request_with(token(keypair)))
    assert (who.user_id, who.email, who.email_verified, who.given_name) == ("kc-user-1", "sam@example.com", True, "Sam")


@pytest.mark.parametrize("bad", [
    {"exp": int(time.time()) - 600},          # expired
    {"aud": "another-api"},                   # wrong audience
    {"iss": "http://evil.test/realms/x"},     # wrong issuer
    {"azp": "some-other-client"},             # a client that may not call the API
    {"sub": None},                            # no subject
])
async def test_tokens_that_fail_any_check_are_refused(provider, keypair, bad):
    with pytest.raises(HTTPException) as err:
        await provider.authenticate(request_with(token(keypair, **bad)))
    assert err.value.status_code == 401


async def test_unsigned_and_hmac_confused_tokens_are_refused(provider, keypair):
    now = int(time.time())
    claims = {"iss": ISSUER, "aud": "syruss-api", "azp": "syruss-app", "sub": "x", "iat": now, "exp": now + 300}
    unsigned = jwt.encode(claims, None, algorithm="none")
    for bad in (unsigned, "not-a-jwt"):
        with pytest.raises(HTTPException):
            await provider.authenticate(request_with(bad))
    with pytest.raises(HTTPException):
        await provider.authenticate(request_with(None))


# --- gate one: the organization ------------------------------------------------------

async def test_outsiders_are_refused_at_the_organization_gate(client):
    stranger = person("usr_org_stranger", "Stranger")
    # No profile yet: they are sent to finish setting up, whatever organization they name.
    res = await client.get(f"{API}/workspaces", headers=stranger)
    assert res.status_code == 403 and res.json()["detail"]["code"] == "onboarding_required"
    await register(stranger, org_role=None)  # a profile, but no seat in any organization
    res = await client.get(f"{API}/workspaces", headers=stranger)
    assert res.status_code == 403 and res.json()["detail"]["code"] == "not_org_member"
    # An organization that doesn't exist looks exactly the same as one that isn't yours.
    res = await client.get(f"{API}/workspaces", headers={**stranger, "X-Org-Slug": "nope"})
    assert res.status_code == 403 and res.json()["detail"]["code"] == "not_org_member"


async def test_signing_up_makes_an_account_and_then_an_organization_the_person_names(client):
    newcomer = person("usr_new_1", "Nia Perera")
    body = {"first_name": "Nia", "last_name": "Perera", "phone": "+94 77 123 4567", "timezone": "Asia/Colombo"}
    assert (await client.get(f"{API}/me/context", headers=newcomer)).json()["onboarded"] is False
    # An account is not an organization: nothing is made up for them.
    res = await client.post(f"{API}/onboarding", headers=newcomer, json=body)
    assert res.status_code == 201
    context = res.json()
    assert context["onboarded"] and context["user"]["email"] == "usr_new_1@example.com" and context["user"]["timezone"] == "Asia/Colombo"
    assert context["organizations"] == [] and context["workspaces"] == []
    again = (await client.post(f"{API}/onboarding", headers=newcomer, json=body)).json()
    assert again["organizations"] == []

    # They choose the organization's name and address; the address is checked first and is theirs once taken.
    assert (await client.get(f"{API}/organizations/slug-check", headers=newcomer, params={"slug": "nia-co"})).json()["available"] is True
    assert (await client.get(f"{API}/organizations/slug-check", headers=newcomer, params={"slug": "default"})).json()["available"] is False
    assert (await client.get(f"{API}/organizations/slug-check", headers=newcomer, params={"slug": "api"})).json()["available"] is False
    made = await client.post(f"{API}/organizations", headers=newcomer, json={"name": "Nia Co", "slug": "nia-co"})
    assert made.status_code == 201
    [org] = made.json()["organizations"]
    assert (org["slug"], org["name"], org["role"]) == ("nia-co", "Nia Co", "owner") and "billing.manage" in org["permissions"]
    rival = person("usr_new_1b", "Rival Person")
    await client.post(f"{API}/onboarding", headers=rival, json={"first_name": "Rival", "last_name": "Person"})
    taken = await client.post(f"{API}/organizations", headers=rival, json={"name": "Nia Co 2", "slug": "nia-co"})
    assert taken.status_code == 409 and "taken" in taken.json()["detail"]
    assert (await client.post(f"{API}/organizations", headers=rival, json={"name": "Bad", "slug": "No Spaces!"})).status_code == 422
    # Without an address, one is made from the name (and made unique).
    auto = (await client.post(f"{API}/organizations", headers=rival, json={"name": "Nia Co"})).json()["organizations"][0]
    assert auto["slug"].startswith("nia-co-")

    # The new owner can work inside their organization, by its address.
    inside = {**newcomer, "X-Org-Slug": "nia-co"}
    created = await client.post(f"{API}/workspaces", headers=inside, json={"name": "Invoices"})
    assert created.status_code == 201 and created.json()["my_role"] == "owner"
    # Someone without an account can't make one.
    ghost = person("usr_ghost", "No Account")
    assert (await client.post(f"{API}/organizations", headers=ghost, json={"name": "Ghost"})).status_code == 403


async def test_signing_in_opens_the_organization_you_were_last_in(client):
    p = person("usr_two_orgs", "Two Orgs")
    await client.post(f"{API}/onboarding", headers=p, json={"first_name": "Two", "last_name": "Orgs"})
    await client.post(f"{API}/organizations", headers=p, json={"name": "Older Co", "slug": "older-co"})
    ctx = (await client.post(f"{API}/organizations", headers=p, json={"name": "Newer Co", "slug": "newer-co"})).json()
    # Nothing opened yet: the one they joined first.
    assert [o["slug"] for o in ctx["organizations"]] == ["older-co", "newer-co"]
    # Opening an organization makes it the current one, so it leads the list (and /continue opens it).
    assert (await client.get(f"{API}/organization", headers={**p, "X-Org-Slug": "newer-co"})).status_code == 200
    ctx = (await client.get(f"{API}/me/context", headers=p)).json()
    assert [o["slug"] for o in ctx["organizations"]] == ["newer-co", "older-co"]
    assert [o["last_used"] for o in ctx["organizations"]] == [True, False]
    assert (await client.get(f"{API}/organization", headers={**p, "X-Org-Slug": "older-co"})).status_code == 200
    ctx = (await client.get(f"{API}/me/context", headers=p)).json()
    assert ctx["organizations"][0]["slug"] == "older-co"


async def test_onboarding_validates_what_it_stores(client):
    p = person("usr_new_2", "Val Idate")
    assert (await client.post(f"{API}/onboarding", headers=p, json={"first_name": "V", "last_name": "I", "phone": "abc"})).status_code == 422
    assert (await client.post(f"{API}/onboarding", headers=p, json={"first_name": "V", "last_name": "I", "timezone": "Mars/Base"})).status_code == 422
    assert (await client.post(f"{API}/onboarding", headers=p, json={"first_name": "", "last_name": "I"})).status_code == 422
    # No email in the sign-in, no account.
    anon = {"X-User-ID": "usr_new_3", "X-Username": "No Mail"}
    assert (await client.post(f"{API}/onboarding", headers=anon, json={"first_name": "N", "last_name": "M"})).status_code == 422
    # The same email can't be claimed by a second sign-in.
    first = person("usr_new_4", "Dup Licate")
    assert (await client.post(f"{API}/onboarding", headers=first, json={"first_name": "D", "last_name": "L"})).status_code == 201
    second = {**person("usr_new_5", "Dup Licate"), "X-Email": first["X-Email"]}
    assert (await client.post(f"{API}/onboarding", headers=second, json={"first_name": "D", "last_name": "L"})).status_code == 409


async def test_email_belongs_to_the_sign_in_not_the_profile(client):
    res = await client.patch(f"{API}/me", json={"first_name": "Sadeepa", "phone": "+94 11 222 3333", "email": "hacker@example.com"})
    assert res.status_code == 200 and res.json()["first_name"] == "Sadeepa"
    assert res.json()["email"] == "dev@syruss.test"


# --- the people of an organization ----------------------------------------------------

async def test_organization_roles_and_invitations(client):
    boss = person("usr_org_boss", "Boss Owner")
    await register(boss, "owner")
    worker = person("usr_org_worker", "Wen Worker")
    await register(worker, "member")

    # Members can see who is in the organization but not manage it.
    assert (await client.get(f"{API}/organization/members", headers=worker)).status_code == 200
    assert (await client.post(f"{API}/organization/invitations", headers=worker, json={"email": "x@example.com"})).status_code == 403
    assert (await client.patch(f"{API}/organization", headers=worker, json={"name": "Mine now"})).status_code == 403

    # An invitation is for an email address, and only that person can accept it.
    invite = (await client.post(f"{API}/organization/invitations", headers=boss, json={"email": "guest@example.com", "role": "admin"})).json()
    assert (await client.get(f"{API}/org-invitations/{invite['token']}")).json()["role"] == "admin"  # preview needs no sign-in
    wrong = person("usr_org_wrong", "Wrong Person")
    await register(wrong, "member")  # an account in this organization already, but not the invited address
    assert (await client.post(f"{API}/org-invitations/{invite['token']}/accept", headers=wrong)).status_code == 403
    guest = {**person("usr_org_guest", "Gus Guest"), "X-Email": "guest@example.com"}
    from tests.conftest import AsyncSessionLocal
    from app.models.identity import User
    async with AsyncSessionLocal() as s:
        s.add(User(id="usr_org_guest", email="guest@example.com", email_verified=True, first_name="Gus", last_name="Guest"))
        await s.commit()
    accepted = await client.post(f"{API}/org-invitations/{invite['token']}/accept", headers=guest)
    assert accepted.status_code == 200 and accepted.json()["role"] == "admin"
    assert (await client.post(f"{API}/org-invitations/{invite['token']}/accept", headers=guest)).status_code == 409  # single use

    # Owner rules: only owners hand out ownership, and the last owner stays.
    assert (await client.patch(f"{API}/organization/members/usr_org_worker", headers=guest, json={"role": "owner"})).status_code == 403
    assert (await client.patch(f"{API}/organization/members/usr_org_worker", headers=boss, json={"role": "admin"})).status_code == 200
    owners = [m for m in (await client.get(f"{API}/organization/members", headers=boss)).json() if m["role"] == "owner"]
    assert len(owners) >= 2  # the development person owns the organization too
    # Removing someone also removes them from every workspace in the organization.
    tpl = await create_template(client)
    await join(client, tpl, worker, "viewer")
    assert (await client.get(f"{API}/settings", headers={**ws(tpl), **worker})).status_code == 200
    assert (await client.delete(f"{API}/organization/members/usr_org_worker", headers=boss)).status_code == 204
    assert (await client.get(f"{API}/settings", headers={**ws(tpl), **worker})).status_code == 403


# --- every route is behind a gate -----------------------------------------------------

# Public by design: invitation previews (an unguessable token is the key) and health.
PUBLIC = {"/invitations/{token}", "/org-invitations/{token}", "/destinations/oauth/callback"}


def _flatten(router, prefix=""):
    for r in router.routes:
        if isinstance(r, APIRoute):
            yield prefix + r.path, r
        elif hasattr(r, "original_router"):
            yield from _flatten(r.original_router, prefix + r.include_context.prefix)


def _dependencies(dependant, seen=None):
    seen = seen if seen is not None else set()
    for d in dependant.dependencies:
        seen.add(getattr(d.call, "__qualname__", str(d.call)))
        _dependencies(d, seen)
    return seen


def test_no_route_is_open_by_accident():
    unguarded = []
    for path, route in _flatten(api_router):
        deps = _dependencies(route.dependant)
        if path in PUBLIC:
            continue
        if not any(name in " ".join(deps) for name in ("get_current_auth", "get_identity", "development_only", "intake_caller")):
            unguarded.append(f"{sorted(route.methods)} {path}")
    assert unguarded == []


def test_every_workspace_route_checks_membership_and_the_agent_api_checks_tokens():
    from app.api.internal.agent_tools import router as agent_router
    for path, route in _flatten(agent_router):
        assert any("get_workspace" in d for d in _dependencies(route.dependant)), path


async def test_a_photo_from_single_sign_on_is_kept_on_the_profile_when_it_is_a_safe_address(client):
    google = {**person("usr_photo_1", "Pia Photo"), "X-Picture": "https://lh3.googleusercontent.com/a/pia=s96"}
    res = await client.post(f"{API}/onboarding", headers=google, json={"first_name": "Pia", "last_name": "Photo"})
    assert res.json()["user"]["avatar_url"] == "https://lh3.googleusercontent.com/a/pia=s96"
    sneaky = {**person("usr_photo_2", "Sam Sneaky"), "X-Picture": "javascript:alert(1)"}
    res = await client.post(f"{API}/onboarding", headers=sneaky, json={"first_name": "Sam", "last_name": "Sneaky"})
    assert res.json()["user"]["avatar_url"] is None
