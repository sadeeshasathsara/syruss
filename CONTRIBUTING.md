# Contributing to Syruss

Thank you for helping. This page is the short version; `docs/BRANCHING_AND_RELEASES.md` has the full branching and release rules and
`DEVELOPER_SETUP.md` has the setup.

By taking part you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md). Security problems go to [SECURITY.md](SECURITY.md), not to
the issue tracker.

## Ways to help

- **Report a bug or suggest an improvement:** open an issue with the template that fits. Say what you did, what you expected, and what
  happened. Small, specific issues are easiest to act on.
- **Fix something or add something:** for anything bigger than a small fix, open an issue first so we can agree on the approach before you
  spend time on it.
- **Improve the docs:** corrections and clearer wording are welcome.

## Getting set up

See `DEVELOPER_SETUP.md`. In short: start the backing services (`bridge/docker-compose.yml`: Postgres, MinIO, Kafka, Mailpit, Keycloak),
install the dependencies, and run `syruss` to start everything. Never commit secrets or `.env` files; each service has a `.env.example`.

## The workflow

1. Branch from `dev`: `feat/<short-name>` for a feature, `fix/<short-name>` for a fix. Work is merged into `dev` only (never straight into
   `test` or `main`: they are promoted from `dev`).
2. Keep a pull request to one thing. Write the commit messages as
   [Conventional Commits](https://www.conventionalcommits.org/): `feat(www): ...`, `fix(server): ...`, `docs: ...`.
3. **Add a changeset** when you change what a service does (not for docs only):
   ```bash
   python scripts/changeset.py write -b server=minor -m "What changed, in words a user would use"
   ```
   Name every service you changed. CI checks this (`python scripts/changeset.py check`).
4. **Test what you changed** (see below), and add a test for a bug you fix or a behaviour you add.
5. Open the pull request into `dev` with the template filled in. CI must pass and a maintainer reviews it.

## Tests and checks

```bash
cd server && python -m pytest             # the API (uses its own test database)
python -m pytest mail/tests               # the mail service (its own test database)
cd agentic_framework && python -m pytest  # the agent
cd www && npx tsc --noEmit && npm run lint        # the customer app (same for admin/ and site/)
cd site && npm run check:auth-sync                # the shared sign-in code must be identical in site/, www/ and admin/
```

The sign-in code in `site/src/lib/auth/`, `www/src/lib/auth/` and `admin/src/lib/auth/` is deliberately the same file in three places:
change all three together.

## Style

- Follow what is already there: names, structure and the way comments explain *why*.
- Keep user-facing text short, plain and free of internal terms.
- Don't add a dependency for something small; if you must, say why in the pull request.
- Anything that handles secrets, access control or untrusted input (documents, email, uploads) needs a test for the failure case, not
  only the success case.

## Licensing

Syruss is licensed under the **GNU General Public License v3** (see [LICENSE](LICENSE)). By contributing you agree that your contribution
is licensed the same way. Don't submit code you don't have the right to share under it, or code copied from a project with an
incompatible license.
