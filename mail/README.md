# Mail service

Every email Syruss sends goes through this service. It is the only place that holds mail credentials, and the only
thing that talks to mail providers.

```
server   ──(mail.outbound, Kafka)──────▶ mail ──▶ the account routed to the purpose (Google, SMTP)
Keycloak ──(SMTP, internal network)────▶ mail     its own emails, through the same accounts
server   ◀──(mail.events, Kafka)─────── mail     sent / retrying / dead, for each job
server   ◀──(mail.inbound, whole emails)─ mail ◀── mailboxes it reads (help, documents, ...), from where it left off
server   ──(mail.inbound.acks)─────────▶ mail     "took it in": the service lets the email go
server   ──(mail.commands, signed)─────▶ mail     the admin app's Email page: accounts, jobs, Google, the log
server   ◀──(mail.replies, mail.directory) mail   the answer to each command; the public state (jobs, accounts, key)
```

Kafka is the only way in: there is no HTTP API, and the server holds no mail setting, address or credential.

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

A purpose uses the account routed to it. While none is connected, or the connected one needs reconnecting or can't do everything the
purpose needs, its mail **waits** at this service (without using up its tries, and shown in the admin app as "No mailbox") and goes out
once a mailbox is connected. No mail credential or server is read from the environment.

## Jobs

