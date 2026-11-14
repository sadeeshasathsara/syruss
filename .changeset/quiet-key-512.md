---
server: minor
admin: minor
---

- Platform staff are added only from the terminal: `syruss staff add EMAIL --role ROLE`, or `python -m app.invite_staff` on a server.
  - For an address with no sign-in, it creates one in Keycloak, and Keycloak emails a link to choose a password and set up the one-time code. That link leads to the invitation.
  - The Keycloak admin login comes only from the command's own environment, so the running server gets no Keycloak access.
- The admin app no longer invites or resends invitations: its Staff page shows the command, and owners can still withdraw invitations. Its sign-in page still creates no accounts.
