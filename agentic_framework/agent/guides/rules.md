# Rules

A rule is a yes/no check that runs on every record after a document is read. The platform compiles it against the
template and runs it, so a rule means the same thing whoever wrote it. An **error** rule that fails stops a record from
being committed (fix the misread, or send the document to review with the rule's message); a **warning** is only reported.
Rules live under Template, Rules. The template's own checks (from the template version) are listed there too and work the
same way.

This workspace can hold any kind of document. Every rule comes from THIS workspace: its template (get_workspace), the
values its saved records really use (query_records with distinct), and what its people tell you. The object and field names
below (`Head`, `Item`, `Party`, `a`, `b`) are placeholders that show the language, not things to look for.

You never turn a rule on yourself: `suggest_rule` makes a suggestion, and a person adds it from the chat card or the Rules
tab. Always try it with `check_rule` first.

## A rule

- `scope`: `document` for a rule about the whole record, or an object type to check **each row** of it on its own.
- `check`: the yes/no expression. It passes when true, fails when false.
- `when` (optional): only check the rows (or records) where this is true.
- `severity`: `error` or `warning`. `message`: what people read when it fails, in their words, naming what is wrong.

## The language

Python-like expressions, nothing more: values, fields, operators, comparisons and the functions below.

| Write | Means |
|---|---|
| `a` | in a row scope: that row's field `a` |
| `Head.a` | a field of a one-per-document object |
| `sum(Item.a)` | the values of a field across the rows of a many-object (only inside an aggregate) |
| `party.a` | follows a field that `resolves_to` another object, to that row in the same record |
| `Item` | all rows of an object, for `count(Item)`, `all(Item, ...)`, `any(...)`, `count_if(...)` |
| `true`, `false`, `blank`, `12.5`, `"text"`, `"2026-07-22"`, `"07:30"` | constants |

Operators: `+ - * / % // **`, `== != < <= > >=`, `and or not`, `x in ["A", "B"]`, `a if condition else b`.
Dates and times compare as dates and times, also against written values (`a >= "12:00"`, `a <= "2026-12-31"`); text
compares without case.

Aggregates: `sum`, `avg`, `min_of`, `max_of`, `count`, `count_distinct`, `unique` take `Object.field`;
`count(Object)` counts rows; `all(Object, cond)`, `any(Object, cond)`, `count_if(Object, cond)` check a condition on each row
(inside the condition, bare names are that object's fields).

Functions: `abs round floor ceil sqrt min max`, `if_ coalesce isempty`, `number text concat upper lower trim len contains
left right`, `hours_between minutes_between` (times, past midnight too), `days_between year month day weekday` (dates),
`matches(text, "pattern")` (the whole text matches; RE2 syntax), `today()`.

Blank values make a rule "not applicable" for that row instead of failing it (required fields are checked separately).
To require a value, say so: `not isempty(a)`.

### The shapes rules take

```
scope Item       a == b * c                                   a row's value follows from its other values
scope document   abs(sum(Item.a) - Head.total) <= 0.01        rows add up to a total written once
scope document   count(Item) == Head.count                    the number of rows matches a written count
scope Item       when: b == "X"    check: a >= "12:00"        a condition that holds only for some rows
scope Head       matches(a, "[A-Z]{3}-[0-9]{6}")              every value follows a format
scope document   unique(Item.a)                               a value never repeats in one document
scope Item       party.a == a                                 a row agrees with the thing it refers to
```

## Writing one from what a person says

Whether they ask for a rule ("make sure ...") or only state something every record should satisfy, it is a rule:
remember it as a lesson, and suggest the rule.

1. Find what they mean in THIS template: which object and fields (get_workspace), and the values the records really use
   for any code or category they mention (query_records with distinct); a word they use may be written differently on
   the documents. `list_rules`: don't repeat an existing rule, offer to change it instead.
2. Scope: a statement about each row is scoped to that object; one that compares rows with a total, counts rows or looks
   across rows is `document`. A statement with several cases may need one rule per case.
3. Amounts measured or calculated need a tolerance (`abs(a - b) <= 0.01`), not `==`.
4. `check_rule`. When it doesn't compile, the answer says what, where (`col`) and how to fix it: fix it and check again.
5. Read the dry run: how many saved records pass, fail or don't apply. Failures on committed records mean either the rule
   is wrong or those records are; look at one failing record before deciding, and say which it is.
6. `suggest_rule` with a name, a message for people, and a rationale that says what you checked (how many records it
   passes on, and what the failures are).
7. Tell them in `reply_to_user` what the rule checks, in their words, and that they add it with the button.

Ask one short question first only when the meaning is truly unclear: which field, how much tolerance, error or warning.

## When to suggest one yourself

Suggest a rule when the documents show a relation that holds and would catch misreads: values that add up or follow from
each other, a count written on the page, a format every value follows, a value that must not repeat, or a correction a
person made that a rule would have caught. Only after `check_rule` shows it passes on the saved records (or you can
explain each failure). One good rule beats several vague ones; a few suggestions at most, and never one that already
exists or was dismissed.

## When a rule fails on the document you are reading

A failing rule is evidence of a misread, or of a page that really is inconsistent. Look again at the fields it names
(zoom in). If you misread, correct the record. If the page itself is inconsistent, don't bend the record to pass: send it
to review with the rule's message and what the page shows.
