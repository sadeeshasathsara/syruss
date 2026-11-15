"""
The icons a template object can have: its picture on the template, on its profile and in search.

One list, read three ways: the server checks a proposed template against it, the agent reads it as a guide
(`agentic_framework/agent/guides/object-icons.md`, written by `scripts/object_icons.py`), and both apps map the same
names to lucide icons (`www/src/lib/objectIcons.ts`, `admin/src/lib/objectIcons.ts`, written by the same script).
Names are lucide's own (https://lucide.dev). A test keeps all four in step.
"""
from __future__ import annotations

import difflib
from typing import Dict, List, Optional, Tuple

DEFAULT_ICON = "box"

# group -> [(icon name, what it suits)]
GROUPS: Dict[str, List[Tuple[str, str]]] = {
    "People": [
        ("user", "one person"), ("users", "a group of people, a team"), ("user-round", "a person, softer"),
        ("contact", "a contact card, someone to reach"), ("id-card", "an identity, a member, a badge holder"),
        ("baby", "a child, a dependant"), ("person-standing", "someone present, a visitor, an attendee"),
        ("hand-helping", "a helper, a volunteer, someone cared for"), ("graduation-cap", "a student, a learner"),
        ("briefcase-business", "someone at work, a professional"), ("hard-hat", "a worker on site, a contractor"),
        ("stethoscope", "a clinician, a health check"), ("shield-user", "an officer, someone on duty, someone responsible"),
    ],
    "Places": [
        ("map-pin", "a place, an address, a site"), ("map", "an area, a region, a route plan"),
        ("globe", "a country, worldwide"), ("navigation", "a direction, a heading"), ("compass", "a bearing, an orientation"),
        ("flag", "a territory, a checkpoint, a milestone"), ("mountain", "terrain, outdoors"), ("trees", "a park, a forest"),
        ("tent", "a camp, a temporary site"), ("route", "a route, a journey, a delivery round"),
    ],
    "Buildings": [
        ("building", "a building, an office"), ("building-2", "a company, an organization"), ("house", "a home, a property"),
        ("store", "a shop, a branch, a seller"), ("factory", "a plant, a manufacturer"), ("warehouse", "a warehouse, a depot, stock held"),
        ("hospital", "a hospital, a clinic"), ("school", "a school, a campus"), ("hotel", "a hotel, accommodation"),
        ("church", "a place of worship"), ("landmark", "a public body, a bank, an institution"), ("door-open", "a room, an entrance"),
    ],
    "Vehicles and transport": [
        ("truck", "a lorry, a shipment, a delivery"), ("car", "a car, a vehicle"), ("bus", "a bus, a coach"),
        ("train-front", "a train, rail"), ("plane", "a flight, air freight"), ("ship", "a vessel, sea freight"),
        ("bike", "a bicycle, a courier"), ("forklift", "handling equipment, loading"), ("fuel", "fuel, a filling"),
        ("container", "a shipping container, a load"),
    ],
    "Money": [
        ("banknote", "cash, an amount, a payment"), ("wallet", "an account, a balance"), ("credit-card", "a card payment"),
        ("coins", "a charge, a fee, small amounts"), ("receipt", "a receipt, a bill, a charge line"), ("piggy-bank", "savings, a deposit"),
        ("hand-coins", "a payout, a refund, a grant"), ("badge-dollar-sign", "a price, a rate"), ("calculator", "a calculation, a total"),
        ("percent", "a tax, a discount, a rate"), ("chart-line", "a trend, a value over time"),
    ],
    "Documents and records": [
        ("file-text", "a document, a letter"), ("file", "a file"), ("files", "a set of documents"), ("folder", "a case, a collection"),
        ("clipboard-list", "a list, a form, an inspection"), ("clipboard-check", "a check, an approval, a sign-off"),
        ("notebook", "a log, a journal"), ("book-open", "a manual, a register"), ("scroll-text", "a contract, terms, a policy"),
        ("newspaper", "a publication, a notice"), ("signature", "a signature, a signed party"), ("stamp", "an official stamp, a certification"),
        ("file-spreadsheet", "a table, a sheet of lines"), ("sticky-note", "a note, a remark"),
    ],
    "Time": [
        ("calendar", "a date, a day"), ("calendar-days", "a period, a schedule"), ("calendar-clock", "an appointment, a booking"),
        ("calendar-range", "a date range, a term"), ("calendar-check", "a confirmed booking, attendance"), ("clock", "a time, hours"),
        ("timer", "a duration"), ("hourglass", "a deadline, waiting"), ("alarm-clock", "a reminder, a start time"),
        ("history", "past events, a history"),
    ],
    "Goods and items": [
        ("package", "a parcel, an item"), ("boxes", "stock, inventory"), ("shopping-cart", "an order, a purchase"),
        ("shopping-bag", "a sale, retail"), ("tag", "a product, a label, a category"), ("tags", "categories, labels"),
        ("barcode", "a product code, a SKU"), ("scan-barcode", "a scanned item"), ("gift", "a gift, a reward"),
        ("shirt", "clothing, a uniform"), ("apple", "food, produce"), ("coffee", "a drink, a café item"),
        ("utensils", "a meal, catering"), ("pill", "a medicine, a dose"),
    ],
    "Equipment and technology": [
        ("wrench", "a repair, maintenance"), ("hammer", "a tool, a job"), ("settings", "a setting, a configuration"),
        ("cog", "a machine part, a mechanism"), ("plug", "a connection, power"), ("cpu", "a device, hardware"),
        ("monitor", "a screen, a workstation"), ("laptop", "a computer"), ("smartphone", "a phone, a mobile device"),
        ("printer", "a printer"), ("server", "a server, a system"), ("hard-drive", "storage, a disk"),
        ("battery", "a battery, a charge level"), ("lightbulb", "a fixture, an idea"), ("camera", "a camera, a photo"),
        ("key", "a key, access, a credential"), ("lock", "something secured, a lock"),
    ],
    "Health and science": [
        ("heart-pulse", "health, vital signs"), ("activity", "a measurement, an activity"), ("syringe", "an injection, a vaccine"),
        ("thermometer", "a temperature reading"), ("ambulance", "an emergency, a call-out"), ("brain", "a mind, an assessment"),
        ("dna", "a sample, genetics"), ("microscope", "a lab test, an analysis"), ("flask-conical", "a chemical, a lab sample"),
    ],
    "Nature and conditions": [
        ("leaf", "an environmental item, organic"), ("sprout", "a crop, growth"), ("tree-pine", "a tree, timber"),
        ("flower", "a plant"), ("sun", "weather, daylight"), ("cloud", "weather, the cloud"), ("droplet", "water, a liquid"),
        ("flame", "fire, heat, gas"), ("zap", "electricity, energy use"), ("wind", "wind, air"), ("snowflake", "cold, frozen goods"),
        ("paw-print", "an animal, a pet"), ("fish", "fish, seafood"), ("bird", "a bird, livestock"),
    ],
    "Communication": [
        ("mail", "an email, a letter sent"), ("phone", "a call, a phone number"), ("message-square", "a message, a comment"),
        ("messages-square", "a conversation"), ("megaphone", "an announcement, a campaign"), ("bell", "an alert, a notification"),
        ("send", "a dispatch, something sent"), ("at-sign", "an address online, a handle"), ("inbox", "something received, an intake"),
        ("video", "a video, a meeting"),
    ],
    "Work, events and status": [
        ("briefcase", "a job, a project, an engagement"), ("ticket", "a ticket, an admission, a request"), ("trophy", "a result, a win"),
        ("award", "an award, a qualification"), ("target", "a goal, a target"), ("list-checks", "tasks done, a checklist"),
        ("list-todo", "tasks to do"), ("kanban", "work in stages"), ("gavel", "a legal matter, a ruling"), ("scale", "a judgement, a balance, weight"),
        ("shield", "protection, insurance, security"), ("shield-check", "a compliance check, a guarantee"),
        ("triangle-alert", "an incident, a risk, a warning"), ("ban", "a refusal, something blocked"),
        ("circle-check", "a completed item, a pass"), ("badge-check", "a verified item, a licence"),
    ],
    "Measures and shapes": [
        ("box", "a general thing"), ("circle", "a general item"), ("square", "a unit, a slot"), ("hash", "a number, an identifier"),
        ("layers", "levels, a composition"), ("layout-grid", "a set of slots, a layout"), ("star", "a rating, a favourite"),
        ("bookmark", "a saved item, a reference"), ("link", "a link between things, a reference"), ("puzzle", "a component, a part"),
        ("shapes", "mixed items"), ("sparkles", "something special"), ("gauge", "a reading, a level"), ("ruler", "a dimension, a length"),
        ("weight", "a weight, a mass"), ("chart-column", "counts, a figure"), ("chart-pie", "a share, a breakdown"),
        ("sigma", "a sum, a total"), ("list", "a line item, an entry"), ("table", "a table of rows"),
    ],
}

