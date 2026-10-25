"""
What a destination receives. Without a mapping it gets the approved record as it is. With one, the record is reshaped into the fields the
other system expects, each filled from a field of the record, a formula (the same safe formula language as analytics screens) or a constant.

    {"mode": "mapped",
     "fields": [{"target": "invoice_number", "source": "field",    "value": "Invoice.number"},
                {"target": "total_cents",    "source": "formula",  "value": "round(Invoice_total * 100)"},
                {"target": "currency",       "source": "constant", "value": "EUR"}],
     "lines": {"target": "lines", "from": "LineItem", "fields": [{"target": "description", "source": "field", "value": "LineItem.description"}]}}

A target with a dot ("customer.name") becomes a nested object. Formulas see the first record of every object as Object_field (and as the
bare field name when only one single object has it) plus filename and external_id; inside lines they also see the line's own fields bare.
"""
import re
from typing import Any, Dict, List, Optional, Tuple
from app.services import formula
from app.services.formula import Blank, FormulaError

SOURCES = ("field", "formula", "constant")


def _ident(name: str) -> str:
    return re.sub(r"\W", "_", str(name))


def records(data: Dict[str, Any], object_type: str) -> List[Dict[str, Any]]:
    value = (data or {}).get(object_type)
    if isinstance(value, dict):
        return [value]
    return [r for r in value if isinstance(r, dict)] if isinstance(value, list) else []


