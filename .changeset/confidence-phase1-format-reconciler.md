---
"document-processing-agentic-system": minor
---

feat(confidence): Phase 1 - deterministic format validators and intrinsic arithmetic reconciler

Adds the Format & Arithmetic sub-scorer (fixed 20-pt weight) as Phase 1
of the production-grade confidence engine. Includes IBAN, Luhn, ISO date,
currency, email, and RE2 regex validators, plus an ArithmeticReconciler
that verifies qty×price=line_total, line sums vs subtotal, subtotal+tax
vs grand total, and time-range vs hours. 16/16 unit tests pass.
