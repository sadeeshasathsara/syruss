---
mail: minor
server: minor
admin: patch
---

- **No mail password or server comes from an environment file any more.** `MAIL_SMTP_*` and `MAIL_IMAP_*` are gone from the mail service. Until a mailbox is connected (`python -m mail connect`, or the admin app's Email page), mail simply waits, without using up its tries, and goes out once one is. `MAIL_BACKEND` is now `none` (default), `console` or `memory`; the old `smtp` value is read as `none`, so set up your mailbox again with `python -m mail connect` (the README shows the local Mailpit command). A real server refuses to start with anything but `none`.
- **Which mailbox serves a job is now part of the job** (the separate routes table is copied over once, then dropped). Jobs with no mailbox show "No mailbox" in the admin app.
- **Kafka can use TLS and SASL.** `KAFKA_SECURITY_PROTOCOL`, `KAFKA_SASL_MECHANISM`, `KAFKA_SASL_USERNAME`, `KAFKA_SASL_PASSWORD` and `KAFKA_SSL_CA_FILE` apply to every Kafka connection in the mail service and the server; off by default.
- **Docs:** the mail README now has first-install steps, a Kafka security guide with the exact ACLs for the mail and server users, key rotation, and backup and restore; the deploy guide follows.
