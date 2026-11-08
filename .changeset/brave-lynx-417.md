---
server: minor
---

- Every purchase is now an invoice from Syruss to the organization. Buying a plan, an add-on or credits issues one at once, and what it sells is switched on only once it is paid.
- Invoices are numbered with no gaps (SYR-2026-00042), carry both parties' details and tax IDs, and are emailed with their PDF. Once issued, the database refuses to change what an invoice says: a mistake is voided.
- Owners keep the organization's billing details (legal name, billing email, address, country, tax ID, purchase order) at /billing/profile, list their invoices and download each PDF, and the receipt once paid.
- Platform staff keep Syruss's own billing details (seller, numbering prefix, payment terms), the bank accounts customers pay into and tax rates by the buyer's country (with reverse charge for buyers with a tax ID).
- Staff draft invoices for custom deals, issue, void or throw away drafts, record payments against an invoice (partial payments leave it open with the balance due), and switch an invoice on before it is paid for an organization they trust. Every action needs a reason and a fresh sign-in, and shows in the organization's activity.
- Payment methods are adapters (BILLING_METHODS: bank_transfer and staff_recorded by default). They replace the earlier payment providers and billing requests; open requests become draft invoices.
- Moving from a free plan to a paid one starts a whole new period once it is paid. Moving to a free plan isn't invoiced: it happens when the current period ends.
- Billing alert emails now link to the main app (PUBLIC_APP_URL) instead of the public site.
- A limit's refusal reads "The Free plan allows 1 intake channel", not "1 intake channels".
