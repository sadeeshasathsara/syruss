---
mail: minor
server: minor
---

- **The server and the mail service talk only over Kafka; the mail service's HTTP API is gone.**
  - **Setting up mail from the admin app:** each change (connect or remove a mailbox, choose which account serves a job, add your own jobs, connect Google, send a given-up message again, read the delivery log) is a command the server signs and puts on `mail.commands`; the mail service carries out only what is signed, recent and new, and answers on `mail.replies`. The Email page shows the mail service's own directory (`mail.directory`), so it is always what the mail service says. New admin API for jobs: `POST /sysadmin/mail/purposes`, `PUT` and `DELETE /sysadmin/mail/purposes/{key}`.
  - **Passwords:** a mailbox password is sealed to the mail service's public key before it leaves the server, so it never crosses Kafka in the clear; the admin app can seal it in the browser (the API takes a ready `sealed` value).
  - **Incoming mail:** emails the mail service reads are published whole on `mail.inbound`; the server turns support mail into tickets and acknowledges each one (`mail.inbound.acks`), and only then does the mail service delete it. One that is never acknowledged is published again, and one over 25 MB is reported instead of lost. Mail for a job nothing here handles yet (document inbound) waits at the mail service.
  - **Addresses:** the server no longer asks for them over HTTP. It reads the sender's and the help mailbox's addresses from the directory, and recognises mail from any of our own mailboxes (so a reply loop never opens a ticket).
  - **Removed:** `MAIL_SERVICE_URL` and `MAIL_SERVICE_TOKEN` (server), `MAIL_API_HOST`, `MAIL_API_PORT` and `MAIL_SERVICE_TOKEN` (mail), the mail service's web server, and the scheduler's inbound sweep.
  - **Deploying:** run `python -m app.mail_signing_key` once. Put the first line (`MAIL_COMMAND_SIGNING_KEY`) in `server.env` and the second (`MAIL_TRUSTED_SERVER_KEY`) in `mail.env`; both services refuse to start without their half. Deploy the mail service first, then the server.
