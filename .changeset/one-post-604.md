---
mail: minor
server: minor
---

- **A mail service: every email Syruss sends goes through one place** (the new `mail/` service). It holds the only mail credentials.
  - **The server never talks to a mail server again.** Sending an email queues it in the database (`mail_outbox`, sealed while it waits). A relay publishes it on Kafka (`mail.outbound`), and the mail service sends it and reports back (`mail.events`). Mail waits safely while Kafka or the mail service is down.
  - **Keycloak's emails** (verify an address, reset a password, a new staff member's set-up link) go to the mail service's SMTP door on the internal network, with their own login. `harden.py --smtp` points Keycloak there.
  - **One mail interface with permissions:** a mail account says what it was given (send, read), and each use opens it for only what it needs.
  - **Purposes route to accounts:** `notifications`, `accounts` (Keycloak) and `support`. Until an account is connected for a purpose, the mail service's own account (`MAIL_SMTP_*` in `mail.env`) sends.
  - **Delivery:** each email is sent once, even if handed over twice. Failures are retried with growing waits and given up after a while; an address the server refuses is given up at once. The text isn't kept once an email is done.
  - **Admin overview:** a new Email health check shows mail that couldn't be sent or is waiting too long.
- **Settings moved:** `SMTP_*` and `SUPPORT_SMTP_*` leave the server; the mail server is set in `mail.env` (`MAIL_SMTP_*`). The server's `MAIL_BACKEND` is now `service` (or `console`, `memory`).