def scope_values(data: Dict[str, Any], *, document: Optional[Dict[str, Any]] = None, row: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The names a formula may use, with their values."""
    values: Dict[str, Any] = {}
    bare: Dict[str, List[Any]] = {}
    for object_type in (data or {}):
        first = (records(data, object_type) or [None])[0]
        if first is None:
            continue
        multiple = len(records(data, object_type)) > 1
        for name, value in first.items():
            values[f"{_ident(object_type)}_{_ident(name)}"] = value
            if not multiple:
                bare.setdefault(_ident(name), []).append(value)
    for name, found in bare.items():
        if len(found) == 1:
            values.setdefault(name, found[0])
    values["filename"] = (document or {}).get("filename")
    values["external_id"] = (document or {}).get("external_id")
    if row:
        values.update({_ident(k): v for k, v in row.items()})
    return values


def _lookup(path: str, data: Dict[str, Any], row: Optional[Dict[str, Any]], row_type: Optional[str], document: Dict[str, Any]) -> Any:
    """'Object.field' from the first record (or the current line); 'document.filename' / 'document.external_id' from the document."""
    head, _, field = path.partition(".")
    if head == "document":
        return document.get(field)
    if row is not None and head == row_type:
        return row.get(field)
    found = records(data, head)
    return found[0].get(field) if found else None


def _set(out: Dict[str, Any], target: str, value: Any) -> None:
    parts = target.split(".")
    node = out
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def _one(spec: Dict[str, Any], data: Dict[str, Any], document: Dict[str, Any], row: Optional[Dict[str, Any]], row_type: Optional[str],
         problems: List[str]) -> Any:
    kind, raw = spec.get("source", "field"), spec.get("value")
    if kind == "constant":
        return raw
    if kind == "field":
        return _lookup(str(raw or ""), data, row, row_type, document)
    try:
        return formula.compile_formula(str(raw or "")).run(scope_values(data, document=document, row=row))
    except FormulaError as exc:
        problems.append(f"{spec.get('target')}: {exc}")
    except Blank:
        pass
    return None


def apply(mapping: Dict[str, Any], data: Dict[str, Any], document: Dict[str, Any], required: Tuple[str, ...] = ()) -> Tuple[Dict[str, Any], List[str]]:
    """(the reshaped record, what could not be done). `required` are targets that must end up with a value."""
    problems: List[str] = []
    out: Dict[str, Any] = {}
    for spec in mapping.get("fields") or []:
        if spec.get("target"):
            _set(out, spec["target"], _one(spec, data, document, None, None, problems))
    lines = mapping.get("lines")
    if lines and lines.get("from"):
        built = []
        for row in records(data, lines["from"]):
            line: Dict[str, Any] = {}
            for spec in lines.get("fields") or []:
                if spec.get("target"):
                    _set(line, spec["target"], _one(spec, data, document, row, lines["from"], problems))
            built.append(line)
        _set(out, lines.get("target") or "lines", built)
    for target in required:
        if _get(out, target) in (None, ""):
            problems.append(f"{target} is empty. Map it to a field of the record, or set it to a fixed value.")
    return out, problems[:20]


def _get(out: Dict[str, Any], target: str) -> Any:
    node: Any = out
    for part in target.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def check(mapping: Dict[str, Any], schema: Dict[str, Any], targets: Optional[List[str]] = None) -> List[str]:
    """Problems with a mapping before it is saved: unknown fields, broken formulas, targets the destination does not take."""
    problems: List[str] = []
    known: Dict[str, set] = {o["type"]: {f["name"] for f in o.get("fields", [])} for o in (schema or {}).get("objects", [])}
    names = {f"{_ident(t)}_{_ident(n)}" for t, fs in known.items() for n in fs} | {_ident(n) for fs in known.values() for n in fs} | {"filename", "external_id"}

    def each(specs: List[Dict[str, Any]], extra: set, row_type: Optional[str]) -> None:
        for spec in specs:
            target = spec.get("target") or ""
            if not target:
                problems.append("A mapped field has no name.")
                continue
            if targets is not None and not row_type and target not in targets:
                problems.append(f"'{target}' is not something this destination takes.")
            source, value = spec.get("source", "field"), spec.get("value")
            if source not in SOURCES:
                problems.append(f"{target}: unknown source '{source}'.")
            elif source == "field":
                head, _, field = str(value or "").partition(".")
                if head != "document" and (head not in known or field not in known[head]):
                    problems.append(f"{target}: '{value}' is not a field of this template.")
            elif source == "formula":
                try:
                    formula.compile_formula(str(value or ""), allowed=names | extra)
                except FormulaError as exc:
                    problems.append(f"{target}: {exc}")

    each(mapping.get("fields") or [], set(), None)
    lines = mapping.get("lines")
    if lines:
        if lines.get("from") not in known:
            problems.append(f"Lines come from '{lines.get('from')}', which is not an object of this template.")
        else:
            each(lines.get("fields") or [], {_ident(n) for n in known[lines["from"]]}, lines["from"])
    return problems


def guess(schema: Dict[str, Any], targets: List[Dict[str, Any]]) -> Dict[str, Any]:
    """A first mapping for an accounting-style destination: each target filled from the template field whose name looks most like it."""
    hints = {
        "vendor_name": ("vendor", "supplier", "seller", "payee", "company"),
        "invoice_number": ("invoice_number", "invoice_no", "invoice_id", "bill_number", "number", "reference"),
        "invoice_date": ("invoice_date", "issue_date", "bill_date", "date"),
        "due_date": ("due_date", "due", "payment_due"),
        "currency": ("currency",),
        "total": ("total", "amount_due", "grand_total", "amount"),
        "tax_total": ("tax_total", "tax", "vat"),
        "memo": ("memo", "notes", "description"),
        "description": ("description", "item", "name", "product"),
        "quantity": ("quantity", "qty"),
        "unit_price": ("unit_price", "price", "rate"),
        "amount": ("amount", "line_total", "total"),
    }
    objects = (schema or {}).get("objects", [])
    mapping: Dict[str, Any] = {"mode": "mapped", "fields": []}

    def find(candidates: Tuple[str, ...], pool) -> Optional[str]:
        for hint in candidates:
            for obj in pool:
                for f in obj.get("fields", []):
                    if f["name"].lower() == hint:
                        return f"{obj['type']}.{f['name']}"
        for hint in candidates:
            for obj in pool:
                for f in obj.get("fields", []):
                    if hint in f["name"].lower():
                        return f"{obj['type']}.{f['name']}"
        return None

    singles = [o for o in objects if not o.get("multiple")] or objects
    repeating = [o for o in objects if o.get("multiple")]
    for t in targets:
        if t.get("line"):
            continue
        hit = find(hints.get(t["key"], (t["key"],)), singles)
        if hit:
            mapping["fields"].append({"target": t["key"], "source": "field", "value": hit})
    line_targets = [t for t in targets if t.get("line")]
    if line_targets and repeating:
        pick = repeating[0]
        fields = []
        for t in line_targets:
            hit = find(hints.get(t["key"], (t["key"],)), [pick])
            if hit:
                fields.append({"target": t["key"], "source": "field", "value": hit})
        mapping["lines"] = {"target": "lines", "from": pick["type"], "fields": fields}
    return mapping
