---
server: patch
---

- **Card payments work with the installed Stripe library (16).** Its objects aren't dictionaries any more, and three calls still read them as if they were, so each failed:
  - **Finding a card payment whose webhook never arrived:** the check failed quietly, and Stripe's "You're all done here" page kept coming back.
  - **Keeping the card for renewals:** the payment webhook failed whenever "Keep the card" was ticked.
  - **Paying a renewal with the kept card:** the charge failed.
- Stripe's answers are now converted to plain data once, and tested with the library's real objects.
