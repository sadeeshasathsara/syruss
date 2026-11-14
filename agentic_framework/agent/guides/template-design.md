# Designing a template

A template is a model of the business, not a copy of the page. Work out three things from the samples:

- **The things**: the real, recurring things the documents talk about (people, places, organizations,
  vehicles, products, accounts). Each exists apart from this page and turns up on other documents. Each
  becomes an object with an identity (the field that identifies it, the field that names it) and only
  the attributes that belong to the thing itself, whatever document it is on.
- **The facts**: what each document records about those things (an event, a transaction, an assignment,
  a measurement). Each becomes an object without identity, holding what is true of that occurrence only
  (dates, times, quantities, amounts, status, signatures) and pointing at the things involved with
  `resolves_to` fields that hold their identifying values.
- **The document itself**: one object for what is true of the page as a whole (its number, date, totals,
  who issued it), if anything is.

## How people will recognize each document

People can't tell documents apart by file name (`94771079086_20260722_223851.jpeg`). Every notification, list
and log line names a document by its **identification key**, so choose one in the template: the one or few
fields of the one-per-document objects (`"multiple": false`) that tell this document from the others the
way the team would say it, such as where and when, or who and which period. Prefer values that are on every
page and short; two fields usually beat one (a date alone can repeat across documents, and so can a name
alone). Never use a field of a repeating object.

Use details people understand, not codes. People know names, not IDs: a thing's name beats its code or
number. Whenever the page shows both a code and a name for the same thing, put the name in a field of a
one-per-document object and use that one in the key (keep the code as its own field too). Use a code only when
the page gives nothing descriptive; a date or period is always fine to combine with a name. This is about the
thing the document is about, not the person who filled it in. Say your choice in the proposal's observation so the
admin can change it, and ask when the samples leave it open. The admin can change it at any time.

Headers, tables and footers are layout, not the model: one row can mention several things, and one
thing appears in many rows and on many pages. Ask of every field whether it describes the thing or this
occurrence. Getting this wrong (a row that is both a thing and what happened to it on this document) means the
platform cannot recognize the same thing on the next document.

Then check the model against every sample: each value on each page has exactly one place to go. Ask
the admin, in the same run and with your hypothesis, about whatever the samples could not settle (the
meaning of a code or column, which blocks matter, which rules always hold); revise the proposal when
they answer. Only include rules you have evidence for. Every proposal is the complete schema, not a diff.

## Changing an active template

Template changes need human approval (`propose_template_change`). Before proposing one, check it
against past documents with `validate_record` using the proposed schema (their records, not their
pages), and report what you found in the evidence: how many past records still fit, what would change.

## Template format

An illustration of the format only, with placeholder names: `Party` and `Thing` are things with an identity,
`Head` is the document itself, `Item` is a repeating fact. Model each workspace from its own documents and
name objects and fields the way its pages and people do.

```json
{"document_type": "<kind>", "description": "...",
 "objects": [
  {"type": "Party", "description": "...", "multiple": false,
   "identity": {"id_field": "code", "label_field": "name"},
   "fields": [{"name": "code", "type": "string", "required": true, "description": "..."},
              {"name": "name", "type": "string", "required": true}]},
  {"type": "Thing", "multiple": true, "identity": {"id_field": "code", "label_field": "label"},
   "fields": [{"name": "code", "type": "string", "required": true}, {"name": "label", "type": "string"},
              {"name": "kind", "type": "string"}]},
  {"type": "Head", "multiple": false,
   "fields": [{"name": "number", "type": "string", "required": true}, {"name": "date", "type": "date"},
              {"name": "party", "type": "string", "resolves_to": "Party"},
              {"name": "item_count", "type": "integer"}, {"name": "total", "type": "number"}]},
  {"type": "Item", "multiple": true,
   "fields": [{"name": "thing", "type": "string", "resolves_to": "Thing"},
              {"name": "quantity", "type": "number"}, {"name": "rate", "type": "number"},
              {"name": "amount", "type": "number"}, {"name": "start", "type": "time"}, {"name": "end", "type": "time"}]}],
 "identification": {"parts": [{"object": "Head", "field": "number"}, {"object": "Head", "field": "date"}], "separator": "_"},
 "validation_rules": [
   {"id": "...", "rule_type": "sum_match", "object": "Item", "field": "amount", "total_object": "Head",
    "total_field": "total", "description": "..."},
   {"rule_type": "count_equals", "object": "Item", "expected_count": 5 | "count_object": "Head", "count_field": "item_count"},
   {"rule_type": "expression", "object": "Item", "expression": "abs(amount - quantity * rate) < 0.01"},
   {"rule_type": "allowed_values", "object": "Thing", "field": "kind", "values": ["A", "B"]},
   {"rule_type": "regex", "object": "Party", "field": "code", "pattern": "\\d{9}"},
   {"rule_type": "unique", "object": "Thing", "field": "code"},
   {"rule_type": "time_diff", "object": "Item", "start_field": "start", "end_field": "end",
    "hours_field": "<a number field>" | "expected_hours": 8, "tolerance_hours": 0.25}]}
```

`identification.parts` are `{"object", "field"}` pairs, each a field of an object with `"multiple": false`; the key
joins their values in order with the separator ("1001_2026-07-22"). Leave `identification` out only when no
one-per-document field identifies a document.

Field types: string, number, integer, boolean, date, time. `resolves_to` names another object that has
an identity. The platform refuses a record whose references point at nothing in it, and refuses a
COMMIT whose identity contradicts what is already known.
