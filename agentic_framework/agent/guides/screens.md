# Analytics screens

An analytics screen (the "Analytics" tab in the sidebar) is a saved table over the workspace's saved records: one row for every row of a template object, across all documents. People
open them in the sidebar. The platform computes every cell, total and calculation; you choose what to show and check the result.

## The definition

```json
{
  "source": {"object": "Guard", "include_pending_review": false},
  "columns": [
    {"id": "name", "label": "Guard", "field": "name"},
    {"id": "site", "label": "Site", "field": "Roster.site_id"},
    {"id": "hours", "label": "Hours", "field": "hours", "format": {"type": "number", "decimals": 1}, "aggregate": "sum"},
    {"id": "overtime", "label": "Overtime", "kind": "formula", "formula": "max(hours - 8, 0)", "aggregate": "sum",
     "highlight": [{"when": "overtime > 3", "tone": "warning"}]}
  ],
  "filters": [{"field": "site", "op": "eq", "value": "S12"}],
  "sort": [{"column": "hours", "dir": "desc"}],
  "group_by": [],
  "totals": true
}
```

- `source.object`: a repeating object (many rows per document) or a one-per-document object (one row per document).
- A column is a **field** (`field`: a field of the object, or `Object.field` of a one-per-document object such as the roster's date and
  site) or a **formula** (`kind: "formula"`). `id` is lowercase letters, digits, `_`; formulas refer to columns by id, and to any field by
  its variable (see `screen_catalogue`: `variable`, and `object__field` for document fields). `hidden: true` keeps a helper column out of view.
- `format.type`: text, number, currency, percent, date, time, duration (hours shown as 7h 30m). `aggregate`: sum, avg, min, max, count,
  count_distinct, used for group results and the totals row.
- `filters`: `op` is eq, ne, contains, in, gte, lte, between (value `[low, high]`), empty, not_empty, or `{"formula": "hours > 10"}`. Filters
  apply before grouping. Matching ignores case.
- **Grouping** (`group_by`: column ids): one row per group. Every other visible column needs an `aggregate`, or `hidden: true`, or be a
  formula with `"scope": "group"` that works on the group's results (e.g. `round(hours / shifts, 2)`).

### Per row, then totalled, or on the totals?

A formula column with the default scope works on **each row** and is then totalled by its `aggregate`: overtime per shift is
`"formula": "max(hours - 8, 0)", "aggregate": "sum"` (each row's hours minus 8, never below 0, then summed per guard). A formula with
`"scope": "group"` sees only the group's results, so `max(hours - 8, 0)` there would subtract 8 once from the guard's total hours:
wrong. Use `scope: "group"` only for ratios of totals (`round(hours / shifts, 1)`), and only after the columns it uses have an `aggregate`.

## Formulas

Numbers, text, true/false, blank. `+ - * / % **`, comparisons, `and or not`, `a if condition else b`. Functions: `abs min max round floor
ceil sqrt`, `if_(c, a, b) coalesce isempty`, `number text concat upper lower trim len contains left right`, `hours_between(start, end)`
(times like 07:30, past midnight counts forwards), `minutes_between days_between year month day weekday`.

Blank stays blank through arithmetic; text that is not a number, or dividing by zero, gives a blank cell and is counted in the result's `errors`.
`max`/`min` ignore blanks. Read `errors` after every preview: a column that is blank for many rows means the formula or the data needs a look.

## Ask first, then build

In a chat, an open request ("an analytics page", "a full table", "to make decisions", "I have no idea") is not yet something to build.
Look at the records and the existing screens, then ask up to three short questions in one reply: the decision it should help with, how
they want it cut (per person, per site, per month), which period, what counts as too much or too little. Offer two or three directions
from their real data with the numbers you saw, so they can pick. A specific request needs no questions. `save_screen` needs `goal` (what
they want, from their words) and `goal_is_clear: true`; the platform refuses it otherwise.

If an existing screen already covers part of it, ask "change 'X' or make a new one?" and wait. `save_screen` refuses a near-repeat
until they have said "a new one" (then pass `person_chose_new: true`); when they wrote from a screen's own page, change that screen
with `update_screen`. People can search and filter any screen themselves (a search box over every column, filters including "between",
"one of a list" and written conditions); `run_screen` takes the same `search`, so use it to answer a question rather than building a
screen for it.

## How to work

1. `list_screens` first: do not repeat a screen that exists; improve it (`update_screen`, only when asked) or build something different.
2. `screen_catalogue` for the real field names and variables. Never guess a field.
3. `preview_screen`, look at the rows and totals, fix the problems it reports, try again. Save only what you have seen work (the
   platform refuses to save a definition you have not previewed). Check one figure by hand: preview with a filter for a single person
   or group, or without grouping, and make sure the arithmetic of your formula gives what the table says.
4. Answer questions with numbers from `run_screen` or `preview_screen`, and say which screen (or which filters) they came from. Never add up
   rows in your head and never present a figure no tool returned. Say plainly when there are few records and a pattern may be chance.

## What is worth building (be inventive, then verify)

Think like an analyst who knows this business, not like a form-filler. Good screens answer a question someone would otherwise ask a
colleague or work out in a spreadsheet:

- Totals per person, site, supplier or month (grouped, with sum and count), and who is above or below the usual.
- Outliers: rows well above the group's usual (a formula against a group's average), values that repeat suspiciously, round numbers.
- Time: this week against last, a drift in a figure over months, busy days of the week (`weekday`), late or early patterns.
- What is missing or late: blank required-looking fields, documents with no total, days with no roster, items past a due date.
- Money and rates: tax or margin from a total, price per unit, share of the total (`amount / total`).
- Reconciliation: rows that should agree (hours worked against hours claimed, quantity against items listed).
- Ranking: top ten and bottom ten by a measure.

Name a screen the way a colleague would ask for it. Give each a one-sentence description, and for ones you suggest on your own, a rationale
that says what you noticed and why it matters, in plain words, with the real number if you have one. A few screens that earn their place
beat many that do not: three suggestions at most, none that repeat an existing screen.
