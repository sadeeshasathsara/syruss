# Rules

A rule is a yes/no check that runs on every record after a document is read: "the lines add up to the total", "a shift is
at most 16 hours". The platform compiles it against the template and runs it, so a rule means the same thing whoever wrote it.
An **error** rule that fails stops a record from being committed (fix the misread, or send the document to review with the
rule's message); a **warning** is only reported. Rules live under Template, Rules. The template's own checks (from the
template version) are listed there too and work the same way.

You never turn a rule on yourself: `suggest_rule` makes a suggestion, and a person adds it from the chat card or the Rules
tab. Always try it with `check_rule` first.

## A rule

- `scope`: `document` for a rule about the whole record, or an object type to check **each row** of it on its own.
- `check`: the yes/no expression. It passes when true, fails when false.
- `when` (optional): only check the rows (or records) where this is true.
- `severity`: `error` or `warning`. `message`: what people read when it fails. Write it for them: "The lines don't add up to
  the invoice total."

## The language

Python-like expressions, nothing more: values, fields, operators, comparisons and the functions below.

| Write | Means |
|---|---|
| `hours` | in a row scope: that row's field |
| `Invoice.total` | a field of a one-per-document object |
| `sum(Line.amount)` | the values of a field across the rows of a many-object (only inside an aggregate) |
| `guard.name` | follows a field that `resolves_to` another object, to that row in the same record |
| `Line` | all rows of an object, for `count(Line)`, `all(Line, ...)`, `any(...)`, `count_if(...)` |
| `true`, `false`, `blank`, `12.5`, `"text"`, `"2026-07-22"`, `"07:30"` | constants |

Operators: `+ - * / % // **`, `== != < <= > >=`, `and or not`, `x in ["A", "B"]`, `a if condition else b`.
Dates and times compare as dates and times, text compares without case.

Aggregates: `sum`, `avg`, `min_of`, `max_of`, `count`, `count_distinct`, `unique` take `Object.field`;
`count(Object)` counts rows; `all(Object, cond)`, `any(Object, cond)`, `count_if(Object, cond)` check a condition on each row
(inside the condition, bare names are that object's fields).

Functions: `abs round floor ceil sqrt min max`, `if_ coalesce isempty`, `number text concat upper lower trim len contains
left right`, `hours_between minutes_between` (times, past midnight too), `days_between year month day weekday` (dates),
`matches(text, "pattern")` (the whole text matches; RE2 syntax), `today()`.

Blank values make a rule "not applicable" for that row instead of failing it, so `hours <= 16` says nothing about a row
without hours (required fields are checked separately). To require something, say so: `not isempty(signature)`.

### Examples

```
scope Line       amount == quantity * price
scope document   abs(sum(Line.amount) - Invoice.total) <= 0.01
scope document   count(Guard) == Roster.guard_count
scope Guard      hours_between(in_time, out_time) <= 16
scope Guard      when: not isempty(out_time)    check: hours == hours_between(in_time, out_time)
scope Shift      guard.employee_no == guard_no
scope Invoice    matches(number, "INV-[0-9]{6}") and date <= today()
scope document   unique(Guard.employee_id)
```

## Writing one from plain words

1. Find the objects and fields they mean (`get_workspace` has the template; `list_rules` has what exists). Don't repeat an
   existing rule; offer to change it instead.
2. Choose the scope: a rule about each row ("every shift...") is scoped to that object; a rule that compares rows with a total
   or counts rows is `document`.
3. Money and measured amounts need a tolerance: `abs(a - b) <= 0.01`, not `a == b`.
4. `check_rule`. When it doesn't compile, the answer says what, where (`col`) and how to fix it: fix it and check again.
5. Read the dry run: how many saved records pass, fail or don't apply. Failures on committed records mean either the rule is
   wrong or those records are; look at one failing document's record before deciding, and say which it is.
6. `suggest_rule` with a name, a message for people, and a rationale that says what you checked ("passes on 46 of 47 rosters;
   the one that fails, Colombo Port 2026-10-12, has 26 hours where the times say 12").
7. Tell them in `reply_to_user` what the rule checks, in plain words, and that they add it with the button.

Ask one short question first only when the meaning is truly unclear: which object, how much tolerance, error or warning.

## When to suggest one yourself

Suggest a rule when you have seen a relation hold that would catch misreads, for example:
- a total that is the sum of lines, hours that follow from the times, a count written on the page;
- a correction a person made that a rule would have caught (the reviewer fixed a total that didn't add up);
- a format every value follows (an ID always 5 digits).

Only after `check_rule` shows it passes on the saved records (or you can explain each failure). One good rule beats several
vague ones; a few suggestions at most, and never one that already exists or was dismissed.

## When a rule fails on the document you are reading

A failing rule is evidence of a misread, or of a page that really is inconsistent. Look again at the fields it names (zoom in).
If you misread, correct the record. If the page itself is inconsistent, don't bend the record to pass: send it to review with
the rule's message and what the page shows.
