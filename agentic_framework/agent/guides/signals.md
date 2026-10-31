# Signals on a record

`validate_record` and `submit_record` return signals about the record. A blocking signal refuses a
COMMIT until you deal with it; each signal's message says what it found.

## Identity signals (the workspace graph)

Committed records build a graph of who and what appears together (a person on a site's roster, a
customer with a branch).

- `identity_lookalike` (blocking): what you read has never appeared in this context, while a regular
  there is one character or a letter or two away: the classic misread. Zoom into the page again. If the
  page clearly shows what you read, resubmit with `acknowledged_signals` `[{id, reason}]` saying what you
  saw; if not, correct it only when the page supports the correction, otherwise send the document to
  review.
- `identity_stranger` (advisory): something with a history elsewhere appears here for the first time.
  It never blocks; mention it in notes when it matters.

Use `related` to see where something usually appears. The graph helps you choose between readings; it
never supplies a value the page does not show.

## Documents already in the database

`possible_duplicate` and `duplicate_file` (blocking) mean the document looks already recorded; the
existing record comes as evidence. Compare the two records, not the pages.

- Same document (uploaded twice, a copy, a rescan): send this one to review and say which document it
  duplicates, so a person decides. Never commit the same document twice.
- Sure it is a different document: acknowledge the signal and say exactly what differs.