A **job** is a named use of mail with a mailbox of its own: the built-in ones are notifications, document inbound,
promotions, help (`support`) and Keycloak's account emails (`accounts`); more can be added from the admin app. Each says whether
it sends, reads or both, is served by one account (kept with the job; without one its mail waits), and may have settings of its own (sender
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

The admin app's **Email** page (owners only) sends signed commands through the server (`commands.py`, `operations.py`):
- **Accounts:** add a mailbox on any mail server, or set up Google mail.
- **Jobs:** choose which account each job uses; add jobs of your own.
- **Checks:** test an account.
- **Delivery log:** see what was sent, and send again mail that was given up.

**Connecting Google or Microsoft** (`oauth.py`) asks only for the permissions the chosen jobs need, and saves what the provider actually granted. Their OAuth apps (client id and secret, plus Microsoft's tenant) are set in the admin app's **Provider apps** tab and kept sealed here (`apps.py`); Either can instead come from `GOOGLE_MAIL_CLIENT_*` or `MICROSOFT_MAIL_CLIENT_*` (and `MICROSOFT_MAIL_TENANT`) in the environment; the dashboard takes precedence. Microsoft rotates refresh tokens, and each new one is saved at once (`accounts/graph.py`). When a provider stops accepting the sign-in (access removed, password changed, or Google's 7-day limit for an app in testing), the account is marked "Needs reconnecting" and its jobs wait until it is connected again.

## Reading mailboxes

`inbound.py` has a poller for each job that reads (help, document inbound, and any you add), every `poll_seconds` of the job (else `MAIL_POLL_SECONDS`):
- **Read-only:** the mailbox is opened for reading only.
- **From where it left off:** a cursor is kept per account and purpose. The first read only notes where the mailbox is.
- **Handed to the server:** each new message is kept (sealed) and published whole on `mail.inbound` (its job, account and size in the headers). The server takes it in, and says so on `mail.inbound.acks`; only then is it deleted here. One the server never acknowledges is published again every few minutes (and dropped after `INBOUND_KEEP_DAYS`). A message over `MAIL_MAX_MESSAGE_BYTES` isn't kept: a notice with no body tells the server it came.

## First install

Nothing secret is in an environment file, so a fresh install has no mailbox. In `mail.env` set the database, `MAIL_MASTER_KEYS`
(`python -m mail keygen`) and `MAIL_TRUSTED_SERVER_KEY` (the first of the pair `python -m app.mail_signing_key` makes on the server;
the other goes in the server's env), then start the service and connect the first mailbox from the server's shell, so the first
set-up link and sign-in codes can go out:

```
python -m mail connect --address no-reply@example.com --smtp-host smtp.example.com --smtp-username no-reply@example.com --password-stdin
python -m mail fingerprint      # compare with the fingerprint the admin app shows beside its forms
python -m mail jobs             # which mailbox serves each job
```

Everything after that is done in the admin app (Email): more mailboxes, Google and Microsoft, which job uses which, jobs of your own.

## Kafka security

Kafka has no login by default, so on any broker that is not on a private network turn on **TLS and SASL**, and give each service only
the access it needs. This service and the server both read the same settings (`kafka_security.py`): `KAFKA_SECURITY_PROTOCOL`
(`SASL_SSL`), `KAFKA_SASL_MECHANISM` (`SCRAM-SHA-512`), `KAFKA_SASL_USERNAME`, `KAFKA_SASL_PASSWORD` and `KAFKA_SSL_CA_FILE`.

1. Give the broker a certificate (`listeners=SASL_SSL://...`, `sasl.enabled.mechanisms=SCRAM-SHA-512`,
   `authorizer.class.name=org.apache.kafka.metadata.authorizer.StandardAuthorizer`, `allow.everyone.if.no.acl.found=false`).
2. Make one user per service, each with its own long random password:
   `kafka-configs.sh --alter --add-config 'SCRAM-SHA-512=[password=...]' --entity-type users --entity-name mail`
3. Allow exactly this (`kafka-acls.sh --add --allow-principal User:<name> --operation <op> --topic <t>`; consumer groups with `--group`):

| Topic | `mail` user | `server` user |
|---|---|---|
| `mail.outbound` | Read | Write |
| `mail.events` | Write | Read |
| `mail.commands` | Read | Write |
| `mail.replies` | Write | Read |
| `mail.directory` | Write | Read |
| `mail.inbound` | Write | Read |
| `mail.inbound.acks` | Read | Write |

   Groups: `mail` reads with `mail`, `mail-commands` and `mail-acks`; the server with `core-mail-events`, `core-mail-inbound` and
   (prefix) `core-mail.`. The `mail` user also needs Create, Describe and AlterConfigs on `mail.*` (it creates its topics at start-up;
   pre-create them and drop those if you prefer). Nobody else may write to `mail.commands`: signed commands are the second lock, not the first.
4. The other services that use Kafka (the agent, the live bridge) need their own users and ACLs on their own topics.

## Keys

- **Master keys** (`MAIL_MASTER_KEYS`, newest first) seal everything secret in the database. **Rotate:** `python -m mail keygen`, put the
  new key *in front* of the list, restart. Everything older still opens; new and changed data is sealed with the new key. Once you have
  reconnected or re-saved what you want re-sealed (or are happy to keep the old key for it), the old key can stay; remove it only after
  every account has been connected again, since data sealed with a removed key can't be read.
- **Box key** (what the browser seals secrets to): made on first use and kept sealed in the database. Its fingerprint is in the
  admin app and in `python -m mail fingerprint`; a changed fingerprint means a different database, so compare after a restore.
- **Signing keys** (the server signs commands, this service trusts the public half): to rotate, make a new pair
  (`python -m app.mail_signing_key`), set the public half here and the private half on the server, restart both. Commands signed
  with the old key are refused from then on.

## Backups

The database (`MAIL_DB`, default `syruss_mail`) holds the accounts, their sealed credentials, jobs, the delivery log and the sealed
box key. Back it up with the rest of Postgres, **and keep `MAIL_MASTER_KEYS` somewhere else**: the database alone is useless without
the keys, and the keys alone are useless without the database. To restore, restore the database, set the same keys, start the service and
run `python -m mail jobs` and the admin app's Test button on each account.

## Running it

```
cp mail/.env.example mail/.env   # then fill in the database password
python -m mail                   # from the repository root
```

Run the tests from inside `mail/` with `pytest`. They use their own database, `syruss_mail_test`.

## Functions and jobs

A **job** is a mailbox with settings, made in the admin app. A **function** is something Syruss does with mail: sending to an email
destination, support tickets, invitations, documents arriving by email. Each function is declared once in the server
(`services/mail_uses.py`) and registered here at start-up (`use.register`); the admin app (Email › Jobs › Where mail is used) chooses
which job each one uses (`use.assign`), and until one is chosen it uses its default job. A message names its function as its `purpose`;
`Store.resolve` turns that into the job, and the job's mailbox sends it. Mail read from a job is handed to the function that job is
chosen for. To give a new feature a mailbox to choose, declare a function in the server and name it when sending (the steps are at the
top of `mail_uses.py`).
