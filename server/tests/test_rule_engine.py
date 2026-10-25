import copy
from app.services.rule_engine import check_rule_definitions, validate_record
from tests.conftest import SCHEMA, VALID_RECORD


def codes(result):
    return sorted(e["code"] for e in result["errors"])


def test_valid_record_passes():
    result = validate_record(SCHEMA, VALID_RECORD)
    assert result["valid"], result
    assert result["rules_checked"] == 1


def test_unknown_objects_and_fields_are_refused():
    data = copy.deepcopy(VALID_RECORD)
    data["Guard"][0]["overtime"] = "OT"
    data["Vehicle"] = [{"plate": "X"}]
    assert codes(validate_record(SCHEMA, data)) == ["unknown_field", "unknown_object"]


def test_required_and_types():
    data = copy.deepcopy(VALID_RECORD)
    data["Guard"][0]["name"] = ""
    data["Guard"][0]["hours"] = "twelve"
    data["Roster"][0]["date"] = "22/07/2026"
    assert codes(validate_record(SCHEMA, data)) == ["bad_type", "bad_type", "missing_required"]


def test_time_diff_across_midnight_uses_hours_field():
    data = copy.deepcopy(VALID_RECORD)
    data["Guard"][0]["hours"] = 8
    result = validate_record(SCHEMA, data)
    assert codes(result) == ["shift_hours"]
    assert "12.00h" in result["errors"][0]["message"]


def test_other_rule_types():
    schema = {
        "objects": [
            {"type": "Invoice", "multiple": False, "fields": [{"name": "total", "type": "number"}, {"name": "lines", "type": "integer"}]},
            {"type": "Line", "fields": [{"name": "sku"}, {"name": "amount", "type": "number"}, {"name": "unit"}]},
        ],
        "validation_rules": [
            {"id": "sum", "rule_type": "sum_match", "object": "Line", "field": "amount", "total_object": "Invoice", "total_field": "total"},
            {"id": "count", "rule_type": "count_equals", "object": "Line", "count_object": "Invoice", "count_field": "lines"},
            {"id": "unit", "rule_type": "allowed_values", "object": "Line", "field": "unit", "values": ["kg", "pcs"]},
            {"id": "sku", "rule_type": "regex", "object": "Line", "field": "sku", "pattern": r"[A-Z]{3}-\d+"},
            {"id": "uniq", "rule_type": "unique", "object": "Line", "field": "sku"},
            {"id": "pos", "rule_type": "expression", "object": "Line", "expression": "amount > 0"},
        ],
    }
    good = {"Invoice": {"total": 30, "lines": 2},
            "Line": [{"sku": "ABC-1", "amount": 10, "unit": "kg"}, {"sku": "ABC-2", "amount": 20, "unit": "pcs"}]}
    assert validate_record(schema, good)["valid"]
    bad = {"Invoice": {"total": 99, "lines": 3},
           "Line": [{"sku": "abc", "amount": -1, "unit": "box"}, {"sku": "abc", "amount": 5, "unit": "kg"}]}
    assert codes(validate_record(schema, bad)) == ["count", "pos", "sku", "sku", "sum", "uniq", "unit"]


def test_rule_definitions_are_checked():
    schema = {"objects": [{"type": "A", "fields": ["x"]}],
              "validation_rules": [{"id": "r1", "rule_type": "nope", "object": "A"},
                                   {"id": "r2", "rule_type": "expression", "object": "B", "expression": "x >"}]}
    problems = check_rule_definitions(schema)
    assert len(problems) == 3
    assert check_rule_definitions(SCHEMA) == []


ORDERS = {
    "objects": [
        {"type": "Customer", "multiple": True, "identity": {"id_field": "account_no", "label_field": "name"},
         "fields": [{"name": "account_no", "required": True}, {"name": "name"}]},
        {"type": "Order", "multiple": True,
         "fields": [{"name": "number"}, {"name": "customer", "resolves_to": "Customer"}]},
    ],
}


def test_references_point_at_things_in_the_record():
    customers = [{"account_no": "AC 12", "name": "Acme"}]
    assert validate_record(ORDERS, {"Customer": customers, "Order": [{"number": "1", "customer": "ac12"}]})["valid"]
    # An unreadable reference is left empty rather than guessed.
    assert validate_record(ORDERS, {"Customer": customers, "Order": [{"number": "2", "customer": None}]})["valid"]
    result = validate_record(ORDERS, {"Customer": customers, "Order": [{"number": "3", "customer": "AC99"}]})
    assert codes(result) == ["dangling_reference"]
    assert result["errors"][0]["path"] == "Order[0].customer"


def test_reference_definitions_are_checked():
    assert check_rule_definitions(ORDERS) == []
    schema = copy.deepcopy(ORDERS)
    schema["objects"][1]["fields"] += [{"name": "a", "resolves_to": "Nowhere"}, {"name": "b", "resolves_to": "Order"}]
    del schema["objects"][0]["identity"]
    problems = check_rule_definitions(schema)
    assert len(problems) == 3
    assert any("no identity" in p for p in problems)
