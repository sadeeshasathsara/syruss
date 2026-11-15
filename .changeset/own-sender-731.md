---
mail: minor
server: minor
---

- **The mail service owns who mail is from; the server's environment no longer has those settings.**
  - **Moved to `mail.env`:** `MAIL_FROM_NAME`, `MAIL_REPLY_TO`, `SUPPORT_EMAIL_ADDRESS` and `SUPPORT_FROM_NAME` (`MAIL_FROM` was already there). The server no longer reads any of the five; old lines in `server.env` are ignored.
  - **How the server gets what it needs:** it asks the mail service for the From address and the support address (`GET /identity`) when it starts and every few minutes, and keeps the last answer. Mail jobs no longer carry a sender name or reply address of the server's choosing: the mail service fills in its own (support mail goes out as the support name and is answered at the support address).
  - **If the mail service can't be reached** before it has ever answered, a ticket email isn't sent and mail from the support mailbox waits to be taken in, rather than using a development address. After the first answer, the last one stands.
  - **Deploying:** put `SUPPORT_EMAIL_ADDRESS` (and `MAIL_REPLY_TO`, `MAIL_FROM_NAME`, `SUPPORT_FROM_NAME` if you changed them) in `mail.env` before the server is updated. The "`MAIL_FROM` at a real domain" start-up check is the mail service's now.
