---
admin: minor
server: minor
site: patch
www: patch
---

- The system admin app (admin/, port 3200), for the people who run the platform: service health, platform figures, the platform staff and their audit log
- It signs in through its own Keycloak client with a password and a one-time code every time; `bridge/keycloak/harden.py` adds the client and its sign-in flow to a running realm
- The admin app's sign-in pages offer no sign-up and no other providers, and setting up a one-time code fits one screen, on phones too
- The /api/v1/sysadmin API answers only the admin app's tokens and only platform staff, whose roles (owner, operator, support, billing, viewer) each allow only their part; staff changes need a fresh sign-in
- Every staff action is written to a platform audit log that the database refuses to change or delete
- An organization records why it is shut: suspended by platform staff, or closed by its owner
- site/, www/ and admin/ share one sign-in implementation, checked identical in CI
