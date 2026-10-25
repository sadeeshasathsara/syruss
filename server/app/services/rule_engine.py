"""
Deterministic record validation against a template schema.

The agent decides what a document says; this module decides whether a record
is allowed to be committed. Nothing here knows about any specific domain:
object types, fields and rules all come from the template.

Record shape: {"<ObjectType>": [ {field: value, ...}, ... ]}
(a single dict is accepted for objects with "multiple": false)

Objects that stand for real, recurring things declare an identity
({"id_field", "label_field"}). A field with "resolves_to": "<ObjectType>" holds the
identity of such a thing; the record must then contain that thing (a row of
<ObjectType> with the same id), so every reference in a record points at something in it.

Supported rules (all take "object": "<ObjectType>"):
  time_diff      start_field, end_field, and either hours_field or expected_hours; tolerance_hours (default 0.25)
  sum_match      field (summed over records), total_object + total_field (single record holding the total)
  count_equals   expected_count, or count_object + count_field holding the expected count
  allowed_values field, values
  regex          field, pattern
  unique         field
  expression     expression over field names (per record), e.g. "hours <= 16"
"""
import ast
import math
import operator
import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Schema normalization
# ---------------------------------------------------------------------------

def normalize_schema(schema: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Returns {object_type: {"multiple": bool, "identity": dict | None, "fields": {name: field_spec}}}."""
    objects: Dict[str, Dict[str, Any]] = {}
    for obj in (schema or {}).get("objects", []) or []:
        if not isinstance(obj, dict) or not obj.get("type"):
            continue
        fields: Dict[str, Dict[str, Any]] = {}
        for f in obj.get("fields", []) or []:
            if isinstance(f, str):
                fields[f] = {"name": f, "type": "string", "required": False}
            elif isinstance(f, dict) and f.get("name"):
                fields[f["name"]] = {"type": "string", "required": False, **f}
        ident = obj.get("identity")
        objects[obj["type"]] = {
            "multiple": obj.get("multiple", True),
            "identity": ident if isinstance(ident, dict) else None,
            "fields": fields,
        }
    return objects


def schema_rules(schema: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return list((schema or {}).get("validation_rules", []) or [])


# ---------------------------------------------------------------------------
# Value parsing
# ---------------------------------------------------------------------------

_TIME_RE = re.compile(r"^\s*(\d{1,2})[:.]?(\d{2})\s*([ap]\.?m\.?)?\s*$", re.IGNORECASE)


def parse_time_minutes(value: Any) -> Optional[int]:
    if value is None:
        return None
    m = _TIME_RE.match(str(value))
    if not m:
        return None
    hours, minutes = int(m.group(1)), int(m.group(2))
    suffix = (m.group(3) or "").lower().replace(".", "")
    if suffix == "pm" and hours < 12:
        hours += 12
    if suffix == "am" and hours == 12:
        hours = 0
    if hours > 24 or minutes > 59:
        return None
    return (hours % 24) * 60 + minutes


def parse_number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _check_type(field_type: str, value: Any) -> Optional[str]:
    if field_type in ("number", "integer"):
        n = parse_number(value)
        if n is None:
            return f"expected a number, got {value!r}"
        if field_type == "integer" and not float(n).is_integer():
            return f"expected an integer, got {value!r}"
    elif field_type == "boolean":
        if not isinstance(value, bool) and str(value).lower() not in ("true", "false", "yes", "no"):
            return f"expected true/false, got {value!r}"
    elif field_type == "time":
        if parse_time_minutes(value) is None:
            return f"expected a time like HH:MM, got {value!r}"
    elif field_type == "date":
        if isinstance(value, (date, datetime)):
            return None
        try:
            date.fromisoformat(str(value)[:10])
        except ValueError:
            return f"expected an ISO date YYYY-MM-DD, got {value!r}"
    return None


# ---------------------------------------------------------------------------
# Safe expression evaluation (no attribute access, calls limited to math helpers)
# ---------------------------------------------------------------------------

_BIN_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.Mod: operator.mod, ast.Pow: operator.pow,
    ast.FloorDiv: operator.floordiv,
}
_CMP_OPS = {
    ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt,
    ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge,
}
_FUNCS = {"abs": abs, "min": min, "max": max, "round": round, "sqrt": math.sqrt}


def safe_eval(expression: str, variables: Optional[Dict[str, Any]] = None) -> Any:
    variables = variables or {}

    def ev(node: ast.AST) -> Any:
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float, str, bool)):
            return node.value
        if isinstance(node, ast.Name):
            if node.id not in variables:
                raise ValueError(f"unknown name '{node.id}'")
            return variables[node.id]
        if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
            return _BIN_OPS[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd, ast.Not)):
            v = ev(node.operand)
            return -v if isinstance(node.op, ast.USub) else (+v if isinstance(node.op, ast.UAdd) else not v)
        if isinstance(node, ast.BoolOp):
            vals = [ev(v) for v in node.values]
            return all(vals) if isinstance(node.op, ast.And) else any(vals)
        if isinstance(node, ast.Compare):
            left = ev(node.left)
            for op, comp in zip(node.ops, node.comparators):
                if type(op) not in _CMP_OPS:
                    raise ValueError("unsupported comparison")
                right = ev(comp)
                if not _CMP_OPS[type(op)](left, right):
                    return False
                left = right
            return True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS and not node.keywords:
            return _FUNCS[node.func.id](*[ev(a) for a in node.args])
        raise ValueError(f"unsupported expression element: {type(node).__name__}")

    return ev(ast.parse(expression, mode="eval"))


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _records(data: Dict[str, Any], object_type: str) -> List[Dict[str, Any]]:
    value = data.get(object_type)
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [r for r in value if isinstance(r, dict)]
    return []


def _error(code: str, message: str, path: str = "") -> Dict[str, str]:
    return {"code": code, "message": message, "path": path}


def _check_structure(objects: Dict[str, Dict[str, Any]], data: Dict[str, Any]) -> List[Dict[str, str]]:
    errors: List[Dict[str, str]] = []
    for key, value in data.items():
        if key not in objects:
            errors.append(_error(
                "unknown_object",
                f"'{key}' is not an object in the template. Record only template objects; "
                "propose a template change to capture new information.",
                key,
            ))
            continue
        spec = objects[key]
        if not isinstance(value, (list, dict)):
            errors.append(_error("bad_shape", f"'{key}' must be a list of records or a single record.", key))
            continue
        if isinstance(value, list) and not spec["multiple"] and len(value) > 1:
            errors.append(_error("bad_shape", f"'{key}' allows a single record, got {len(value)}.", key))
        for i, rec in enumerate(_records(data, key)):
            for fname in rec:
                if fname not in spec["fields"]:
                    errors.append(_error(
                        "unknown_field",
                        f"'{key}.{fname}' is not a template field. Propose a template change to capture it.",
                        f"{key}[{i}].{fname}",
                    ))
            for fname, fspec in spec["fields"].items():
                val = rec.get(fname)
                path = f"{key}[{i}].{fname}"
                if val is None or val == "":
                    if fspec.get("required"):
                        errors.append(_error("missing_required", f"Required field '{key}.{fname}' is empty.", path))
                    continue
                type_problem = _check_type(fspec.get("type", "string"), val)
                if type_problem:
                    errors.append(_error("bad_type", f"'{key}.{fname}': {type_problem}", path))
    return errors


def _ref_key(value: Any) -> str:
    return re.sub(r"\s+", "", str(value)).upper()


def _check_references(objects: Dict[str, Dict[str, Any]], data: Dict[str, Any]) -> List[Dict[str, str]]:
    """Every resolves_to value must name a thing that is in this record."""
    errors: List[Dict[str, str]] = []
    for key, spec in objects.items():
        for fname, fspec in spec["fields"].items():
            target = fspec.get("resolves_to")
            ident = objects.get(target, {}).get("identity") if target else None
            if not ident:
                continue  # malformed references are rejected when the template is saved
            known = {_ref_key(r.get(ident["id_field"])) for r in _records(data, target)
                     if r.get(ident["id_field"]) not in (None, "")}
            for i, rec in enumerate(_records(data, key)):
                val = rec.get(fname)
                if val not in (None, "") and _ref_key(val) not in known:
                    errors.append(_error(
                        "dangling_reference",
                        f"'{key}.{fname}' is {val!r}, but no {target} with {ident['id_field']} {val!r} is in this record. "
                        f"Record that {target} too, or correct the reference.",
                        f"{key}[{i}].{fname}",
                    ))
    return errors


def _check_rule(rule: Dict[str, Any], data: Dict[str, Any]) -> List[Dict[str, str]]:
    rtype = rule.get("rule_type") or rule.get("type")
    obj = rule.get("object") or rule.get("target_object")
    rid = rule.get("id") or rtype or "rule"
    label = rule.get("description") or rid
    records = _records(data, obj) if obj else []
    errors: List[Dict[str, str]] = []

    if rtype == "time_diff":
        tol = float(rule.get("tolerance_hours", 0.25))
        for i, r in enumerate(records):
            start = parse_time_minutes(r.get(rule.get("start_field")))
            end = parse_time_minutes(r.get(rule.get("end_field")))
            if start is None or end is None:
                continue
            actual = ((end - start) % (24 * 60)) / 60.0
            expected = parse_number(r.get(rule["hours_field"])) if rule.get("hours_field") else parse_number(rule.get("expected_hours"))
            if expected is not None and abs(actual - expected) > tol:
                errors.append(_error(rid, f"{label}: {actual:.2f}h between start and end, expected {expected:g}h.", f"{obj}[{i}]"))

    elif rtype == "sum_match":
        total_recs = _records(data, rule.get("total_object") or "")
        if total_recs:
            stated = parse_number(total_recs[0].get(rule.get("total_field")))
            line_sum = sum(parse_number(r.get(rule.get("field"))) or 0.0 for r in records)
            if stated is not None and abs(line_sum - stated) > float(rule.get("tolerance", 0.01)):
                errors.append(_error(rid, f"{label}: sum of {obj}.{rule.get('field')} is {line_sum:g}, stated total is {stated:g}.", obj))

    elif rtype == "count_equals":
        expected = rule.get("expected_count")
        if expected is None and rule.get("count_object"):
            count_recs = _records(data, rule["count_object"])
            expected = parse_number(count_recs[0].get(rule.get("count_field"))) if count_recs else None
        if expected is not None and len(records) != int(expected):
            errors.append(_error(rid, f"{label}: found {len(records)} {obj} records, expected {int(expected)}.", obj))

    elif rtype == "allowed_values":
        allowed = {str(v).strip().lower() for v in rule.get("values", [])}
        for i, r in enumerate(records):
            v = r.get(rule.get("field"))
            if v not in (None, "") and str(v).strip().lower() not in allowed:
                errors.append(_error(rid, f"{label}: '{v}' is not one of {sorted(rule.get('values', []))}.", f"{obj}[{i}].{rule.get('field')}"))

    elif rtype == "regex":
        pattern = re.compile(rule.get("pattern", ".*"))
        for i, r in enumerate(records):
            v = r.get(rule.get("field"))
            if v not in (None, "") and not pattern.fullmatch(str(v)):
                errors.append(_error(rid, f"{label}: '{v}' does not match the expected format.", f"{obj}[{i}].{rule.get('field')}"))

    elif rtype == "unique":
        seen: Dict[str, int] = {}
        for i, r in enumerate(records):
            v = r.get(rule.get("field"))
            if v in (None, ""):
                continue
            k = str(v).strip().lower()
            if k in seen:
                errors.append(_error(rid, f"{label}: '{v}' appears in records {seen[k]} and {i}.", f"{obj}[{i}].{rule.get('field')}"))
            seen[k] = i

    elif rtype == "expression":
        for i, r in enumerate(records):
            variables = {k: (parse_number(v) if parse_number(v) is not None else v) for k, v in r.items()}
            try:
                ok = safe_eval(rule.get("expression", "True"), variables)
            except Exception:
                continue  # fields missing in this record; structure checks report required fields
            if not ok:
                errors.append(_error(rid, f"{label}: '{rule.get('expression')}' is false.", f"{obj}[{i}]"))

    return errors


def validate_record(schema: Optional[Dict[str, Any]], data: Dict[str, Any]) -> Dict[str, Any]:
    """Returns {"valid": bool, "errors": [...], "rules_checked": int}."""
    if not isinstance(data, dict):
        return {"valid": False, "errors": [_error("bad_shape", "Record must be an object keyed by object type.")], "rules_checked": 0}
    objects = normalize_schema(schema)
    errors = _check_structure(objects, data) + _check_references(objects, data)
    rules = schema_rules(schema)
    for rule in rules:
        errors.extend(_check_rule(rule, data))
    return {"valid": not errors, "errors": errors, "rules_checked": len(rules)}


def check_rule_definitions(schema: Dict[str, Any]) -> List[str]:
    """Static checks on a proposed schema, so bad template changes are rejected up front."""
    problems: List[str] = []
    objects = normalize_schema(schema)
    if not objects:
        problems.append("Schema defines no objects.")
    known_types = {"time_diff", "sum_match", "count_equals", "allowed_values", "regex", "unique", "expression"}
    for obj in (schema or {}).get("objects", []) or []:
        ident = obj.get("identity") if isinstance(obj, dict) else None
        if ident:
            fields = objects.get(obj.get("type"), {}).get("fields", {})
            for key in ("id_field", "label_field"):
                if ident.get(key) not in fields:
                    problems.append(f"Object '{obj.get('type')}' identity.{key} '{ident.get(key)}' is not one of its fields.")
    for otype, spec in objects.items():
        for fname, fspec in spec["fields"].items():
            target = fspec.get("resolves_to")
            if not target:
                continue
            if target == otype:
                problems.append(f"'{otype}.{fname}' resolves_to its own object; an object's identity already says what it is.")
            elif target not in objects:
                problems.append(f"'{otype}.{fname}' resolves_to unknown object '{target}'.")
            elif not objects[target]["identity"]:
                problems.append(f"'{otype}.{fname}' resolves_to '{target}', which has no identity; "
                                f"give '{target}' an identity (the field that identifies it and the field that names it).")
    for rule in schema_rules(schema):
        rtype = rule.get("rule_type") or rule.get("type")
        obj = rule.get("object") or rule.get("target_object")
        if rtype not in known_types:
            problems.append(f"Rule '{rule.get('id')}' has unknown rule_type '{rtype}'.")
        if obj not in objects:
            problems.append(f"Rule '{rule.get('id')}' refers to unknown object '{obj}'.")
        if rtype == "expression":
            try:
                ast.parse(rule.get("expression", ""), mode="eval")
            except SyntaxError:
                problems.append(f"Rule '{rule.get('id')}' has an invalid expression.")
        if rtype == "regex":
            try:
                re.compile(rule.get("pattern", ""))
            except re.error:
                problems.append(f"Rule '{rule.get('id')}' has an invalid regex.")
    return problems
