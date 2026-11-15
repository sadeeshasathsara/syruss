---
admin: minor
server: minor
---

- **The admin app's Email section is organised around jobs, and its mailbox passwords are sealed in your browser.**
  - **Jobs** (`Email › Jobs`): the built-in jobs (notifications, help, document inbound, promotions, and Keycloak's account emails) each with the mailbox it uses (a mailbox that can't do the job isn't offered for it), when it last sent and read, its last error, and settings of its own (sender name, where replies go, how often it reads). **Add a job** of your own; remove it again when you no longer need it.
  - **Accounts** (`Email › Accounts`): the connected mailboxes, with Google, or any mail server. Pick which jobs a new mailbox serves as you add it.
  - **Delivery log** is unchanged.
  - **Passwords are sealed in the browser** to the mail service's key before they are sent, so neither this app's server nor Kafka ever holds one. The key's fingerprint is shown beside the form; it should match `python -m mail fingerprint` on the mail server. A browser without the needed support (older than Chrome 133, Firefox 130 or Safari 17) still works: the server seals the password instead, and the page says so.
  - **Live:** the Email pages are told the moment the mail service's state changes (a mailbox connected, a job's mailbox chosen, a problem found), so they are always what the mail service says. The new `mail` scope of the staff live stream carries it, owners only; it names what changed, never its content. A "Live" badge shows when it is connected.
  - **Audit log:** the mail actions (connecting or removing a mailbox, choosing a job's mailbox, adding, changing and removing jobs) read in plain words.
