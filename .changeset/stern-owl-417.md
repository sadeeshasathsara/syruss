---
server: minor
admin: minor
www: minor
---

- Staff sign-ins must include a one-time code: the server checks the token's amr claim, and `bridge/keycloak/harden.py` makes Keycloak report it. Staff sessions last at most 8 hours, and the admin app signs out after 30 minutes without use, with a warning first.
- A second owner's approval for risky actions, which owners turn on and scope in Platform controls. It covers suspending organizations, disabling accounts, adding or removing owners, credit notes, refunds and voids, and prepaid pages, plan changes and early switch-on. A held action waits on the new Approvals page and is carried out as the person who asked once another owner approves. While the rule is on, turning it off needs approval too. The server can turn it off with `python -m app.approvals off`.
- Customers control support access. Owners and admins are emailed and told in the app when Syruss support opens their content, and everyone in the organization sees a banner while it's open. Owners and admins can end it at any time, or stop support opening content at all, under Settings > Organization > Support access.
- Unused staff access is taken away: after 60 days without using the admin app, with a warning 7 days before. Owners are reminded to confirm who still needs access every 90 days. Both are adjustable on the Staff page.
