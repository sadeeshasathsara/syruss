# Help: billing, plans, invoices and paying

Billing belongs to the organization, not to a workspace: one plan is shared by all its workspaces. Use the page and
button names exactly as written here.

**Who can see it.** Only people whose organization role can "Manage the plan and billing": the organization's owners.
Admins and members can't open Settings → Billing; they see that their role doesn't allow it. Someone who isn't an owner
should ask an owner. When you can call get_billing_status, it answers only for such a person; if it is refused, say
that their role can't see billing, not that something is broken.

## Where billing is
Organization Settings → **Billing**:
- **Plan and usage**: the current plan, this period's dates (the plan's pages renew at the end of each period), the
  pages read this period against the plan's allowance, prepaid credits, pages by workspace, and what the plan allows.
  A banner shows invoices waiting for payment, with a **Pay it** link.
- **Plans and credits**: change the plan, buy add-ons, buy prepaid pages.
- **Invoices**: every invoice issued to the organization; open one to pay it and download its PDF or receipt.
- **Billing details**: who invoices are made out to, and the card kept for renewals.
- **History**: payments received and every change to the prepaid credits.
In a workspace, Settings → General → **Plan and billing** shows the shared plan and what that workspace has used.

## Plans, pages and credits
- A plan sets the pages read each period for the whole organization, and per-workspace limits such as members a
  workspace. **Add-ons** add more each month, on top of the plan, and several stack.
- **Prepaid credits** are pages bought in packs. They are used once the plan's pages for the period run out, and they
  don't expire.
- When the plan's pages are used and there are no credits left, new documents are refused until the pages renew or
  credits are added. Owners are told by email and in the app at 80% and 100% of the pages, and when credits run low or
  out.
- **Buying** (Plans and credits → **Switch to this plan** or **Buy**, then **Get the invoice**) issues an invoice at
  once. What it sells is switched on as soon as that invoice is paid. A note printed on the invoice (a purchase order
  number) is optional.
- Buying needs the organization's **Billing details** filled in first; the page links to them when they are missing.
  If it says Syruss can't take orders yet, Syruss's own billing setup isn't finished: that is for Syruss support.

## Invoices and their statuses
Settings → Billing → **Invoices** lists them with their number, issue and due dates, total and status:
| Status shown | Meaning |
|---|---|
| Waiting for payment | Issued and not yet fully paid; due by its due date. |
| Overdue | Still waiting after its due date. |
| Paid | Fully paid. The receipt can be downloaded. |
| Not paid | A renewal that wasn't paid in time: the organization moved to the Free plan. It can still be paid, and paying it puts the plan back. |
| Void | Cancelled by Syruss; nothing is owed. |
An open invoice shows its lines, Subtotal, tax, Total, what was Paid or Credited, and **Still owed**. Corrections appear
as **Credit notes** on the invoice. **Download the invoice (PDF)**; **Download the receipt (PDF)** once it's paid.

## Paying by card
On the invoice, under **How to pay → Card**: **Pay … by card** opens Stripe's secure page; card details never reach
Syruss. **Keep the card to pay renewals by themselves** saves it for renewals. After paying, the invoice page says the
card payment is being confirmed and updates by itself. The kept card is changed with **Change the card** or dropped with
**Stop using it** under Billing details → Card for renewals. Card payment appears only when Syruss has it switched on.

## Paying by bank transfer
On the invoice, under **How to pay → Bank transfer**:
1. Pay into the account shown, quoting the reference shown on the page.
2. Then **Send the slip**: Amount paid, Paid on, **Transfer reference (optional)** (the reference or transaction number
   on the bank's confirmation: it helps Syruss find the transfer), the slip or transfer confirmation (a PDF or a photo:
   JPEG, PNG or WebP, up to 10 MB each, up to 5 files), and an optional note. **Send it to Syruss**.
3. Under **Slips you sent** it shows **Being checked**: Syruss checks it against its account, and the invoice is paid
   only once the slip is **Confirmed**. While it is being checked, **Withdraw** takes it back to send a corrected one.
4. **Not confirmed** means Syruss couldn't match it (the money hasn't arrived, or the slip doesn't show it). The reason
   is shown under it ("Syruss said: …"). The invoice keeps waiting: fix what the reason says and send the slip again
   from the same invoice; the form appears again once nothing is being checked.
If the page says to ask Syruss for the account details, there is no account for that currency yet: contact support.
A slip is never "paid" until Syruss confirms it. Never tell anyone an invoice is paid unless its status says Paid.

## Renewals, reminders, grace period and the Free plan
- A paid plan renews each period. Shortly before the period ends (seven days by default) a **renewal invoice** for the
  next period is issued, due when that period starts. A card kept for renewals pays it by itself; if the card is
  declined, the owner is told and can pay by hand (the card is tried again before reminders are sent).
- **Reminders** are emailed to the billing email before and after the due date (by default 3 days before, on the day,
  and 3 days after).
- A renewal not paid by its due date makes the plan **Renewal past due**; the Plan and usage page says to pay before the
  grace period ends. The **grace period** is seven days by default.
- After the grace period the organization **moves to the Free plan**: its data stays, add-ons end, and the invoice
  shows **Not paid**. Paying it later, even then, puts the plan back.
- Exact dates are on the invoice and in the emails; the default timings above can differ for an organization.

## Billing details
Settings → Billing → **Billing details** → Made out to: Legal name, Billing email (invoices, receipts and reminders are
sent there), Address, City, Country (two letters; it decides the tax), and optionally a second address line, Postal
code, Region, Tax ID and Purchase order number (printed on every invoice). Invoices already issued keep the details they
were issued with.
