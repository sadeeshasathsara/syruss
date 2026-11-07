# Template rules

Rules are checks that run on every record after a document is read, whatever the documents are: values that must add
up or follow from each other, a written count that must match, a format every value follows. They are written over the
template's own objects and fields, and live beside the template (Template → Rules), not in its versions, so adding or
changing one does not need a new version.

| Where | What |
|---|---|
| `server/app/services/rule_lang.py` | the language: compiler (type checks against the template) and interpreter |
| `server/app/services/rule_service.py` | saving, dry runs on saved records, recheck after a template change |
| `server/app/api/v1/rules.py` | the people's API (`/api/v1/rules`) |
| `server/app/api/internal/agent_tools/rules.py` | the agent's API (`/rules`, `/rules/check`, `/rules/suggest`) |
| `agentic_framework/agent/tools/rules.py`, `agent/guides/rules.md` | the agent's tools and guide |
| `www/src/app/[org]/w/[ws]/template/rules/` | Rules tab, new rule (plain English or custom), edit |

## Life of a rule

- **Written** by a person (custom editor, live compile check and dry run), or **suggested** by Syruss: on its own when it
  sees a relation that holds, or because a person described one on the new-rule page (that opens a new chat with
  `mode: "rules"`). A suggestion does nothing until someone with `template.publish` adds it.
- **Compiled** against the active template before it is saved. A rule that doesn't compile is refused with the problem,
  the column where it is, an explanation and a hint.
- **Run** on every record (`rule_engine.validate_record(..., rules)`): an `error` rule that fails makes the record invalid,
  so a COMMIT is refused and the document goes to review; a `warning` is stored on the record's validation and shown.
- **Stopped** (`broken`) when a new template version is activated and the rule no longer compiles; it is not run, people
  with `template.publish` are notified (`rule.broken`), and it comes back on its own if a later version fits again.

The checks that come with a template version (`validation_rules`: `sum_match`, `time_diff`, ...) still run as before and
are listed read-only on the Rules tab.

## The language

Python's expression syntax, with names that are template paths. The object and field names below are placeholders:

```
scope Item        a == b * c                              a row's value follows from its other values
scope document    abs(sum(Item.a) - Head.total) <= 0.01   rows add up to a total written once
scope document    count(Item) == Head.count               the number of rows matches a written count
scope Item        when: b == "X"    check: a >= "12:00"   a condition that holds for some rows only
scope Item        party.a == a                            a row agrees with the thing it refers to
scope Head        matches(a, "[A-Z]{3}-[0-9]{6}")         every value follows a format
```

In a row scope, bare names are that row's fields; `Object.field` reads a one-per-document object, or (inside an
aggregate) every row of a many-object; `ref.field` follows a `resolves_to` field. Blank values make a rule "not
applicable" for that row rather than failing it. The full reference is the agent's guide, `agent/guides/rules.md`, and
`GET /api/v1/rules/language`.

## Why not Python, and why it is safe

Rules are written by people and by a language model, so they are untrusted input. Running them as Python (`eval`, `exec`,
or a "restricted" Python) is not safe: in-process Python sandboxes have always been escaped (`().__class__.__mro__`,
`__subclasses__`, frames, generators, C extensions), and a real sandbox would mean a separate locked-down process per
check. A home-made grammar would be safe but would need its own parser and error messages, for no security gain.

So a rule is **parsed** by Python's own parser (`ast.parse(mode="eval")`, which only builds a tree and runs nothing) and
**interpreted** by `rule_lang.py`:

- Only listed node types are accepted: constants, names that are template objects or fields, `Object.field` /
  `ref.field` paths, arithmetic, comparisons, `and/or/not`, `if/else`, literal lists for `in`, and calls to the listed
  functions. Subscripts, lambdas, comprehensions, f-strings, `:=`, keyword or `*` arguments, and any name starting with
  `_` are refused at compile time.
- Compiling turns the tree into small Python closures that read values out of the record's dicts. Nothing calls
  `getattr`, `eval`, `exec` or `import`, and a rule only ever sees the one record it is checking: no database, files,
  network, other documents or interpreter state.
- Work is bounded: 600 characters and 200 nodes per expression, at most two nested row conditions, a step budget per
  record, exponents at most 12, texts at most 10,000 characters, and `matches` (and the template's `regex` checks) use
  RE2, which runs in linear time, so no pattern can hang the server.

`server/tests/test_rule_lang.py` holds the escape attempts and resource attacks that must stay refused.
