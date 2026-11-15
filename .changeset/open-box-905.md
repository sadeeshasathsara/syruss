---
mail: minor
server: minor
admin: minor
---

- **Mail accounts are set up in the admin app**, on a new **Email** page (owners only).
  - **Google mail:** "Set up Google mail" signs in to Google and asks only for what the chosen uses need. Sending needs `gmail.send`; Support also needs `gmail.readonly`. Syruss can't delete or change anything in the mailbox.
    - A Workspace account with an Internal OAuth app needs no review from Google.
    - A personal Gmail works while the app is in Testing, but Google ends its sign-in every 7 days. The account then shows "Needs reconnecting", and mail uses the fallback account until someone reconnects it.
  - **Any other mail server:** add a mailbox with SMTP to send and IMAP to read (read-only).
  - **Choose which account does what:** notifications, sign-in emails (Keycloak) or support. A choice the account can't serve is refused when it's made.
  - **Test** an account, and **remove** one (a Google sign-in is revoked).
  - **The delivery log:** what went to whom and how it went. Mail that was given up can be sent again.
  - Every change is in the platform audit log.
- **The mail service reads the support mailbox**, whichever account is connected for Support, and hands each new message to the server, which turns it into a ticket reply.
  - It starts from when it was switched on, and nothing is read twice.
  - The server's own IMAP polling and `SUPPORT_IMAP_*` are gone. The fallback account can read too (`MAIL_IMAP_*` in `mail.env`).
- **The server reaches the mail service's internal API** with `MAIL_SERVICE_URL` and `MAIL_SERVICE_TOKEN`, the same token as in `mail.env`. The test server and production refuse to start without a long one.
- **Support settings** now say whether the support mailbox is read, and link to the Email page.
