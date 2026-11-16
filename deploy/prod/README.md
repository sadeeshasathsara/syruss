# Production

The production server runs the same images as test, tagged `latest` (built by CI when `main` is released). It only pulls:
nothing is built on it. The folder holds everything the server needs besides its secrets.

| File | What it is |
|---|---|
| `docker-compose.yml` | The stack. Sized for a 4 GB machine; raise the `mem_limit`s with the machine. |
| `Caddyfile` | TLS and routing, one subdomain per service. Keycloak's admin console is not reachable from outside. |
| `hook.py` | The deploy hook CI calls after a release on `main` (the same program as test's). |
| `backup.sh` / `restore.sh` | Both databases and every stored file, into dated folders; and back. |

## First time

1. A machine (Ubuntu LTS, 4 GB or more), Docker, and Caddy installed as a system service. Point DNS for the domain and
   `app.`, `admin.`, `api.`, `auth.`, `files.` and `deploy.` at it.
2. Copy this folder to `~/syruss-deploy`. Create `env/` beside it, readable only by you (`chmod 700 env`):
   - `stack.env`: `POSTGRES_*`, `MINIO_ROOT_*`, `KAFKA_CLUSTER_ID`, `KEYCLOAK_*`, `KC_HOSTNAME`, `SYRUSS_*_URL`, the three
     client secrets. Use long random values (`openssl rand -base64 32`), never the development ones.
   - `server.env`, `agent.env`, `mail.env`, `www.env`, `site.env`, `admin.env`: start from each service's `.env.example`.
     `mail.env` holds no mail password or server: mailboxes are connected with `python -m mail connect` and then in the admin app. It does hold
     `MAIL_MASTER_KEYS` (`python -m mail keygen`; keep it apart from the database), the relay password Keycloak signs in with
     (`MAIL_RELAY_PASSWORD`), the Google OAuth client (set in the admin app's Provider apps tab; redirect URIs `https://admin.<domain>/email/google` and `/email/microsoft`)
     and `MAIL_TRUSTED_SERVER_KEY`; the server gets the mail service's directory key as `MAIL_SERVICE_PUBLIC_KEY` (`python -m mail signer`, once the mail service has started). The server and the mail service talk only over Kafka: make the pair of signing keys once with
     `python -m app.mail_signing_key` (the first line goes in `server.env`, the second in `mail.env`). Connect mailboxes afterwards in
     the admin app's Email page (or `python -m mail connect` first, on the server, to get the first emails out). The
     compose file sets `ENV=production`, and then the server and the agent refuse to start while a development value is
     left, and say which: the database, storage and token passwords; `SECRETS_ENCRYPTION_KEY`; the public addresses
     (`OIDC_ISSUER`, `PUBLIC_SITE_URL`, `PUBLIC_APP_URL`, `PUBLIC_ADMIN_URL`, `PUBLIC_API_URL`) and `CORS_ORIGINS`, which
     must be https and not localhost; the signing keys (`AGENT_TOKEN_PRIVATE_KEY` for the
     server, `AGENT_TOKEN_PUBLIC_KEY` for the agent). The test server (`ENV=test`) checks the same.
   - Live Stripe keys and the webhook secret (webhook URL `https://api.<domain>/api/v1/billing/webhooks/stripe`), the
     model provider on a paid tier, `MODEL_PRICES`, and the mail service's `MAIL_FROM` (a real domain; in `mail.env`) with a mailbox from a mail provider that has SPF, DKIM and DMARC set up. Turn on Kafka TLS and SASL (mail/README.md, "Kafka security").
   - The support mailbox: `SUPPORT_EMAIL_ADDRESS` in `mail.env` (the server asks the mail service for it), and the mailbox itself connected in the admin app's
     Email page for Support (Google, or any server with IMAP): the mail service sends ticket answers from it and reads
     customers' replies.
3. `echo 'SYRUSS_DOMAIN=<domain>' | sudo tee -a /etc/default/caddy`, copy the `Caddyfile` to `/etc/caddy/`, reload Caddy.
4. `docker compose --env-file env/stack.env pull && docker compose --env-file env/stack.env up -d`.
5. Harden the Keycloak realm on the server itself (Caddy keeps `/admin` closed from outside):
   `KEYCLOAK_URL=http://127.0.0.1:8080 KEYCLOAK_ADMIN=... KEYCLOAK_ADMIN_PASSWORD=... python3 harden.py` (from `bridge/keycloak`).
   Run it again after upgrading: it also makes staff sign-ins report their one-time code, which the server requires.
6. Point Keycloak's email at the mail service (it emails new staff their set-up link): `KEYCLOAK_SMTP_HOST=mail
   MAIL_RELAY_PASSWORD=... MAIL_FROM=... python3 harden.py --smtp` as in step 5, with the same `MAIL_RELAY_PASSWORD` and
   `MAIL_FROM` as `mail.env`. Then add the first platform owner, with the Keycloak admin login for this one command:
   `docker compose --env-file env/stack.env exec -e KEYCLOAK_URL=http://keycloak:8080 -e KEYCLOAK_ADMIN=... -e KEYCLOAK_ADMIN_PASSWORD=... server python -m app.invite_staff you@company.com`.
   It makes their sign-in (Keycloak emails a link to choose a password and set up the one-time code) and emails the
   invitation (its link is printed too). Use an address with no Syruss customer account: a staff account only opens the
   admin app. Everyone on the staff is added this way (add `support`, `billing`, ... after the address for another role):
   the admin app adds nobody, and its sign-in page creates no accounts.
7. In the admin app, fill in Syruss's billing details, a bank account and the tax rates. Invoices can't be issued before.
8. The deploy hook: run `hook.py` as a systemd service with `HOOK_SECRET` (a long random value) and
   `DEPLOY_DIR=~/syruss-deploy`. In GitHub, set the `PROD_DEPLOY_URL` (`https://deploy.<domain>/deploy`) and
   `PROD_DEPLOY_SECRET` secrets, create the `production` environment (add yourself as a required reviewer), and set the
   repository variable `PROD_DEPLOY_ENABLED=true`. Until then a release on `main` publishes images and deploys nothing.

## How the containers are locked down

`docker-compose.yml` runs every container without Linux capabilities (the Postgres images and MinIO get back only what
they need to switch to their own user), with `no-new-privileges`, and with a process limit. Syruss's own services run as
an unprivileged user on a read-only filesystem, with scratch space in memory. The networks keep the web apps away from
the databases, and the databases, Kafka and object storage off the internet: `data` and `auth` are internal networks.
Base images are pinned by digest (Dependabot proposes updates), and `.github/workflows/container-scan.yml` scans the
images every week. Raise `pids_limit` or a `tmpfs` size here if a service needs more.

## Backups

`backup.sh` writes `syruss.pgdump`, `keycloak.pgdump` and `files.tar.gz` with checksums into `~/syruss-backups/<time>/`
and keeps 14 days (`KEEP_DAYS`). Run it nightly from cron (see its header) and copy the folder off the machine. Try
`restore.sh` on a spare machine once, and again after big changes: a backup nobody has restored is a hope, not a backup.
