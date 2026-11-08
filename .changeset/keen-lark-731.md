---
admin: minor
---

- Billing has new tabs: Invoices, Payments to check and Settings; the Requests tab is gone (purchases are invoices now).
- Payments to check: each slip a customer sent, shown beside what its invoice asks for, with warnings (an amount that isn't what is due, a slip or bank reference sent before). Approve it for what actually arrived (more than is due asks to confirm) or reject it with a reason the customer sees. A count shows on the tab.
- Invoices: filter by state (waiting, overdue, paid, drafts, not paid, void), find by number, download CSVs for the accountants, and make an invoice for a custom deal from catalog items or written lines, as a draft or issued at once.
- Each invoice has its own page: lines and tax, both parties, payments with their files, slips sent, credit notes, the PDF and receipt, and the changes staff make: issue or throw away a draft, record a payment, switch it on before it's paid, void it, issue a credit note (optionally refunding), attach the receipt issued for a cash payment.
- Settings: Syruss's billing details (shown as not ready until complete), numbering prefix, payment terms, renewal timetable, the bank accounts customers pay into and tax rates by country.
- An organization's page shows its billing details, its invoices and whether a renewal is past due; "Record a payment" became "New invoice".
- Files customers uploaded keep the API's protective headers through the admin app (never read as anything else, unable to run anything).
