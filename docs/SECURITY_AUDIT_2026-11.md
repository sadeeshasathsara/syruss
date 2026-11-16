# Security audit, November 2026

Scope: what was added since the audits in PRs #48, #58 and #61 (the mail service and its admin pages, document inbound by email, the
records table, drafts kept across a sign-in) and a fresh pass of the scanners over everything.

## Method

- `npm audit` on admin, www and site (production dependencies): no known vulnerabilities.
- `bandit` over `server/app`, `mail` and `agentic_framework` (medium and above): 14 findings, all reviewed and none exploitable. The
  Cypher and SuiteQL/QuickBooks query builders quote and escape every value (`_lit`, `_s`, `_q`); SHA-1 is used only to derive request
  ids and message ids; the Jinja environment without autoescape is the one for one-line subjects (HTML templates are autoescaped);
  the `0.0.0.0` hits are a default for an off-by-default SFTP port and a list of "local" hosts.
- Secrets scan of tracked files (private keys, Stripe, AWS, Slack, GitHub, Google keys): only red-team test fixtures.
- Manual review of the new code: command signing and replay, the SMTP door for Keycloak, header injection in built mail, inbound mail
  handling, the OAuth connect flows, the sealed-secret paths, the records endpoints (SQL, tenant scope), the drafts route.

## Fixed

| # | Where | Finding | Fix |
|---|---|---|---|
| 1 | Mail directory (Kafka has no login) | Anyone able to write to `mail.directory` could publish their own box key, so passwords typed in the admin app would be sealed to them. | The directory's key is signed by the mail service; the server believes it only with the mail service's public key (`MAIL_SERVICE_PUBLIC_KEY`, required on a real server). A forged or swapped key is ignored. |
| 2 | Drafts route (`/api/drafts`) | Unauthenticated, and sealed with the same key as session cookies: a way for anyone to have the app seal JSON, and a cross-use risk. | Needs a signed-in session; drafts use a key of their own derived from the secret and the purpose, so a draft can't be a session or sign-in value. |
| 3 | Adding a mail server | An owner could point SMTP/IMAP at localhost, a private address or a cloud metadata address, and the mail service would connect from inside. | On a real server the host must resolve only to public addresses (loopback, private, link-local, shared 100.64/10 and IPv4-mapped forms are refused). |
| 4 | Support tickets by email | A ticket's number is guessable; from a mail server that records no sender checks, a spoofed From plus `[#123]` in the subject could write into someone's ticket. | Without an `Authentication-Results` record, only a message that answers one of ours (In-Reply-To or References) continues a ticket. A subject tag alone opens a separate ticket. |
| 5 | Email channel addresses | The tag after the plus sign had 32 random bits. | 64 random bits for new channels. |

## Checked and fine

- Header injection (CR/LF in subject, recipients, names, ids): refused by the mail builder.
- The Keycloak relay needs its login (an empty password refuses everyone), takes only the configured From, and listens only where configured.
- Commands to the mail service: Ed25519 signatures, 5-minute freshness, one-time ids; secrets arrive sealed and never come back.
- Records endpoints: parameters bound, attribute names checked against the template, `%` and `_` in a search are literal, every query scoped to the workspace.
- Web apps: no `innerHTML` use beyond static theme scripts; cookies are httpOnly, `SameSite=Lax`, `Secure` over https; CORS lists the apps' own origins.
- The production compose publishes only to 127.0.0.1; Kafka and the databases are not published.

## Left to the operator (not code)

- Kafka has no login by default. Turn on TLS and SASL with the ACL table in `mail/README.md` ("Kafka security") before the broker is reachable
  by anything but the services. Messages on `mail.inbound` and `mail.events` can still be forged by someone who can write to those topics.
- An email channel accepts mail from anyone who has its address unless an allowed-sender list is set; the sender is whatever the From header
  says. Set the allowed senders, and turn on "only accept mail that passes SPF or DKIM" for channels that matter.
- Keep `MAIL_MASTER_KEYS` apart from the database backup.
