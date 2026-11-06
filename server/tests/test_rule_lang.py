import time
import pytest
from app.services.rule_lang import RuleError, compile_rule, language_help, problems
from tests.conftest import SCHEMA, VALID_RECORD

INVOICE = {
    "objects": [
        {"type": "Supplier", "multiple": False, "identity": {"id_field": "tax_id", "label_field": "name"},
         "fields": [{"name": "tax_id", "type": "string"}, {"name": "name", "type": "string"}]},
        {"type": "Invoice", "multiple": False, "fields": [
            {"name": "number", "type": "string"}, {"name": "date", "type": "date"}, {"name": "total", "type": "number"},
            {"name": "supplier", "type": "string", "resolves_to": "Supplier"}, {"name": "line_count", "type": "integer"},
            {"name": "status", "type": "string"}]},
        {"type": "Line", "multiple": True, "fields": [
            {"name": "description", "type": "string"}, {"name": "quantity", "type": "number"},
            {"name": "price", "type": "number"}, {"name": "amount", "type": "number"}]},
    ],
}
RECORD = {
    "Supplier": {"tax_id": "TX-1", "name": "Acme"},
    "Invoice": {"number": "INV-000123", "date": "2026-07-22", "total": 150, "supplier": "TX-1", "line_count": 2, "status": "paid"},
    "Line": [{"description": "Bolts", "quantity": 10, "price": 5, "amount": 50},
             {"description": "Nuts", "quantity": 20, "price": 5, "amount": 100}],
}


def run(check, scope="document", data=None, schema=None, when=""):
    return compile_rule({"scope": scope, "check": check, "when": when}, schema or INVOICE).run(data or RECORD)


def error(check, scope="document", schema=None):
    with pytest.raises(RuleError) as caught:
        compile_rule({"scope": scope, "check": check}, schema or INVOICE)
    return caught.value


# --- what rules can say -------------------------------------------------------

def test_document_rules_pass_and_fail():
    assert run("abs(sum(Line.amount) - Invoice.total) <= 0.01") == {"checked": 1, "failed": [], "not_applicable": 0}
    assert run("count(Line) == Invoice.line_count")["failed"] == []
    assert run("sum(Line.amount) == 999")["failed"] == [{"index": None, "path": ""}]


def test_row_rules_report_the_failing_rows():
    result = run("amount == quantity * price", scope="Line",
                 data={**RECORD, "Line": [RECORD["Line"][0], {**RECORD["Line"][1], "amount": 90}]})
    assert result["checked"] == 2 and result["failed"] == [{"index": 1, "path": "Line[1]"}]


def test_times_dates_and_text():
    assert run("hours_between(in_time, out_time) == hours", scope="Guard", schema=SCHEMA, data=VALID_RECORD)["failed"] == []
    assert run("Invoice.date <= today() and Invoice.date >= \"2026-01-01\"")["failed"] == []
    assert run('matches(Invoice.number, "INV-[0-9]{6}")')["failed"] == []
    assert run('Invoice.status in ["Paid", "open"]')["failed"] == []
    assert run('Invoice.status not in ["paid"]')["failed"] != []


def test_aggregates_with_conditions_and_references():
    assert run("all(Line, amount > 0) and count_if(Line, amount >= 100) == 1 and not any(Line, quantity > 50)")["failed"] == []
    assert run("unique(Line.description) and count_distinct(Line.price) == 1 and max_of(Line.amount) == 100")["failed"] == []
    assert run("Invoice.supplier.name == Supplier.name")["failed"] == []


def test_blank_values_make_a_rule_not_applicable():
    data = {**RECORD, "Line": [{"description": "Bolts", "quantity": 10, "price": 5, "amount": None}]}
    assert run("amount == quantity * price", scope="Line", data=data) == {"checked": 0, "failed": [], "not_applicable": 1}
    assert run("isempty(amount) or amount > 0", scope="Line", data=data)["checked"] == 1
    assert run("amount == blank", scope="Line", data=data)["failed"] == []
    assert run("amount != blank", scope="Line", data=data)["failed"] != []


