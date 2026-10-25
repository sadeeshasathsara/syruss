# Keycloak (authentication only)

Keycloak answers *who you are*: passwords, MFA, Google sign-in, sessions, tokens. It does not hold
organizations, workspaces or roles; those live in the core database and are checked on every request.

## Run locally

```
cd bridge
docker compose up -d keycloak-db keycloak
```

Admin console: http://localhost:8080 (credentials from `bridge/.env`). The `syruss` realm is imported
from `realm-syruss.json` on first start. Re-import by deleting the `keycloak_db_data` volume.

## The sign-in and registration pages

`themes/syruss/login` restyles Keycloak's pages (sign in, register, reset password, two-step codes) to match
the site: dark ground, one sheet, IBM Plex Sans (bundled, licence in `resources/fonts`), the same buttons.
It extends the built-in `keycloak.v2` theme, so every flow Keycloak has is covered. The compose file mounts it
and `realm-syruss.json` selects it (`loginTheme`). In development mode edits show on reload; in production
build the theme into your image or mount it the same way. The pages are dark in every browser, like the site's default.

## What the realm sets

- Clients `syruss-site` and `syruss-app`: confidential, authorization code + PKCE S256, exact redirect
  URIs (no wildcards), no implicit or password grant.
- Access tokens carry `aud=syruss-api` (the core API checks it). They carry identity only: no tenant or
  role claims.
- Access token 5 min, SSO idle 30 min, SSO max 10 h, refresh-token rotation, brute-force lockout,
  12-character password policy, user-enrollable TOTP (Account console > Signing in).

## Before production

- **Email verification is off.** Turn `verifyEmail` on once SMTP is configured (Realm settings > Email).
  Until then, keep password signup closed or Google-only: email-bound invitations rely on verified emails.
- **Google:** the identity provider ships disabled, so no Google button shows (a button that can't sign anyone in
  would be worse than none). To turn it on: create an OAuth client in Google Cloud (APIs & Services > Credentials >
  OAuth client ID > Web application) with the redirect URI `http://localhost:8080/realms/syruss/broker/google/endpoint`,
  then run `python bridge/keycloak/google.py on <client-id> <client-secret>`. "Continue with Google" then appears on
  both the sign-in and register pages (`google.py off` removes it). Leave account linking at manual review so an
  unverified local account cannot claim a Google identity.
- Run `start --optimized` behind TLS with `KC_HOSTNAME` and `KC_PROXY_HEADERS=xforwarded`, a strong
  admin password, and a managed database; keep `sslRequired=external`.
- Replace the dev client secrets; the same values go in `site/.env.local` and `www/.env.local`.
