---
www: minor
---

- Billing has two new pages: Invoices and Billing details.
- Invoices: every invoice issued to the organization, with its state (waiting, overdue, paid). Each invoice shows its lines, tax and amounts, downloads as a PDF (and its receipt once paid), and says how to pay what is still owed: Syruss's bank account with the invoice number as the reference and a form to send the slip (PDF or photo, the amount, the day and the bank reference), or by card on Stripe's page, optionally keeping the card for renewals. Slips sent show whether they are being checked, confirmed or not (with Syruss's reason), and one being checked can be withdrawn. Credit notes and payments received are listed with it.
- Billing details: the legal name, billing email, address, country, tax ID and purchase order number invoices are made out to, and the card kept for renewals (change it on Stripe, or stop using it).
- Buying a plan, an add-on or credits now issues an invoice and opens it; without billing details, it says so and links to them. Plan and usage warns when an invoice is waiting or overdue, or a renewal is past due, with a link to pay it. History links each payment to its invoice and says how it was paid.
- Files customers uploaded keep the API's protective headers through the app.
