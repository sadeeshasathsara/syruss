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
- [ ] The flows that must work end to end before it ships, written down here. Nothing in the repository defines them yet.

## 2. Get `dev` ready

- [ ] Billing settles on `dev`: invoices, bank slips, card payments, renewals, credit notes (migrations 0025 to 0030). It
      has not reached `test` yet.
- [ ] The server's pytest suite passes, and the migrations run both on an empty database and on a copy of the `test`
      database (`alembic upgrade head`).
- [ ] Decide on the `www` lint errors: 5 `react-hooks/set-state-in-effect` errors and 7 warnings on `dev`. CI runs
      `tsc` and the build but not lint, so nothing blocks them today.
- [ ] Optional: `MappingPreview` in `www/src/lib/types/core.ts` is two interfaces (knowledge source preview and
      destination preview) that merge into one type; give them separate names.

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

None of this is in the repository yet; `deploy/` only has the test server.

- [ ] A production server and its deploy: a `deploy/prod/` (compose file, Caddyfile, deploy hook) and a deploy job in
      `package.yml` for `main`, like `deploy-test`.
- [ ] Production secrets:
  - `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, using live keys and a webhook pointing at production
  - the model provider: `LLM_PROVIDER`, `GEMINI_API_KEY`, `GEMINI_MODEL`, `CHAT_MODEL`, on a paid tier (the free tier allows 15 requests a minute)
  - `MODEL_PRICES`, so runs show what they cost
  - `SMTP_*` for e-mail
- [ ] Keycloak realm for production, hardened with `bridge/keycloak/harden.py`.
- [ ] Syruss's own billing details filled in (invoices cannot be issued without them).
- [ ] Backups of Postgres and MinIO, with one restore tried. Nothing in the repository sets these up yet.
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