def test_a_condition_can_hold_for_some_rows_only_and_times_compare_as_times():
    schema = {"objects": [{"type": "Item", "fields": [{"name": "kind", "type": "string"}, {"name": "at", "type": "time"}]}]}
    data = {"Item": [{"kind": "X", "at": "19:00"}, {"kind": "x", "at": "7:00 am"}, {"kind": "Y", "at": "06:00"}]}
    result = run('at >= "12:00"', scope="Item", schema=schema, data=data, when='upper(kind) == "X"')
    assert result == {"checked": 2, "failed": [{"index": 1, "path": "Item[1]"}], "not_applicable": 1}   # 7:00 am is before noon


def test_when_limits_which_rows_are_checked():
    result = run("amount >= 100", scope="Line", when="quantity > 15")
    assert result == {"checked": 1, "failed": [], "not_applicable": 1}


# --- what the compiler catches, with where and why ----------------------------------

def test_unknown_fields_and_objects_get_suggestions():
    e = error("sum(Line.amont) == Invoice.total")
    assert e.code == "unknown_field" and "amount" in e.hint and e.col is not None
    assert error("Invoce.total > 0").code == "unknown_name"
    with pytest.raises(RuleError) as caught:
        compile_rule({"scope": "Lines", "check": "true"}, INVOICE)
    assert caught.value.part == "scope" and "Line" in caught.value.hint


def test_types_and_many_versus_one():
    assert error("Invoice.date + 1 > 0").code == "type_mismatch"
    assert error("Invoice.number > 5").code == "type_mismatch"
    assert error("Line.amount > 0").code == "many_not_value"
    assert error("Line > 0").code == "rows_not_value"
    assert error("sum(Invoice.total) > 0").code == "bad_arguments"
    assert error("hours_between(Invoice.total, Invoice.total) > 1").code == "type_mismatch"
    assert error("Invoice.total + 1").code == "not_boolean"


def test_syntax_errors_point_at_the_spot():
    e = error("Invoice.total = 5")
    assert e.code == "syntax" and "==" in e.hint
    assert error("sum(Line.amount").code == "syntax"
    assert error('matches(Invoice.number, "[a-")').code == "bad_pattern"


def test_problems_shape_for_the_editor():
    [p] = problems({"scope": "document", "check": "Invoice.totl > 0"}, INVOICE)
    assert set(p) == {"code", "part", "message", "explanation", "hint", "col", "end_col"} and p["part"] == "check"
    assert problems({"scope": "Line", "check": "amount > 0"}, INVOICE) == []
    assert {f["name"] for f in language_help()["aggregates"]} >= {"sum", "count", "all", "any"}


# --- security: nothing but the language gets through ----------------------------------

@pytest.mark.parametrize("source", [
    "__import__('os').system('id')",
    "().__class__.__mro__[1].__subclasses__()",
    "Invoice.__class__",
    "Invoice.total.__class__ == 1",
    "[x for x in Line]",
    "(lambda: 1)() == 1",
    "Line[0].amount > 0",
    "f'{Invoice.total}' == '1'",
    "open('/etc/passwd') == 1",
    "eval('1') == 1",
    "getattr(Invoice, 'total') == 1",
    "round(Invoice.total, ndigits=2) == 1",
    "(y := 1) == 1",
    "{1: 2} == 1",
    "sum(*Line) == 1",
    "_secret == 1",
])
def test_anything_outside_the_language_is_refused(source):
    with pytest.raises(RuleError):
        compile_rule({"scope": "document", "check": source}, INVOICE)


def test_work_is_bounded():
    start = time.monotonic()
    assert run("9 ** 9 ** 9 > 1")["checked"] == 0               # exponent cap: not applicable, not a hang
    assert error("'a' * 1000000000 == 'a'").code == "type_mismatch"  # text can't be multiplied
    many = {**RECORD, "Line": [{"description": "a" * 40 + "b", "quantity": 1, "price": 1, "amount": 1}] * 300}
    assert run('all(Line, matches(description, "(a+)+$"))', data=many)["failed"]  # RE2: linear time, no ReDoS
    assert run("all(Line, all(Line, amount == 1))", data=many)["checked"] in (0, 1)  # step cap holds nested loops
    assert time.monotonic() - start < 5
    assert error("all(Line, all(Line, all(Line, amount == 1)))").code == "too_complex"
    assert error("Invoice.total > 0 and " * 40 + "true").code in ("too_complex", "too_long")
