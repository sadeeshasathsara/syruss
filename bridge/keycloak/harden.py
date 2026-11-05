"""
Checks the running Keycloak realm against the sign-in settings Syruss expects, and fixes what differs.

    python bridge/keycloak/harden.py            # show what differs and fix it
    python bridge/keycloak/harden.py --check    # only show (exit 1 if anything differs)
    python bridge/keycloak/harden.py --smtp     # also give Keycloak the mail server in server/.env, so it can send
                                                # "forgot your password" emails (stores the SMTP password in Keycloak)

The same settings are in realm-syruss.json, which a brand-new Keycloak imports; this script brings an existing one in line.
It also adds any client the realm file has and the running realm lacks (such as syruss-admin), and gives the system admin
app its own sign-in flow that always asks for a one-time code. A brand-new Keycloak needs this run once too: the import
creates the client, but the flow is made here.
Needs KEYCLOAK_URL / KEYCLOAK_ADMIN / KEYCLOAK_ADMIN_PASSWORD in the environment, or the local defaults.
"""
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

KC = os.environ.get("KEYCLOAK_URL", "http://localhost:8080").rstrip("/")
ADMIN = os.environ.get("KEYCLOAK_ADMIN", "admin")
PASSWORD = os.environ.get("KEYCLOAK_ADMIN_PASSWORD", "admin-change-me")
REALM = "syruss"

# What the realm must say, and why.
WANTED = {
    # Guessing passwords
    "bruteForceProtected": True,          # failed sign-ins slow the attacker down...
    "failureFactor": 5,                   # ...after five wrong passwords...
    "maxFailureWaitSeconds": 900,         # ...by up to 15 minutes...
    "minimumQuickLoginWaitSeconds": 60,
    "permanentLockout": False,            # ...but never locks the real owner out for good
    # Weak passwords
    "passwordPolicy": "length(12) and notUsername and notEmail and passwordHistory(3)",
    # Stolen sessions and tokens
    "accessTokenLifespan": 300,           # an access token is good for five minutes
    "ssoSessionIdleTimeout": 1800,        # 30 minutes without activity ends the session
    "ssoSessionMaxLifespan": 36000,       # and no session lives past ten hours
    "revokeRefreshToken": True,           # a refresh token works once...
    "refreshTokenMaxReuse": 0,            # ...and a replayed one revokes the whole session (theft detected)
    "sslRequired": "external",            # plain http only for localhost
    "defaultSignatureAlgorithm": "RS256",
    # Accounts
    "registrationEmailAsUsername": True,
    "duplicateEmailsAllowed": False,
    "editUsernameAllowed": False,
    "resetPasswordAllowed": True,
    # A trail to look at when something goes wrong
    "eventsEnabled": True,
    "eventsExpiration": 7776000,          # 90 days
    "adminEventsEnabled": True,
    "adminEventsDetailsEnabled": True,
}
# Browsers shouldn't frame the sign-in pages (clickjacking) or let them be sniffed.
WANTED_HEADERS = {
    "xFrameOptions": "DENY",
    "contentSecurityPolicy": "frame-src 'self'; frame-ancestors 'none'; object-src 'none';",
    "xContentTypeOptions": "nosniff",
    "xRobotsTag": "none",
    "xXSSProtection": "1; mode=block",
    "strictTransportSecurity": "max-age=31536000; includeSubDomains",
}


def call(method, path, token=None, body=None, form=None):
    data, headers = None, {}
    if form is not None:
        data = urllib.parse.urlencode(form).encode()
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(KC + path, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req) as r:
        raw = r.read()
        return json.loads(raw) if raw else None


def smtp_from_env() -> dict:
    values = {}
    for line in (Path(__file__).resolve().parents[2] / "server" / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            k, _, v = line.partition("=")
            values[k.strip()] = v.strip()
    host, port, security = values.get("SMTP_HOST", ""), values.get("SMTP_PORT", "587"), values.get("SMTP_SECURITY", "starttls").lower()
    return {
        "host": host, "port": port, "from": values.get("MAIL_FROM", ""), "fromDisplayName": values.get("MAIL_FROM_NAME", "Syruss"),
        "auth": "true" if values.get("SMTP_USERNAME") else "false", "user": values.get("SMTP_USERNAME", ""), "password": values.get("SMTP_PASSWORD", ""),
        "starttls": "true" if security == "starttls" else "false", "ssl": "true" if security == "ssl" else "false",
    }


# The system admin app signs in through its own copy of the browser flow: no riding on an existing session, no
# redirect to another provider, and a one-time code every time (someone without one is asked to set it up first).
ADMIN_CLIENT = "syruss-admin"
ADMIN_FLOW = "syruss admin browser"
# Development values the compose file falls back to; a real deployment sets SYRUSS_* (see bridge/.env.example).
DEFAULTS = {"SYRUSS_SITE_URL": "http://localhost:3100", "SYRUSS_APP_URL": "http://localhost:3000", "SYRUSS_ADMIN_URL": "http://localhost:3200",
            "SYRUSS_SITE_CLIENT_SECRET": "dev-site-secret-change-me", "SYRUSS_APP_CLIENT_SECRET": "dev-app-secret-change-me",
            "SYRUSS_ADMIN_CLIENT_SECRET": "dev-admin-secret-change-me"}


def realm_clients() -> list:
    """The clients realm-syruss.json defines, with ${SYRUSS_*} filled in as Keycloak's import would."""
    def fill(value):
        if isinstance(value, str):
            return re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), DEFAULTS.get(m.group(1), m.group(0))), value)
        if isinstance(value, list):
            return [fill(v) for v in value]
        if isinstance(value, dict):
            return {k: fill(v) for k, v in value.items()}
        return value
    return [fill(c) for c in json.loads((Path(__file__).with_name("realm-syruss.json")).read_text(encoding="utf-8"))["clients"]]


