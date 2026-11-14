---
www: minor
site: patch
admin: patch
server: patch
---

- **Sign-in settings are managed inside Syruss.** Settings → Account and security shows:
  - whether you have a password and when it last changed;
  - your authenticator apps, and whether two-step verification is on;
  - whether Google is linked.

  Changing the password, setting up two-step verification or removing an authenticator opens one Syruss-styled sign-in step (a Keycloak Application Initiated Action) that comes straight back with a confirmation. Keycloak's own account pages are turned off (`harden.py`).
- **Every button on the sign-in pages shows that it's working** ("Signing in…", "Creating your account…") and can't be pressed twice.
- **Clearer bank transfer slip form:** a drop area with a "Choose files" button, the chosen files listed and removable, and the "Transfer reference" explained.
- **A card payment Stripe finished while its webhook never arrived is no longer lost.** Paying again, or opening the invoice, checks the open checkout with Stripe and records the payment, instead of reopening Stripe's finished page. An expired checkout is replaced by a new one.
