---
mail: minor
---

- **Mail has jobs, and the mail service takes signed commands over Kafka** (the first step of moving all mail setup out of the server).
  - **Jobs:** notifications, document inbound, promotions, help and Keycloak's account emails are jobs, and more can be added. Each is served by its own mailbox and has settings of its own (sender name, reply-to, how often it is read). Each job that reads has its own poller, so one slow mailbox doesn't hold up another.
  - **Commands on Kafka:** `mail.commands` (signed by the server, recent, never repeated; anything else is refused), answers on `mail.replies`, and the public state (jobs, accounts' addresses and status, the key secrets are sealed to) on the compacted topic `mail.directory`. The mail service makes these topics itself. The HTTP API stays for now.
  - **Secrets:** `MAIL_MASTER_KEYS` (newest first) can be rotated; mail read from mailboxes is sealed at rest too (`MAIL_SECRETS_KEY` still works). A mailbox password can arrive sealed to the service's public key, so Kafka and the server never hold it.
  - **Command line:** `python -m mail connect` connects the first mailbox (the password is asked for and sealed straight into the database), with `keygen`, `fingerprint` and `jobs`.
  - **Setting up:** add `MAIL_MASTER_KEYS` (`python -m mail keygen`); `MAIL_TRUSTED_SERVER_KEY` isn't needed until the server sends commands (the next step).