def wanted_requirement(execution: dict, parent: str):
    """What each step of the admin flow must be (None: leave it as the copied browser flow has it)."""
    provider, name, subflow = execution.get("providerId"), execution.get("displayName", ""), execution.get("authenticationFlow")
    if provider in ("auth-cookie", "identity-provider-redirector") or (subflow and execution["level"] == 0 and name.endswith(" Organization")):
        return "DISABLED"
    if provider == "conditional-user-configured" and parent.endswith("Conditional OTP"):
        return "DISABLED"   # the code is asked of everyone, not only of those who already set one up
    if provider == "auth-otp-form" or (subflow and (name.endswith("Conditional OTP") or name.endswith(" forms"))):
        return "REQUIRED"
    return None


def ensure_admin_sign_in(token: str, check_only: bool) -> list:
    """Missing clients created, the admin flow in place and bound to the admin client. Returns what differed."""
    differed = []
    for wanted in realm_clients():
        if not call("GET", f"/admin/realms/{REALM}/clients?clientId={urllib.parse.quote(wanted['clientId'])}", token):
            differed.append(f"client {wanted['clientId']}: missing")
            if not check_only:
                call("POST", f"/admin/realms/{REALM}/clients", token, wanted)
    flows = {f["alias"]: f for f in call("GET", f"/admin/realms/{REALM}/authentication/flows", token)}
    if ADMIN_FLOW not in flows:
        differed.append(f"flow '{ADMIN_FLOW}': missing")
        if check_only:
            return differed
        call("POST", f"/admin/realms/{REALM}/authentication/flows/browser/copy", token, {"newName": ADMIN_FLOW})
        flows = {f["alias"]: f for f in call("GET", f"/admin/realms/{REALM}/authentication/flows", token)}
    path = f"/admin/realms/{REALM}/authentication/flows/{urllib.parse.quote(ADMIN_FLOW)}/executions"
    parents = {}   # the sub-flow each level is inside, as the list walks the flow top to bottom
    for execution in call("GET", path, token):
        if execution.get("authenticationFlow"):
            parents[execution["level"] + 1] = execution["displayName"]
        want = wanted_requirement(execution, parents.get(execution["level"], ""))
        if want and execution["requirement"] != want:
            differed.append(f"flow step '{execution['displayName']}': {execution['requirement']} -> {want}")
            if not check_only:
                call("PUT", path, token, {**execution, "requirement": want})
    [client] = call("GET", f"/admin/realms/{REALM}/clients?clientId={ADMIN_CLIENT}", token) or [None]
    if client and (client.get("authenticationFlowBindingOverrides") or {}).get("browser") != flows[ADMIN_FLOW]["id"]:
        differed.append(f"client {ADMIN_CLIENT}: signs in through the '{ADMIN_FLOW}' flow")
        if not check_only:
            client["authenticationFlowBindingOverrides"] = {**(client.get("authenticationFlowBindingOverrides") or {}), "browser": flows[ADMIN_FLOW]["id"]}
            call("PUT", f"/admin/realms/{REALM}/clients/{client['id']}", token, client)
    return differed


def main() -> int:
    check_only = "--check" in sys.argv
    token = call("POST", "/realms/master/protocol/openid-connect/token",
                 form={"client_id": "admin-cli", "username": ADMIN, "password": PASSWORD, "grant_type": "password"})["access_token"]
    realm = call("GET", f"/admin/realms/{REALM}", token)
    changes = {k: (realm.get(k), v) for k, v in WANTED.items() if realm.get(k) != v}
    headers = realm.get("browserSecurityHeaders") or {}
    header_changes = {k: (headers.get(k), v) for k, v in WANTED_HEADERS.items() if headers.get(k) != v}

    for name, (now, want) in {**changes, **{f"headers.{k}": v for k, v in header_changes.items()}}.items():
        print(f"  {name}: {now!r} -> {want!r}")
    admin_sign_in = ensure_admin_sign_in(token, check_only)
    for line in admin_sign_in:
        print(f"  {line}")
    if not changes and not header_changes and not admin_sign_in:
        print("The realm already matches.")
    elif admin_sign_in and not check_only and not changes and not header_changes:
        print("Updated.")
    if (changes or header_changes) and not check_only:
        realm.update({k: v for k, (_, v) in changes.items()})
        realm["browserSecurityHeaders"] = {**headers, **WANTED_HEADERS}
        call("PUT", f"/admin/realms/{REALM}", token, realm)
        print("Updated.")
    if "--smtp" in sys.argv and not check_only:
        smtp = smtp_from_env()
        if not smtp["host"]:
            print("No SMTP_HOST in server/.env: Keycloak's mail server left as it is.")
        else:
            realm = call("GET", f"/admin/realms/{REALM}", token)
            realm["smtpServer"] = smtp
            call("PUT", f"/admin/realms/{REALM}", token, realm)
            print(f"Keycloak will send mail through {smtp['host']}:{smtp['port']}.")
    return 1 if (changes or header_changes or admin_sign_in) and check_only else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.URLError as e:
        sys.exit(f"Couldn't reach Keycloak at {KC}: {e}")