ICON_NAMES: List[str] = [name for icons in GROUPS.values() for name, _ in icons]
_SET = frozenset(ICON_NAMES)
_HINTS = {name: hint for icons in GROUPS.values() for name, hint in icons}

assert DEFAULT_ICON in _SET and len(_SET) == len(ICON_NAMES), "icon names must be unique and include the default"


def is_icon(name: Optional[str]) -> bool:
    return isinstance(name, str) and name in _SET


def suggest(name: str, n: int = 5) -> List[str]:
    """Icon names close to `name` (by spelling, then by what they suit), for an error message."""
    name = (name or "").strip().lower()
    close = difflib.get_close_matches(name, ICON_NAMES, n=n, cutoff=0.5)
    if len(close) < n and name:
        words = set(name.replace("-", " ").split())
        for icon, hint in _HINTS.items():
            if icon not in close and words & set(hint.replace(",", " ").split()):
                close.append(icon)
                if len(close) >= n:
                    break
    return close or [DEFAULT_ICON]


def icon_of(obj: Optional[dict]) -> str:
    """The object's icon, or the default when it has none (or one no longer in the list)."""
    icon = (obj or {}).get("icon")
    return icon if is_icon(icon) else DEFAULT_ICON


def problems(schema: Optional[dict]) -> List[str]:
    """Objects in a proposed schema whose icon is missing or not in the list, as messages the agent can act on."""
    out: List[str] = []
    for obj in (schema or {}).get("objects") or []:
        if not isinstance(obj, dict):
            continue
        icon = obj.get("icon")
        if icon is None or icon == "":
            out.append(f'Object "{obj.get("type")}" has no icon: pick one from the object icons guide.')
        elif not is_icon(icon):
            out.append(f'Object "{obj.get("type")}" has icon "{icon}", which isn\'t in the list. Close ones: {", ".join(suggest(str(icon)))}.')
    return out
