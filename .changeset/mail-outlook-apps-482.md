---
mail: minor
server: minor
admin: minor
---

- **Outlook and Microsoft 365 mailboxes can be connected with a button.** Email › Accounts has **Connect with Microsoft** beside Google: you sign in, approve only sending and, where a job reads, reading, and no app password is involved. The mail service sends through Microsoft Graph, reads new mail by delta query, and keeps the new refresh token Microsoft hands out after each refresh.
- **Google and Microsoft apps are set in the admin app.** The new **Email › Provider apps** tab takes each app's client id (and Microsoft's tenant) and its secret, which is sealed in the browser to the mail service's key and never shown again. The redirect address to register is shown there. Google's environment settings still work until an app is saved.
- **The Jobs list is cleaner:** one aligned table of job, mailbox and a one-word status, with the settings behind a row's arrow.
- **Audit log:** connecting a Microsoft mailbox and setting or removing a provider app read in plain words.
