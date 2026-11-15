# Mail service

Every email Syruss sends goes through this service. It is the only place that holds mail credentials, and the only
thing that talks to mail providers.

```
server ──(mail.outbound, Kafka)──▶ mail ──▶ the account routed to the purpose (SMTP, …)
server ◀──(mail.events, Kafka)──── mail     sent / retrying / dead, for each job
```

## The interface

`accounts/base.py` defines `MailAccount`: one mailbox, which states what it can do (`supports`) and what it was given
(`granted`). Nothing uses an account directly. `account.open(needs)` returns a `MailHandle` with only the abilities
asked for (`Capability.SEND`, `Capability.READ`), and refuses with `MailNotPermitted` when the account wasn't given
them.

| Kind | Sends | Reads |
|---|---|---|
| `SmtpImapAccount` (`accounts/smtp_imap.py`) | SMTP | IMAP, read-only (the mailbox is never changed) |
| `ConsoleAccount`, `MemoryAccount` (`accounts/local.py`) | logs it / keeps it | tests only |

## Purposes

Each use of mail is a purpose with the capabilities it needs (`router.py`):

| Purpose | Needs | What |
|---|---|---|
| `notifications` | send | invitations, sign-in codes, billing, staff notices, records delivered by email |
| `accounts` | send | Keycloak's own emails (verify an address, reset a password, a new staff member's set-up link) |
| `support` | read, send | support tickets |

A purpose uses the account routed to it. With no route, or while that account needs reconnecting or can't do
everything the purpose needs, it falls back to the account from the environment (`MAIL_SMTP_*`, `MAIL_FROM`), which is
also how a fresh install sends its first emails.

## Delivery

`delivery.py` handles each job:
- **Sent once:** a job is recorded by the publisher's id, so a job published twice is sent once.
- **Retries:** failures are tried again after 30s, 60s, 120s and so on (at most an hour) until `MAX_ATTEMPTS`, then given up.
- **Refused addresses** are given up at once.
- **Crash recovery:** a job held by a process that died mid-send is taken again once its lease runs out.
- **What's kept:** the message itself only until the job is done. The delivery log keeps the recipient, subject and outcome, never the text.

## Running it

```
cp mail/.env.example mail/.env   # then fill in the database password
python -m mail                   # from the repository root
```

Run the tests from inside `mail/` with `pytest`. They use their own database, `syruss_mail_test`.
