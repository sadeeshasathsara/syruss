"""
Unit tests for Phase 1: Deterministic Format Validators and Intrinsic Arithmetic Reconciler.
"""
from datetime import date, datetime
import pytest

from app.services.validation.formatters import (
    check_format_rule,
    validate_currency,
    validate_email,
    validate_iban,
    validate_iso_date,
    validate_luhn,
    validate_regex,
)
from app.services.validation.math_reconciler import (
    ArithmeticReconciler,
    reconciler,
)


# ============================================================================
# Format & Checksum Validators
# ============================================================================

class TestFormatValidators:
    def test_iban_valid(self):
        # Valid test IBANs (Germany, UK)
        valid_de = "DE89 3704 0044 0532 0130 00"
        valid_gb = "GB82 WEST 1234 5698 7654 32"
        is_valid, err = validate_iban(valid_de)
        assert is_valid is True
        assert err is None

        is_valid, err = validate_iban(valid_gb)
        assert is_valid is True
        assert err is None

    def test_iban_invalid(self):
        # Mutate check digits
        invalid_de = "DE88 3704 0044 0532 0130 00"
        is_valid, err = validate_iban(invalid_de)
        assert is_valid is False
        assert "checksum failed" in err.lower()

        # Too short
        is_valid, err = validate_iban("DE12")
        assert is_valid is False

        # Empty
        is_valid, err = validate_iban("")
        assert is_valid is False

    def test_luhn_valid(self):
        # 79927398713 is a classic Luhn test number
        assert validate_luhn("79927398713")[0] is True
        assert validate_luhn("49927398716")[0] is True
        # Spaces/dashes should be ignored
        assert validate_luhn("7992-7398-713")[0] is True

    def test_luhn_invalid(self):
        # Single digit changed
        assert validate_luhn("79927398710")[0] is False
        assert validate_luhn("abc")[0] is False
        assert validate_luhn("1")[0] is False  # too short
        assert validate_luhn(None)[0] is False

    def test_iso_date(self):
        assert validate_iso_date("2026-11-17")[0] is True
        assert validate_iso_date(date(2026, 11, 17))[0] is True
        assert validate_iso_date(datetime(2026, 11, 17, 10, 0))[0] is True

        # Invalid formats
        assert validate_iso_date("17-11-2026")[0] is False
        assert validate_iso_date("2026/11/17")[0] is False
        assert validate_iso_date("invalid")[0] is False
        assert validate_iso_date(None)[0] is False

        # Out of bounds years (< 1900 or > 2100)
        assert validate_iso_date("1850-05-10")[0] is False
        assert validate_iso_date("2150-01-01")[0] is False

    def test_currency(self):
        assert validate_currency(100)[0] is True
        assert validate_currency(1234.56)[0] is True
        assert validate_currency("$1,234.56")[0] is True
        assert validate_currency("€ 99.90")[0] is True
        assert validate_currency("¥5000")[0] is True
        assert validate_currency("£10.50")[0] is True

        assert validate_currency("not_a_number")[0] is False
        assert validate_currency("")[0] is False
        assert validate_currency(None)[0] is False

    def test_email(self):
        assert validate_email("syruss@enterprise.io")[0] is True
        assert validate_email("first.last+tag@sub.domain.co.uk")[0] is True

        assert validate_email("missing_at_sign.com")[0] is False
        assert validate_email("@no_user.com")[0] is False
        assert validate_email("")[0] is False
        assert validate_email(None)[0] is False

    def test_regex(self):
        assert validate_regex("INV-2026-001", r"^INV-\d{4}-\d{3}$")[0] is True
        assert validate_regex("PO-99", r"^INV-\d{4}-\d{3}$")[0] is False
        assert validate_regex(None, r"^.*$")[0] is False

    def test_check_format_rule_dispatcher(self):
        # Validations via generic dispatcher
        assert check_format_rule("2026-05-01", "iso_date")[0] is True
        assert check_format_rule("2026-05-01", "date")[0] is True
        assert check_format_rule("$45.00", "currency")[0] is True
        assert check_format_rule("admin@system.io", "email")[0] is True
        assert check_format_rule("INV-456", "regex:^INV-\\d+$")[0] is True

        # Unrecognized rule passes gracefully
        assert check_format_rule("anything", "custom_unknown_rule")[0] is True
        assert check_format_rule("anything", None)[0] is True


