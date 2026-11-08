---
server: minor
---

- Paying by card through Stripe: a new "card" payment method (BILLING_METHODS=...,card), offered only once STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET are set. The customer pays an invoice on Stripe's own Checkout page (card details never reach Syruss); an open page is reused rather than a second one made.
- Stripe's signed events (POST /api/v1/billing/webhooks/stripe) record the payment and pay the invoice. Unsigned or wrongly signed events are refused, each event is handled once, and a payment Stripe reports two ways is counted once.
- A customer can keep the card while paying, so invoices can be charged to it later without them; Stripe's billing portal changes or removes it, and the customer can stop using it at any time. The organization's activity shows invoices paid by card.
