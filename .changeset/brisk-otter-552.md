---
server: minor
www: minor
admin: minor
agent: minor
---

- PDF documents are accepted everywhere a picture is (the app, the API, e-mail, SFTP). Pages are counted from the file (a PDF's pages, a TIFF's frames) and each one is charged; the agent and people in the app can look at every page. A file with more than INTAKE_MAX_PAGES pages (100) or a password-protected PDF is refused.
- One picture that shows several sheets is counted as that many pages when the agent reports it: the extra pages are charged, capped by PICTURE_MAX_PAGES (10), written to the audit log with what was seen, and refused when they don't fit the plan.
- Card payments: Stripe is called off the server's main thread with retries and idempotency keys; a disputed payment reaches Syruss's billing address and a lost dispute is recorded as money back; a bank debit that bounces tells the customer; a declined renewal card is tried again when each reminder comes due.
- Billing in the admin app shows where the money stands for each currency: owed, overdue, received this month and last, invoiced, and what recurs monthly.
- A deleted workspace's files leave storage with it. An organization its owner closed is emptied CLOSED_ORG_RETENTION_DAYS (30) later; invoices and the activity log are kept. People can delete their own account (Sign-in settings).
- Platform staff get an Accounts page: find organizations and people, suspend or reactivate an organization, disable or enable an account, each with a reason in the audit log.