# ============================================================================
# Intrinsic Arithmetic Reconciler
# ============================================================================

class TestArithmeticReconciler:
    @pytest.fixture
    def invoice_schema(self):
        return {
            "objects": [
                {
                    "type": "InvoiceHeader",
                    "multiple": False,
                    "fields": [
                        {"name": "invoice_number", "type": "string"},
                        {"name": "subtotal", "type": "number", "math_role": "subtotal"},
                        {"name": "tax_amount", "type": "number", "math_role": "tax"},
                        {"name": "grand_total", "type": "number", "math_role": "grand_total"},
                    ],
                },
                {
                    "type": "LineItem",
                    "multiple": True,
                    "fields": [
                        {"name": "item_desc", "type": "string"},
                        {"name": "quantity", "type": "number", "math_role": "quantity"},
                        {"name": "unit_price", "type": "number", "math_role": "unit_price"},
                        {"name": "line_total", "type": "number", "math_role": "line_total"},
                    ],
                },
            ]
        }

    def test_invoice_perfect_reconciliation(self, invoice_schema):
        data = {
            "InvoiceHeader": {
                "invoice_number": "INV-1001",
                "subtotal": 100.00,
                "tax_amount": 10.00,
                "grand_total": 110.00,
            },
            "LineItem": [
                {"item_desc": "Service A", "quantity": 2, "unit_price": 30.00, "line_total": 60.00},
                {"item_desc": "Service B", "quantity": 4, "unit_price": 10.00, "line_total": 40.00},
            ],
        }

        result = reconciler.reconcile(invoice_schema, data)
        assert result.reconciled is True
        assert result.has_discrepancy is False
        assert result.score == 1.0
        # 2 line checks (2x30=60, 4x10=40) + 1 subtotal check (60+40=100) + 1 grand total check (100+10=110) = 4 checks
        assert result.applicable_checks_count == 4
        assert result.passed_checks_count == 4

    def test_invoice_line_calculation_discrepancy(self, invoice_schema):
        # Line 0 math contradiction: 2 * 30.00 is 60.00, but stated as 65.00
        data = {
            "InvoiceHeader": {
                "invoice_number": "INV-1001",
                "subtotal": 105.00,
                "tax_amount": 10.00,
                "grand_total": 115.00,
            },
            "LineItem": [
                {"item_desc": "Service A", "quantity": 2, "unit_price": 30.00, "line_total": 65.00},  # Math error!
                {"item_desc": "Service B", "quantity": 4, "unit_price": 10.00, "line_total": 40.00},
            ],
        }

        result = reconciler.reconcile(invoice_schema, data)
        assert result.reconciled is False
        assert result.has_discrepancy is True
        assert result.passed_checks_count < result.applicable_checks_count
        assert any("LineItem[0]" in d.check_name and not d.passed for d in result.details)

    def test_invoice_subtotal_sum_discrepancy(self, invoice_schema):
        # Lines sum to 60 + 40 = 100, but subtotal states 150.00
        data = {
            "InvoiceHeader": {
                "invoice_number": "INV-1001",
                "subtotal": 150.00,  # Contradicts sum of lines (100.00)
                "tax_amount": 15.00,
                "grand_total": 165.00,
            },
            "LineItem": [
                {"item_desc": "Service A", "quantity": 2, "unit_price": 30.00, "line_total": 60.00},
                {"item_desc": "Service B", "quantity": 4, "unit_price": 10.00, "line_total": 40.00},
            ],
        }

        result = reconciler.reconcile(invoice_schema, data)
        assert result.reconciled is False
        assert result.has_discrepancy is True
        assert any("sum(LineItem.line_total)" in d.check_name and not d.passed for d in result.details)

    def test_invoice_grand_total_discrepancy(self, invoice_schema):
        # Subtotal 100 + Tax 10 = 110, but grand total states 200.00
        data = {
            "InvoiceHeader": {
                "invoice_number": "INV-1001",
                "subtotal": 100.00,
                "tax_amount": 10.00,
                "grand_total": 200.00,  # Contradicts 100 + 10 = 110
            },
            "LineItem": [
                {"item_desc": "Service A", "quantity": 2, "unit_price": 30.00, "line_total": 60.00},
                {"item_desc": "Service B", "quantity": 4, "unit_price": 10.00, "line_total": 40.00},
            ],
        }

        result = reconciler.reconcile(invoice_schema, data)
        assert result.reconciled is False
        assert result.has_discrepancy is True
        assert any("subtotal_plus_tax" in d.check_name and not d.passed for d in result.details)

    def test_heuristic_detection_without_explicit_math_roles(self):
        # Schema does not specify "math_role", only field names: qty, unit_price, amount, subtotal, tax, total
        schema = {
            "objects": [
                {
                    "type": "Receipt",
                    "multiple": False,
                    "fields": [
                        {"name": "subtotal", "type": "number"},
                        {"name": "vat", "type": "number"},
                        {"name": "total", "type": "number"},
                    ],
                },
                {
                    "type": "Items",
                    "multiple": True,
                    "fields": [
                        {"name": "qty", "type": "number"},
                        {"name": "rate", "type": "number"},
                        {"name": "amount", "type": "number"},
                    ],
                },
            ]
        }

        data = {
            "Receipt": {
                "subtotal": 50.00,
                "vat": 5.00,
                "total": 55.00,
            },
            "Items": [
                {"qty": "5", "rate": "10.00", "amount": "50.00"},
            ],
        }

        result = reconciler.reconcile(schema, data)
        assert result.reconciled is True
        assert result.has_discrepancy is False
        assert result.applicable_checks_count >= 3
        assert result.score == 1.0

    def test_time_range_vs_hours_roster(self):
        roster_schema = {
            "objects": [
                {
                    "type": "Shift",
                    "multiple": True,
                    "fields": [
                        {"name": "employee", "type": "string"},
                        {"name": "start_time", "type": "string", "math_role": "start_time"},
                        {"name": "end_time", "type": "string", "math_role": "end_time"},
                        {"name": "hours", "type": "number", "math_role": "hours"},
                    ],
                }
            ]
        }

        # 08:00 to 16:30 is 8.5 hours
        good_data = {
            "Shift": [
                {"employee": "Alice", "start_time": "08:00", "end_time": "16:30", "hours": 8.5},
            ]
        }
        res_good = reconciler.reconcile(roster_schema, good_data)
        assert res_good.reconciled is True
        assert res_good.score == 1.0

        # Discrepancy: 08:00 to 16:30 stated as 5.0 hours
        bad_data = {
            "Shift": [
                {"employee": "Alice", "start_time": "08:00", "end_time": "16:30", "hours": 5.0},
            ]
        }
        res_bad = reconciler.reconcile(roster_schema, bad_data)
        assert res_bad.reconciled is False
        assert res_bad.has_discrepancy is True
        assert any("time_range_vs_hours" in d.check_name for d in res_bad.details)

    def test_non_arithmetic_document(self):
        # A document without arithmetic fields should return reconciled=True, 0 checks, score 1.0
        doc_schema = {
            "objects": [
                {
                    "type": "Certificate",
                    "multiple": False,
                    "fields": [
                        {"name": "recipient_name", "type": "string"},
                        {"name": "issue_date", "type": "string"},
                    ],
                }
            ]
        }
        data = {
            "Certificate": {
                "recipient_name": "Jane Doe",
                "issue_date": "2026-01-01",
            }
        }
        res = reconciler.reconcile(doc_schema, data)
        assert res.reconciled is True
        assert res.applicable_checks_count == 0
        assert res.score == 1.0
        assert res.has_discrepancy is False
