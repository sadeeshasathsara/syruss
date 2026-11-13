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
   - `server.env`, `agent.env`, `www.env`, `site.env`, `admin.env`: start from each service's `.env.example`. The server
     refuses to start in production without https addresses, its own signing keys and `SECRETS_ENCRYPTION_KEY`.
   - Live Stripe keys and the webhook secret (webhook URL `https://api.<domain>/api/v1/billing/webhooks/stripe`), the
     model provider on a paid tier, `MODEL_PRICES`, and `SMTP_*` from a mail provider with SPF, DKIM and DMARC set up.
3. `echo 'SYRUSS_DOMAIN=<domain>' | sudo tee -a /etc/default/caddy`, copy the `Caddyfile` to `/etc/caddy/`, reload Caddy.
4. `docker compose --env-file env/stack.env pull && docker compose --env-file env/stack.env up -d`.
5. Harden the Keycloak realm on the server itself (Caddy keeps `/admin` closed from outside):
   `KEYCLOAK_URL=http://127.0.0.1:8080 KEYCLOAK_ADMIN=... KEYCLOAK_ADMIN_PASSWORD=... python3 harden.py` (from `bridge/keycloak`).
6. In the admin app, fill in Syruss's billing details, a bank account and the tax rates. Invoices can't be issued before.
7. The deploy hook: run `hook.py` as a systemd service with `HOOK_SECRET` (a long random value) and
   `DEPLOY_DIR=~/syruss-deploy`. In GitHub, set the `PROD_DEPLOY_URL` (`https://deploy.<domain>/deploy`) and
   `PROD_DEPLOY_SECRET` secrets, create the `production` environment (add yourself as a required reviewer), and set the
   repository variable `PROD_DEPLOY_ENABLED=true`. Until then a release on `main` publishes images and deploys nothing.

## Backups

`backup.sh` writes `syruss.pgdump`, `keycloak.pgdump` and `files.tar.gz` with checksums into `~/syruss-backups/<time>/`
and keeps 14 days (`KEEP_DAYS`). Run it nightly from cron (see its header) and copy the folder off the machine. Try
`restore.sh` on a spare machine once, and again after big changes: a backup nobody has restored is a hope, not a backup.
