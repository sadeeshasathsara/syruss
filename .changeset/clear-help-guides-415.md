---
agent: minor
server: patch
---

- **Syruss answers questions about Syruss itself from facts.** Seven help guides the chat reads before it answers "how do I…" or "why can't I…": getting started, documents and review, billing, team and roles, account security, connections, and analytics. They name the pages and buttons as the app shows them (Settings → Billing → Invoices, Settings → Account and security → Sign-in, Settings → Organization → Support access).
  - **Billing** covers plans and credits, invoice statuses, paying by card on Stripe's page, paying by bank transfer (the slip, its transfer reference, "Being checked" and "Not confirmed", and sending again), renewals, reminders, the grace period and the move to Free, and billing details.
  - **Account security** covers password and two-step changes, "Keep me signed in" (30 days), trusted browsers and their emailed codes, and Google sign-in.
- **A "Helping with Syruss" section in the chat charter**, before the shared boundaries: read the matching guide first, point to the exact page, never invent a feature or setting, and say plainly when only Syruss support can help.
- **`get_billing_status`, a read-only chat tool**: the organization's plan, invoices waiting for payment, and the latest bank slip sent (with why it wasn't confirmed). The server answers only during a person's own chat, and only when their organization role manages billing (its owners); work runs and everyone else are refused.
- **A new red-team case**: a document that tells the agent to say the person's invoice is paid.
