"""
Turns Google sign-in on (or off) for the local Keycloak realm.

    python bridge/keycloak/google.py on  <client-id> <client-secret>
    python bridge/keycloak/google.py off

Create the OAuth client in Google Cloud first (APIs & Services > Credentials > OAuth client ID > Web application)
with this redirect URI:  http://localhost:8080/realms/syruss/broker/google/endpoint  (your Keycloak address in production).
The "Continue with Google" button then appears on the sign-in and register pages by itself.
Needs KEYCLOAK_URL / KEYCLOAK_ADMIN / KEYCLOAK_ADMIN_PASSWORD in the environment, or the local defaults.
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

KC = os.environ.get("KEYCLOAK_URL", "http://localhost:8080").rstrip("/")
ADMIN = os.environ.get("KEYCLOAK_ADMIN", "admin")
PASSWORD = os.environ.get("KEYCLOAK_ADMIN_PASSWORD", "admin-change-me")


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
    try:
        with urllib.request.urlopen(req) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def wire_photo(token: str) -> None:
    """So the photo Google shares reaches the app: a profile attribute, an import mapper and a token claim."""
    _, profile = call("GET", "/admin/realms/syruss/users/profile", token)
    if not any(a["name"] == "picture" for a in profile["attributes"]):
        profile["attributes"].append({
            "name": "picture", "displayName": "Picture", "validations": {"length": {"max": 512}},
            "permissions": {"view": ["admin", "user"], "edit": ["admin"]},
        })
        print("profile attribute", call("PUT", "/admin/realms/syruss/users/profile", token, profile)[0])
    _, mappers = call("GET", "/admin/realms/syruss/identity-provider/instances/google/mappers", token)
    if not any(m["name"] == "picture" for m in mappers):
        print("import mapper", call("POST", "/admin/realms/syruss/identity-provider/instances/google/mappers", token, {
            "name": "picture", "identityProviderAlias": "google", "identityProviderMapper": "oidc-user-attribute-idp-mapper",
            "config": {"claim": "picture", "user.attribute": "picture", "syncMode": "FORCE"},
        })[0])
    for client_id in ("syruss-site", "syruss-app"):
        _, found = call("GET", f"/admin/realms/syruss/clients?clientId={client_id}", token)
        client = found[0]
        if not any(m["name"] == "picture" for m in client.get("protocolMappers", [])):
            print("claim", client_id, call("POST", f"/admin/realms/syruss/clients/{client['id']}/protocol-mappers/models", token, {
                "name": "picture", "protocol": "openid-connect", "protocolMapper": "oidc-usermodel-attribute-mapper",
                "config": {"user.attribute": "picture", "claim.name": "picture", "jsonType.label": "String",
                           "id.token.claim": "true", "access.token.claim": "true", "userinfo.token.claim": "true"},
            })[0])


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in ("on", "off") or (sys.argv[1] == "on" and len(sys.argv) != 4):
        print(__doc__)
        return 2
    _, tok = call("POST", "/realms/master/protocol/openid-connect/token",
                  form={"client_id": "admin-cli", "username": ADMIN, "password": PASSWORD, "grant_type": "password"})
    token = tok["access_token"]
    status, idp = call("GET", "/admin/realms/syruss/identity-provider/instances/google", token)
    if status != 200:
        print("The google provider is missing from the realm (re-import realm-syruss.json).")
        return 1
    if sys.argv[1] == "on":
        idp["enabled"] = True
        idp["config"]["clientId"], idp["config"]["clientSecret"] = sys.argv[2], sys.argv[3]
    else:
        idp["enabled"] = False
    status, out = call("PUT", "/admin/realms/syruss/identity-provider/instances/google", token, idp)
    if sys.argv[1] == "on" and status in (200, 204):
        wire_photo(token)
    print("google", "on" if idp["enabled"] else "off", status)
    return 0 if status in (200, 204) else 1


if __name__ == "__main__":
    sys.exit(main())
