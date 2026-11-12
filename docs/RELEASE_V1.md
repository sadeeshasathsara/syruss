# Releasing v1.0.0

What is left before the first stable release, in the order it has to happen. Tick items off in the pull requests that
do them. The release process itself is in `BRANCHING_AND_RELEASES.md`; this page is only the one-time path to 1.0.0.

Each service has its own version, so "v1.0.0" means tagging every service that goes out `<service>-v1.0.0`; there is
no single repository version.

## Where things stand (2026-11-09)

| Service | `dev` | `test` | `main` |
|---|---|---|---|
| server | 0.12.2-dev.1 | 0.8.0-rc.1 | 0.2.0 |
| www | 0.8.2-dev.1 | 0.6.2-rc.1 | 0.2.0 |
| agent | 0.6.0-dev.1 | 0.6.0-rc.1 | 0.2.0 |
| admin | 0.5.1-dev.1 | 0.3.0-rc.1 | never released |
| site | 0.2.3-dev.1 | 0.2.2-rc.1 | 0.2.0 |

`test` was last promoted on 2026-11-06 and `main` was last released on 2026-10-31. `main` still runs the old release
workflow and `CHANGELOG.md`; the `_changelog/` files and the current `package.yml` have never run there.
`syruss changeset table` prints the live versions.

## 1. Decide what 1.0 is

- [ ] Which services go to 1.0.0 (all five, or leave `site` and `admin` on 0.x).
- [x] The flows that must work end to end before it ships: the list below. QA on `test` and the check on production both
      follow it; a flow passes when every step does what it says, with nothing unexpected in the server log.

### The 1.0 flows

1. **Sign up and in.** Create an account with email and password, confirm the email with its code, fill in the
   profile, create an organization. Sign out, sign in again from another browser: it asks for the emailed code first.
   Google sign-in, where it is configured.
2. **A workspace and its template.** Create a workspace, upload 2 or 3 sample documents, let the agent propose a
   template, change a field, publish it.
3. **Reading documents.** Upload a picture, a multi-page PDF and a TIFF. Each is read into a record; the PDF's and
   TIFF's pages can all be paged through in the document view; each document's page count is what billing charged.
4. **Review.** A document the agent is unsure of goes to Review; correct a value and approve it; the record shows the
   correction and the agent's chat about it.
5. **Rules and analytics.** Add a rule and see a record fail it; build a screen with a calculated column and export it
   to CSV.
6. **Receiving.** Send a document through the API with an intake key (and with a key whose address allowlist excludes
   you: refused), by e-mail to a channel address, and by SFTP.
7. **Publishing.** Connect a webhook destination, approve a record, see the signed delivery arrive; pause and replay.
8. **People.** Invite someone to the organization and to a workspace with a role; they accept; change their role;
   remove them. A member can't change someone whose role outranks theirs.
9. **Billing.** As the owner, fill in billing details, buy a credit pack: an invoice arrives by email with its PDF.
   Pay one invoice by bank transfer (send the slip; approve it in the admin app) and one by card (Stripe test mode). The
   credits appear and the receipt is ready. In the admin app, the money summary moves accordingly.
10. **The admin app.** Sign in with the admin client; see the overview, a run, a document's process (open its content
    with a reason); suspend and reactivate a test organization; disable and enable a test account.
11. **Leaving.** Delete a workspace (its files leave storage). Close a test organization (it stops opening). Delete a
    test account.
12. **Backups.** On the server: `deploy/prod/backup.sh`, then `restore.sh` of that backup on a spare machine, and flow 3's
    documents open there.

## 2. Get `dev` ready

- [ ] Billing settles on `dev`: invoices, bank slips, card payments, renewals, credit notes (migrations 0025 to 0030). It
      has not reached `test` yet.
- [x] The server's pytest suite passes (on `feat/production-readiness`, 2026-11-12). Still to check: the migrations on a copy
      of the `test` database (`alembic upgrade head`), at the first promotion.
- [x] The `www` lint errors are fixed (and `site`'s), and CI now runs eslint for `www`, `site` and `admin`: an error
      fails the check.
- [x] The destination preview type is `DeliveryPreview`; `MappingPreview` is the knowledge source's alone.

## 3. Bump to 1.0.0 on `dev`

- [ ] One pull request into `dev` with one changeset that gives each chosen service `major`:

  ```bash
  syruss changeset write -b server=major -b www=major -b agent=major -b admin=major -b site=major -m "First stable release"
  ```

  `scripts/changeset.py` takes the highest version tag and adds one to the major, so every 0.x service becomes
  `1.0.0-dev.1`.
- [ ] Merge it last. A promotion takes its version from the newest entry in the source file, so a patch merged after it
      would make the release 1.0.1. Fixes found later go through the rc cycle in step 4.

## 4. Release candidate on `test`

- [ ] Promote `dev` to `test` (pull request title: `chore(release): promote dev to test (v1.0.0-rc.1)`). The release
      job gives each service `1.0.0-rc.1` and the deploy hook (`deploy/test/hook.py`) updates the test server.
- [ ] QA on the test server, following the flows from step 1. At least: sign up and sign in, create a workspace and its
      template, upload documents and let the agent read them, review and correct, rules, analytics screens, receiving
      (API, e-mail, SFTP), publishing to a destination, billing in Stripe test mode, and the admin app.
- [ ] Each fix goes into `dev` as a `patch` changeset and is promoted again (`1.0.1-rc.N`), or, to keep 1.0.0, is
      released as 1.0.1 after it.

## 5. Production setup

`deploy/prod/` has the compose file, Caddyfile, deploy hook, backup and restore, and its README is the runbook.

- [x] The files for a production server and its deploy: `deploy/prod/` and the `deploy-prod` job in `package.yml`
      (it deploys only once `PROD_DEPLOY_ENABLED` is set and the `production` environment exists).
- [ ] The production server itself, set up by following `deploy/prod/README.md`.
- [ ] Production secrets:
  - `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, using live keys and a webhook pointing at production
  - the model provider: `LLM_PROVIDER`, `GEMINI_API_KEY`, `GEMINI_MODEL`, `CHAT_MODEL`, on a paid tier (the free tier allows 15 requests a minute)
  - `MODEL_PRICES`, so runs show what they cost
  - `SMTP_*` for e-mail
- [ ] Keycloak realm for production, hardened with `bridge/keycloak/harden.py`.
- [ ] Syruss's own billing details filled in (invoices cannot be issued without them).
- [x] Backup and restore scripts (`deploy/prod/backup.sh`, `restore.sh`), both tried against a local copy.
- [ ] On the production server: the nightly backup in cron, copies off the machine, and one restore tried there.
- [ ] Domain and TLS.

## 6. Release to `main`

- [ ] Branch protection on `main`, `test` and `dev`. `BRANCHING_AND_RELEASES.md` says they are protected, but GitHub
      reports none of them is.
- [ ] Promote `test` to `main` (pull request title: `chore(release): release v1.0.0`). This is the first run of the
      current release job on `main`. Expect it to:
  - create `_changelog/main.md` with every entry `test` has and `main` lacks, under `1.0.0`
  - tag `<service>-v1.0.0` and create a GitHub Release for each service
  - push the images as `:latest` and `:v1.0.0` to ghcr.io
- [ ] Check it: `syruss changeset table --strict` shows 1.0.0 in `main` and the tags agree.
- [ ] Decide what happens to the old `CHANGELOG.md` on `main` (keep it as history, or remove it).
- [ ] Deploy production from the `:v1.0.0` images and run the step 1 flows once more there.
