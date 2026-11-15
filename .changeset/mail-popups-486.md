---
mail: patch
server: patch
admin: minor
---

- **Add a job** and **Connect a mailbox** are now popups, opened by a button at the top right of the Email page. A mailbox is connected for one job chosen from a dropdown (other jobs can be pointed at it from the Jobs tab); "another mail server" is a step inside the same popup.
- **A Google or Microsoft app's client id and secret are never sent to the browser.** The Provider apps tab no longer shows the id, the API no longer returns it, and the directory no longer carries it. Leave the fields empty to keep what is set.
- **A problem on a job shows only while it is still true.** An old failure through a mailbox that has since been replaced, or one a later message got past, no longer shows as "Problem".
- **The mail service removes entries it published earlier and that no longer exist** (such as the old environment mailbox) from its directory when it starts.
- **Microsoft can be set in the environment like Google:** `MICROSOFT_MAIL_CLIENT_ID`, `MICROSOFT_MAIL_CLIENT_SECRET` and `MICROSOFT_MAIL_TENANT` in `mail.env` (the admin app's Provider apps tab still works and takes precedence).
