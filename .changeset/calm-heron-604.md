---
server: minor
admin: minor
www: minor
site: patch
---

- The system admin console: business figures (recurring revenue, paying and active organizations, churn) with 30-day trends, customers worth a look, one page per organization and per person, staff notes, platform controls (maintenance, kill switches for sign-ups, the agent and intake, announcements shown in the app), one search box, and a security view.
- Staff join by an emailed invitation: anyone can be invited, no account needed. Role changes, removals and withdrawn invitations are emailed too.
- Staff accounts are kept apart from customer accounts: they only open the admin app, can't create or join an organization, and organizations can't invite them. Accepting a staff invitation with an address that has a customer account first lists what it deletes (organizations it alone owns are closed, other memberships removed, the customer profile cleared) and needs a confirmation.
- Signing in to the site or the main app with a staff account goes to the admin dashboard, whatever address it was going to.
- The first platform owner is invited from the server: python -m app.invite_staff you@company.com. Platform lessons for the agent are approved in the admin app.
