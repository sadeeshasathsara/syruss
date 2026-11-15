---
server: minor
www: patch
---

- **Documents sent by email now arrive through the mail service.** An owner gives the **Document inbound** job a mailbox that can read (Google, Microsoft or any IMAP server) in the admin app, and each email channel's address becomes that mailbox with a tag, like `docs+<token>@yourdomain`. The mail service reads the mailbox and hands each message over on Kafka; every attached file becomes a document of the channel it was addressed to, under the same rules as any other document (allowed senders, the SPF/DKIM requirement, file checks, duplicate protection).
- **The server's own mail receiver is gone**, with `INTAKE_SMTP_PORT`, `INTAKE_SMTP_HOST` and `INTAKE_EMAIL_DOMAIN`: the server runs no mail listener and holds no mail setting. Email channels made before show their new address on their page; the old `token@intake-domain` addresses stop working.
- Refused mail is written to the channel's activity with the reason. Nothing is mailed back to the sender, since the From address of unwanted mail is often forged.
