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

## An icon for every object

Every object gets an `"icon"`: its picture on the template, on its profile page and in search. Pick it from the
list below by what the object is and does, not by the document it came from: a person or a group of people, a place
or a building, something that moves, money, a period or a moment, a record or a step in some work, a measurement,
an item that is handled. Prefer the most specific icon that fits; use `box` only when nothing does. Two objects may
share an icon, but give them different ones when you can, so people tell them apart at a glance. The platform
refuses a name that isn't in the list. When you change a template, keep the icons people already chose.

<!-- object-icons:start (written by scripts/object_icons.py) -->
- **People:** `user` (one person); `users` (a group of people, a team); `user-round` (a person, softer); `contact` (a contact card, someone to reach); `id-card` (an identity, a member, a badge holder); `baby` (a child, a dependant); `person-standing` (someone present, a visitor, an attendee); `hand-helping` (a helper, a volunteer, someone cared for); `graduation-cap` (a student, a learner); `briefcase-business` (someone at work, a professional); `hard-hat` (a worker on site, a contractor); `stethoscope` (a clinician, a health check); `shield-user` (an officer, someone on duty, someone responsible)
- **Places:** `map-pin` (a place, an address, a site); `map` (an area, a region, a route plan); `globe` (a country, worldwide); `navigation` (a direction, a heading); `compass` (a bearing, an orientation); `flag` (a territory, a checkpoint, a milestone); `mountain` (terrain, outdoors); `trees` (a park, a forest); `tent` (a camp, a temporary site); `route` (a route, a journey, a delivery round)
- **Buildings:** `building` (a building, an office); `building-2` (a company, an organization); `house` (a home, a property); `store` (a shop, a branch, a seller); `factory` (a plant, a manufacturer); `warehouse` (a warehouse, a depot, stock held); `hospital` (a hospital, a clinic); `school` (a school, a campus); `hotel` (a hotel, accommodation); `church` (a place of worship); `landmark` (a public body, a bank, an institution); `door-open` (a room, an entrance)
- **Vehicles and transport:** `truck` (a lorry, a shipment, a delivery); `car` (a car, a vehicle); `bus` (a bus, a coach); `train-front` (a train, rail); `plane` (a flight, air freight); `ship` (a vessel, sea freight); `bike` (a bicycle, a courier); `forklift` (handling equipment, loading); `fuel` (fuel, a filling); `container` (a shipping container, a load)
- **Money:** `banknote` (cash, an amount, a payment); `wallet` (an account, a balance); `credit-card` (a card payment); `coins` (a charge, a fee, small amounts); `receipt` (a receipt, a bill, a charge line); `piggy-bank` (savings, a deposit); `hand-coins` (a payout, a refund, a grant); `badge-dollar-sign` (a price, a rate); `calculator` (a calculation, a total); `percent` (a tax, a discount, a rate); `chart-line` (a trend, a value over time)
- **Documents and records:** `file-text` (a document, a letter); `file` (a file); `files` (a set of documents); `folder` (a case, a collection); `clipboard-list` (a list, a form, an inspection); `clipboard-check` (a check, an approval, a sign-off); `notebook` (a log, a journal); `book-open` (a manual, a register); `scroll-text` (a contract, terms, a policy); `newspaper` (a publication, a notice); `signature` (a signature, a signed party); `stamp` (an official stamp, a certification); `file-spreadsheet` (a table, a sheet of lines); `sticky-note` (a note, a remark)
- **Time:** `calendar` (a date, a day); `calendar-days` (a period, a schedule); `calendar-clock` (an appointment, a booking); `calendar-range` (a date range, a term); `calendar-check` (a confirmed booking, attendance); `clock` (a time, hours); `timer` (a duration); `hourglass` (a deadline, waiting); `alarm-clock` (a reminder, a start time); `history` (past events, a history)
- **Goods and items:** `package` (a parcel, an item); `boxes` (stock, inventory); `shopping-cart` (an order, a purchase); `shopping-bag` (a sale, retail); `tag` (a product, a label, a category); `tags` (categories, labels); `barcode` (a product code, a SKU); `scan-barcode` (a scanned item); `gift` (a gift, a reward); `shirt` (clothing, a uniform); `apple` (food, produce); `coffee` (a drink, a café item); `utensils` (a meal, catering); `pill` (a medicine, a dose)
- **Equipment and technology:** `wrench` (a repair, maintenance); `hammer` (a tool, a job); `settings` (a setting, a configuration); `cog` (a machine part, a mechanism); `plug` (a connection, power); `cpu` (a device, hardware); `monitor` (a screen, a workstation); `laptop` (a computer); `smartphone` (a phone, a mobile device); `printer` (a printer); `server` (a server, a system); `hard-drive` (storage, a disk); `battery` (a battery, a charge level); `lightbulb` (a fixture, an idea); `camera` (a camera, a photo); `key` (a key, access, a credential); `lock` (something secured, a lock)
- **Health and science:** `heart-pulse` (health, vital signs); `activity` (a measurement, an activity); `syringe` (an injection, a vaccine); `thermometer` (a temperature reading); `ambulance` (an emergency, a call-out); `brain` (a mind, an assessment); `dna` (a sample, genetics); `microscope` (a lab test, an analysis); `flask-conical` (a chemical, a lab sample)
- **Nature and conditions:** `leaf` (an environmental item, organic); `sprout` (a crop, growth); `tree-pine` (a tree, timber); `flower` (a plant); `sun` (weather, daylight); `cloud` (weather, the cloud); `droplet` (water, a liquid); `flame` (fire, heat, gas); `zap` (electricity, energy use); `wind` (wind, air); `snowflake` (cold, frozen goods); `paw-print` (an animal, a pet); `fish` (fish, seafood); `bird` (a bird, livestock)
- **Communication:** `mail` (an email, a letter sent); `phone` (a call, a phone number); `message-square` (a message, a comment); `messages-square` (a conversation); `megaphone` (an announcement, a campaign); `bell` (an alert, a notification); `send` (a dispatch, something sent); `at-sign` (an address online, a handle); `inbox` (something received, an intake); `video` (a video, a meeting)
- **Work, events and status:** `briefcase` (a job, a project, an engagement); `ticket` (a ticket, an admission, a request); `trophy` (a result, a win); `award` (an award, a qualification); `target` (a goal, a target); `list-checks` (tasks done, a checklist); `list-todo` (tasks to do); `kanban` (work in stages); `gavel` (a legal matter, a ruling); `scale` (a judgement, a balance, weight); `shield` (protection, insurance, security); `shield-check` (a compliance check, a guarantee); `triangle-alert` (an incident, a risk, a warning); `ban` (a refusal, something blocked); `circle-check` (a completed item, a pass); `badge-check` (a verified item, a licence)
- **Measures and shapes:** `box` (a general thing); `circle` (a general item); `square` (a unit, a slot); `hash` (a number, an identifier); `layers` (levels, a composition); `layout-grid` (a set of slots, a layout); `star` (a rating, a favourite); `bookmark` (a saved item, a reference); `link` (a link between things, a reference); `puzzle` (a component, a part); `shapes` (mixed items); `sparkles` (something special); `gauge` (a reading, a level); `ruler` (a dimension, a length); `weight` (a weight, a mass); `chart-column` (counts, a figure); `chart-pie` (a share, a breakdown); `sigma` (a sum, a total); `list` (a line item, an entry); `table` (a table of rows)
<!-- object-icons:end -->

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
  {"type": "Party", "description": "...", "icon": "<an icon from the list>", "multiple": false,
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

Every object has an `icon` from the list above. Field types: string, number, integer, boolean, date, time. `resolves_to` names another object that has
an identity. The platform refuses a record whose references point at nothing in it, and refuses a
COMMIT whose identity contradicts what is already known.
