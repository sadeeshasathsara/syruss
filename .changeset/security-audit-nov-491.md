---
mail: minor
server: minor
admin: patch
www: patch
---

- **The key that passwords typed in the admin app are sealed to can't be swapped by someone writing to Kafka.** The mail service signs it; the server believes it only with the mail service's public key (`MAIL_SERVICE_PUBLIC_KEY`, from `python -m mail signer`). A real server now refuses to start without it.
- **Adding a mail server on a real server only accepts public addresses** (not localhost, a private network, a link-local or cloud metadata address).
- **A ticket by email can't be written into by a spoofed sender.** When the receiving mail server records no sender checks, only a message that answers one of ours continues a ticket; a number in the subject alone opens a separate ticket.
- **New email channels get longer, unguessable addresses.**
- Full findings and what was checked: `docs/SECURITY_AUDIT_2026-11.md`.
