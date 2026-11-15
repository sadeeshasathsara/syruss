# Mail service

Every email Syruss sends goes through this service. It is the only place that holds mail credentials, and the only
thing that talks to mail providers.

```
server   ──(mail.outbound, Kafka)──────▶ mail ──▶ the account routed to the purpose (Google, SMTP)
Keycloak ──(SMTP, internal network)────▶ mail     its own emails, through the same accounts
server   ◀──(mail.events, Kafka)─────── mail     sent / retrying / dead, for each job
server   ◀──(mail.inbound + the API)─── mail ◀── mailboxes it reads (support), from where it left off
server   ──(internal API, a token)─────▶ mail     the admin app's Email page: accounts, routes, Google, the log
```

## The interface

`accounts/base.py` defines `MailAccount`: one mailbox, which states what it can do (`supports`) and what it was given
(`granted`). Nothing uses an account directly. `account.open(needs)` returns a `MailHandle` with only the abilities
asked for (`Capability.SEND`, `Capability.READ`), and refuses with `MailNotPermitted` when the account wasn't given
them.

| Kind | Sends | Reads |
|---|---|---|
| `GmailAccount` (`accounts/gmail.py`) | Gmail API (`gmail.send`) | Gmail API (`gmail.readonly`), from a history id |
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

## Jobs

A **job** is a named use of mail with a mailbox of its own: the built-in ones are notifications, document inbound,
promotions, help (`support`) and Keycloak's account emails (`accounts`); more can be added from the admin app. Each says whether
it sends, reads or both, is served by one account (without one, the environment's), and may have settings of its own (sender
name, reply-to, how often it is read). Each job that reads has a poller of its own, so a slow mailbox never holds up another.

## Control plane (Kafka)

The server changes anything here with **signed commands** on `mail.commands` and gets the answer on `mail.replies`
(`commands.py` lists them and the envelope). Kafka has no login, so a command is carried out only when it is signed by the key in
`MAIL_TRUSTED_SERVER_KEY`, recent, and never seen before. What this service lets others know is the **directory** on the compacted
topic `mail.directory` (`directory.py`): the jobs, the accounts' addresses and status, and the public key, never a secret.
`python -m mail keygen | fingerprint | jobs | connect` is the command line (the first mailbox is connected with `connect`).

## Secrets

Everything secret in the database is sealed with the master keys (`MAIL_MASTER_KEYS`, rotatable), including mail read from mailboxes.
Passwords arrive sealed to this service's public key (a box: X25519, HKDF-SHA256, AES-256-GCM, `sealing.py`), so neither Kafka nor the
server ever holds one. The key's fingerprint is logged at start-up and published in the directory so an owner can check it.

## Who mail is from

This service owns the sender and the support identity; no other service has settings for them. `MAIL_FROM_NAME` and
`MAIL_REPLY_TO` fill in the name and reply address of any job that doesn't set its own, and support mail is sent as
`SUPPORT_FROM_NAME` and answered at `SUPPORT_EMAIL_ADDRESS` (empty: `MAIL_FROM`). The server asks for the two addresses it
needs, `MAIL_FROM` and the support address, at `GET /identity` when it starts and every few minutes after.

## Delivery

`delivery.py` handles each job:
- **Sent once:** a job is recorded by the publisher's id, so a job published twice is sent once.
- **Retries:** failures are tried again after 30s, 60s, 120s and so on (at most an hour) until `MAX_ATTEMPTS`, then given up.
- **Refused addresses** are given up at once.
- **Crash recovery:** a job held by a process that died mid-send is taken again once its lease runs out.
- **What's kept:** the message itself only until the job is done. The delivery log keeps the recipient, subject and outcome, never the text.

## Connecting accounts

The admin app's **Email** page (owners only) talks to the internal API (`api.py`) through the server:
- **Accounts:** add a mailbox on any mail server, or set up Google mail.
- **Purposes:** choose which account each purpose uses.
- **Checks:** test an account.
- **Delivery log:** see what was sent, and send again mail that was given up.

**Connecting Google** (`google.py`) asks only for the permissions the chosen purposes need, and saves what Google actually granted. When Google stops accepting the sign-in (access removed, password changed, or the 7-day limit of an OAuth app still in testing), the account is marked "Needs reconnecting" and its purposes fall back to the account from the environment.

## Reading mailboxes

`inbound.py` reads each purpose that reads (support) every `MAIL_POLL_SECONDS`:
- **Read-only:** the mailbox is opened for reading only.
- **From where it left off:** a cursor is kept per account and purpose. The first read only notes where the mailbox is.
- **Handed to the server:** each new message is kept and announced on `mail.inbound`. The server collects it through the API and turns it into a ticket reply. A scheduler sweep collects anything an announcement missed.

## Running it

```
cp mail/.env.example mail/.env   # then fill in the database password
python -m mail                   # from the repository root
```

Run the tests from inside `mail/` with `pytest`. They use their own database, `syruss_mail_test`.
