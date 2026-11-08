---
server: minor
---

- Paid plans renew with an invoice: a week (renewal_lead_days) before the period ends, an invoice for the next period (the plan, or the plan it is moving to, and its add-ons) is issued, due when that period starts. One a period per organization; one that can't be issued because billing details are missing stays a draft for staff. A card the organization kept pays it straight away; if it is declined, the customer is told and pays by hand.
- Reminders before and after an open invoice's due date (reminder_days: 3 days before, on the day, 3 days after), once each, by email and in the app.
- A renewal not paid by its due date makes the subscription past due; after the grace period (grace_days) the organization moves to the Free plan (its data stays) and the invoice becomes uncollectible. Paying it late puts the plan and add-ons back.
- Moving up from one paid plan to a dearer one mid-period charges the new plan for the rest of the period, less the unused part of the current one; moving down is scheduled for the period's end and not invoiced.
- Credit notes correct an issued invoice by an amount, with their own gapless numbers (SYR-CN-2026-00001), a PDF and an email; they can refund the amount from a payment (through Stripe for a card payment, otherwise recorded as done by staff), and one that covers what is left settles an open invoice. Refunds made in Stripe's dashboard are recorded too. Credit notes and refunds can't be changed or removed.
- A CSV export of invoices, payments or credit notes between two days, for the accountants.
