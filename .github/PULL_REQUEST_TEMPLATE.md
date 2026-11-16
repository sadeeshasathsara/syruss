## What and why

<!-- What this changes, and the reason. Link the issue it closes: "Closes #123". -->

## How it was checked

<!-- Tests you ran or added, and anything you tried by hand. Say what you did not check. -->

## Checklist

- [ ] The pull request is into `dev` and does one thing
- [ ] Tests added or updated, and they pass (`server`, `mail`, `agentic_framework` as touched)
- [ ] `tsc` and lint pass for any web app changed (`www`, `admin`, `site`)
- [ ] A changeset names every service changed (`python scripts/changeset.py write ...`), unless this is docs only
- [ ] The shared sign-in code is still identical in `site/`, `www/` and `admin/` (`npm run check:auth-sync` in `site/`)
- [ ] No secrets, `.env` files, or real customer data in the code, tests or screenshots
- [ ] Anything that handles secrets, access or untrusted input has a test for the failure case
- [ ] Docs updated where behaviour or setup changed
