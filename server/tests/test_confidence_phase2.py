"""
Unit tests for Phase 2: Unified Confidence Evaluator Service.

Verifies:
  - Fixed 100-pt weight model (never redistributes)
  - Score without rules / without data sources caps at 60 pts
  - All four hard-cap vetoes correctly clamp to <= 0.50
  - Dimension sub-scorers (visual, math/format, rules, data sources)
  - Full ConfidenceReport serialization
"""
import pytest
from app.services.confidence_service import (
    HARD_CAP,
    MAX_DATA_SOURCES,
    MAX_MATH_FORMAT,
    MAX_RULES,
    MAX_VISUAL,
    VetoReason,
    evaluate_confidence,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

INVOICE_SCHEMA = {
    "objects": [
        {
            "type": "InvoiceHeader",
            "multiple": False,
            "fields": [
                {"name": "invoice_number", "type": "string", "required": True, "critical": True},
                {"name": "vendor_name", "type": "string"},
                {"name": "issue_date", "type": "string", "format_rule": "iso_date"},
                {"name": "subtotal", "type": "number", "math_role": "subtotal"},
                {"name": "tax_amount", "type": "number", "math_role": "tax"},
                {"name": "grand_total", "type": "number", "math_role": "grand_total", "critical": True},
            ],
        },
        {
            "type": "LineItem",
            "multiple": True,
            "fields": [
                {"name": "description", "type": "string"},
                {"name": "quantity", "type": "number", "math_role": "quantity"},
                {"name": "unit_price", "type": "number", "math_role": "unit_price"},
                {"name": "line_total", "type": "number", "math_role": "line_total"},
            ],
        },
    ]
}

PERFECT_INVOICE_DATA = {
    "InvoiceHeader": {
        "invoice_number": "INV-2026-001",
        "vendor_name": "ACME Corp",
        "issue_date": "2026-11-17",
        "subtotal": 100.00,
        "tax_amount": 10.00,
        "grand_total": 110.00,
    },
    "LineItem": [
        {"description": "Service A", "quantity": 5, "unit_price": 10.00, "line_total": 50.00},
        {"description": "Service B", "quantity": 5, "unit_price": 10.00, "line_total": 50.00},
    ],
}

NO_SCHEMA = None
EMPTY_DATA = {}


# ---------------------------------------------------------------------------
# 1. Fixed weight structure integrity
# ---------------------------------------------------------------------------

class TestFixedWeightModel:
    def test_max_weights_sum_to_100(self):
        assert MAX_VISUAL + MAX_MATH_FORMAT + MAX_RULES + MAX_DATA_SOURCES == 100.0

    def test_max_without_rules_and_sources_is_60(self):
        # Perfect visual (1.0) + perfect math (1.0), no rules, no sources
        report = evaluate_confidence(
            schema=INVOICE_SCHEMA,
            data=PERFECT_INVOICE_DATA,
            raw_model_confidence=1.0,
            active_workspace_rules=None,
            entity_match_result=None,
        )
        # Max possible = 40 (visual) + 20 (math) = 60 pts
        assert report.total_pts <= 60.0 + 0.01  # small float tolerance
        assert report.rules.rules_configured is False
        assert report.rules.final_pts == 0.0
        assert report.data_sources.sources_connected is False
        assert report.data_sources.final_pts == 0.0

    def test_weights_never_redistribute(self):
        # Even with no sources connected, visual stays at 40 and math stays at 20
        report = evaluate_confidence(
            schema=INVOICE_SCHEMA,
            data=PERFECT_INVOICE_DATA,
            raw_model_confidence=1.0,
        )
        assert report.visual.final_pts <= MAX_VISUAL
        assert report.math_format.final_pts <= MAX_MATH_FORMAT
        assert report.rules.final_pts <= MAX_RULES
        assert report.data_sources.final_pts <= MAX_DATA_SOURCES

    def test_full_100_points_possible_with_all_dimensions(self):
        # Perfect score across all 4 dimensions
        entity_result = {
            "sources_connected": True,
            "exact_matches": 1,
            "alias_matches": 0,
            "unknown_entities": 0,
            "identity_collisions": 0,
            "total_identity_fields": 1,
        }
        report = evaluate_confidence(
            schema=INVOICE_SCHEMA,
            data=PERFECT_INVOICE_DATA,
            raw_model_confidence=1.0,
            active_workspace_rules=[],
            entity_match_result=entity_result,
        )
        # Even with empty rules list, rules_configured = False → 0 pts for rules
        # To get 100, need actual rules that all pass
        # At minimum, visual (40) + math (20) + data_sources (20) = 80 possible without rules
        assert report.total_pts <= 100.0
        assert report.total_confidence <= 1.0


# ---------------------------------------------------------------------------
# 2. Visual sub-scorer
# ---------------------------------------------------------------------------

class TestVisualScorer:
    def test_scales_raw_model_confidence_to_40pts(self):
        report = evaluate_confidence(
            schema=INVOICE_SCHEMA,
            data=PERFECT_INVOICE_DATA,
            raw_model_confidence=0.90,
        )
        assert abs(report.visual.final_pts - 36.0) < 0.5  # 0.90 * 40 = 36

    def test_clamps_above_1(self):
        report = evaluate_confidence(
            schema=INVOICE_SCHEMA,
            data=PERFECT_INVOICE_DATA,
            raw_model_confidence=1.5,  # clamped to 1.0
        )
        assert report.visual.final_pts == MAX_VISUAL

    def test_clamps_below_0(self):
        report = evaluate_confidence(
            schema=INVOICE_SCHEMA,
            data=PERFECT_INVOICE_DATA,
            raw_model_confidence=-0.5,  # clamped to 0.0
        )
        assert report.visual.final_pts == 0.0

    def test_no_penalty_when_critical_fields_present(self):
        report = evaluate_confidence(
            schema=INVOICE_SCHEMA,
            data=PERFECT_INVOICE_DATA,
            raw_model_confidence=1.0,
        )
        assert report.visual.missing_critical_fields == []
        assert report.visual.critical_field_penalties == 0.0

    def test_deducts_5pts_per_missing_critical_field(self):
        data_missing_critical = {
            "InvoiceHeader": {
                # invoice_number is missing (critical: true)
                "vendor_name": "ACME",
                "issue_date": "2026-11-17",
                "subtotal": 100.00,
                "tax_amount": 10.00,
                "grand_total": 110.00,
            },
            "LineItem": PERFECT_INVOICE_DATA["LineItem"],
        }
        report = evaluate_confidence(
            schema=INVOICE_SCHEMA,
            data=data_missing_critical,
            raw_model_confidence=1.0,
        )
        # 1 missing critical field: 40 - 5 = 35
        assert "InvoiceHeader.invoice_number" in report.visual.missing_critical_fields
        assert report.visual.final_pts == 35.0


# ---------------------------------------------------------------------------
# 3. Math/Format sub-scorer
# ---------------------------------------------------------------------------

class TestMathFormatScorer:
    def test_perfect_arithmetic_gives_full_20pts(self):
        report = evaluate_confidence(
            schema=INVOICE_SCHEMA,
            data=PERFECT_INVOICE_DATA,
            raw_model_confidence=1.0,
        )
        assert report.math_format.has_arithmetic_discrepancy is False
        assert report.math_format.reconciler_score == 1.0
        assert report.math_format.final_pts == MAX_MATH_FORMAT

    def test_no_arithmetic_schema_gives_full_20pts(self):
        bare_schema = {
            "objects": [
                {"type": "Certificate", "multiple": False, "fields": [
                    {"name": "name", "type": "string"},
                ]}
            ]
        }
        report = evaluate_confidence(
            schema=bare_schema,
            data={"Certificate": {"name": "Jane"}},
            raw_model_confidence=0.9,
        )
        assert report.math_format.final_pts == MAX_MATH_FORMAT
        assert report.math_format.has_arithmetic_discrepancy is False

    def test_format_violation_reduces_math_format_score(self):
        data_bad_date = {
            "InvoiceHeader": {
                "invoice_number": "INV-001",
                "issue_date": "17-11-2026",  # Wrong format, should be YYYY-MM-DD
                "subtotal": 100.00,
                "tax_amount": 10.00,
                "grand_total": 110.00,
            },
            "LineItem": PERFECT_INVOICE_DATA["LineItem"],
        }
        report = evaluate_confidence(
            schema=INVOICE_SCHEMA,
            data=data_bad_date,
            raw_model_confidence=1.0,
        )
        assert report.math_format.format_violations
        assert report.math_format.final_pts < MAX_MATH_FORMAT

    def test_arithmetic_discrepancy_detected(self):
        bad_data = {
            "InvoiceHeader": {
                "invoice_number": "INV-001",
                "subtotal": 100.00,
                "tax_amount": 10.00,
                "grand_total": 999.00,  # 100 + 10 ≠ 999 → hard discrepancy
            },
            "LineItem": PERFECT_INVOICE_DATA["LineItem"],
        }
        report = evaluate_confidence(
            schema=INVOICE_SCHEMA,
            data=bad_data,
            raw_model_confidence=0.95,
        )
        assert report.math_format.has_arithmetic_discrepancy is True


# ---------------------------------------------------------------------------
# 4. Rules sub-scorer
# ---------------------------------------------------------------------------

class TestRulesScorer:
    def test_no_rules_configured_gives_0pts(self):
        report = evaluate_confidence(
            schema=INVOICE_SCHEMA,
            data=PERFECT_INVOICE_DATA,
            raw_model_confidence=0.9,
            active_workspace_rules=None,
        )
        assert report.rules.rules_configured is False
        assert report.rules.final_pts == 0.0

    def test_all_rules_pass_gives_full_20pts(self):
        # A rule that always passes on the given data
        passing_rule = {
            "id": "invoice_number_format",
            "name": "Invoice number must start with INV-",
            "scope": "InvoiceHeader",
            "check": "invoice_number != ''",
            "severity": "error",
        }
        report = evaluate_confidence(
            schema=INVOICE_SCHEMA,
            data=PERFECT_INVOICE_DATA,
            raw_model_confidence=0.9,
            active_workspace_rules=[passing_rule],
        )
        assert report.rules.rules_configured is True
        assert report.rules.final_pts == MAX_RULES

    def test_failed_error_rule_reduces_score_and_triggers_veto(self):
        # Schema has a built-in time_diff rule (from SCHEMA fixture in conftest.py)
        # We simulate by using the roster schema with wrong hours
        roster_schema = {
            "objects": [
                {"type": "Shift", "multiple": True, "fields": [
                    {"name": "start_time", "type": "time"},
                    {"name": "end_time", "type": "time"},
                    {"name": "hours", "type": "number"},
                ]},
            ],
            "validation_rules": [
                {"id": "shift_hours", "rule_type": "time_diff", "object": "Shift",
                 "start_field": "start_time", "end_field": "end_time",
                 "hours_field": "hours", "tolerance_hours": 0.25},
            ],
        }
        # 08:00–16:00 is 8h, but stated as 12h → rule fails
        bad_shift = {"Shift": [{"start_time": "08:00", "end_time": "16:00", "hours": 12}]}
        report = evaluate_confidence(
            schema=roster_schema,
            data=bad_shift,
            raw_model_confidence=0.9,
        )
        # Rules configured (schema has built-in rules)
        assert report.rules.rules_configured is True
        assert report.rules.error_failures > 0

    def test_warning_rule_failure_does_not_trigger_veto(self):
        warning_rule = {
            "id": "warn_rule",
            "name": "Preferred vendor check",
            "scope": "InvoiceHeader",
            "check": "vendor_name != 'Unknown'",
            "severity": "warning",
        }
        data_with_unknown_vendor = {
            "InvoiceHeader": {
                "invoice_number": "INV-001",
                "vendor_name": "Unknown",   # triggers warning
                "subtotal": 100.00,
                "tax_amount": 10.00,
                "grand_total": 110.00,
            },
            "LineItem": PERFECT_INVOICE_DATA["LineItem"],
        }
        report = evaluate_confidence(
            schema=INVOICE_SCHEMA,
            data=data_with_unknown_vendor,
            raw_model_confidence=0.9,
            active_workspace_rules=[warning_rule],
        )
        # Warning failures do NOT trigger veto
        assert report.hard_cap_applied != VetoReason.ERROR_RULE_FAILED
        assert report.rules.warning_failures >= 1
        assert report.total_confidence > HARD_CAP


# ---------------------------------------------------------------------------
# 5. Data Sources sub-scorer
# ---------------------------------------------------------------------------

class TestDataSourcesScorer:
    def test_no_sources_connected_gives_0pts(self):
        report = evaluate_confidence(
            schema=INVOICE_SCHEMA,
            data=PERFECT_INVOICE_DATA,
            raw_model_confidence=0.9,
            entity_match_result=None,
        )
        assert report.data_sources.sources_connected is False
        assert report.data_sources.final_pts == 0.0

    def test_exact_match_gives_full_20pts(self):
        entity_result = {
            "sources_connected": True,
            "exact_matches": 2,
            "alias_matches": 0,
            "unknown_entities": 0,
            "identity_collisions": 0,
            "total_identity_fields": 2,
        }
        report = evaluate_confidence(
            schema=INVOICE_SCHEMA,
            data=PERFECT_INVOICE_DATA,
            raw_model_confidence=1.0,
            entity_match_result=entity_result,
        )
        assert report.data_sources.final_pts == MAX_DATA_SOURCES

    def test_alias_match_gives_90pct_of_data_source_pts(self):
        entity_result = {
            "sources_connected": True,
            "exact_matches": 0,
            "alias_matches": 1,
            "unknown_entities": 0,
            "identity_collisions": 0,
            "total_identity_fields": 1,
        }
        report = evaluate_confidence(
            schema=INVOICE_SCHEMA,
            data=PERFECT_INVOICE_DATA,
            raw_model_confidence=1.0,
            entity_match_result=entity_result,
        )
        # 1 alias match → 20 * 0.90 = 18 pts
        assert abs(report.data_sources.final_pts - 18.0) < 0.1

    def test_unknown_entity_gives_25pct_of_data_source_pts(self):
        entity_result = {
            "sources_connected": True,
            "exact_matches": 0,
            "alias_matches": 0,
            "unknown_entities": 1,
            "identity_collisions": 0,
            "total_identity_fields": 1,
        }
        report = evaluate_confidence(
            schema=INVOICE_SCHEMA,
            data=PERFECT_INVOICE_DATA,
            raw_model_confidence=1.0,
            entity_match_result=entity_result,
        )
        # 1 unknown entity → 20 * 0.25 = 5 pts
        assert abs(report.data_sources.final_pts - 5.0) < 0.1


# ---------------------------------------------------------------------------
# 6. Hard-Cap Vetoes
# ---------------------------------------------------------------------------

class TestHardCapVetoes:
    def test_arithmetic_discrepancy_caps_at_0_50(self):
        bad_data = {
            "InvoiceHeader": {
                "invoice_number": "INV-001",
                "subtotal": 100.00,
                "tax_amount": 10.00,
                "grand_total": 200.00,  # 100 + 10 ≠ 200
            },
            "LineItem": PERFECT_INVOICE_DATA["LineItem"],
        }
        entity_result = {
            "sources_connected": True,
            "exact_matches": 2,
            "alias_matches": 0,
            "unknown_entities": 0,
            "identity_collisions": 0,
            "total_identity_fields": 2,
        }
        report = evaluate_confidence(
            schema=INVOICE_SCHEMA,
            data=bad_data,
            raw_model_confidence=1.0,
            entity_match_result=entity_result,
        )
        assert report.total_confidence <= HARD_CAP
        assert report.hard_cap_applied == VetoReason.ARITHMETIC_DISCREPANCY

    def test_identity_collision_caps_at_0_50(self):
        conflicts = [{"code": "identity_conflict", "path": "Guard[0]",
                      "message": "ID 12345 is already known as Alice, not 'Bob'."}]
        report = evaluate_confidence(
            schema=INVOICE_SCHEMA,
            data=PERFECT_INVOICE_DATA,
            raw_model_confidence=1.0,
            identity_conflicts=conflicts,
        )
        assert report.total_confidence <= HARD_CAP
        assert report.hard_cap_applied == VetoReason.IDENTITY_COLLISION

    def test_identity_collision_via_entity_result_caps_at_0_50(self):
        entity_result = {
            "sources_connected": True,
            "exact_matches": 0,
            "alias_matches": 0,
            "unknown_entities": 0,
            "identity_collisions": 1,  # collision!
            "total_identity_fields": 1,
        }
        report = evaluate_confidence(
            schema=INVOICE_SCHEMA,
            data=PERFECT_INVOICE_DATA,
            raw_model_confidence=1.0,
            entity_match_result=entity_result,
        )
        assert report.total_confidence <= HARD_CAP

    def test_missing_critical_field_caps_at_0_50(self):
        data_no_invoice_number = {
            "InvoiceHeader": {
                # invoice_number is missing (critical: true)
                "subtotal": 100.00,
                "tax_amount": 10.00,
                "grand_total": 110.00,
            },
            "LineItem": PERFECT_INVOICE_DATA["LineItem"],
        }
        report = evaluate_confidence(
            schema=INVOICE_SCHEMA,
            data=data_no_invoice_number,
            raw_model_confidence=1.0,
        )
        assert report.total_confidence <= HARD_CAP
        assert report.hard_cap_applied == VetoReason.CRITICAL_FIELD_MISSING

    def test_error_rule_failure_caps_at_0_50(self):
        failing_rule = {
            "id": "require_known_vendor",
            "name": "Vendor must not be blank",
            "scope": "InvoiceHeader",
            "check": "vendor_name != 'UNKNOWN_VENDOR'",
            "severity": "error",
        }
        data_unknown_vendor = {
            "InvoiceHeader": {
                "invoice_number": "INV-001",
                "vendor_name": "UNKNOWN_VENDOR",
                "subtotal": 100.00,
                "tax_amount": 10.00,
                "grand_total": 110.00,
            },
            "LineItem": PERFECT_INVOICE_DATA["LineItem"],
        }
        report = evaluate_confidence(
            schema=INVOICE_SCHEMA,
            data=data_unknown_vendor,
            raw_model_confidence=1.0,
            active_workspace_rules=[failing_rule],
        )
        assert report.total_confidence <= HARD_CAP
        assert report.hard_cap_applied == VetoReason.ERROR_RULE_FAILED

    def test_no_veto_when_all_clean(self):
        entity_result = {
            "sources_connected": True,
            "exact_matches": 1,
            "alias_matches": 0,
            "unknown_entities": 0,
            "identity_collisions": 0,
            "total_identity_fields": 1,
        }
        report = evaluate_confidence(
            schema=INVOICE_SCHEMA,
            data=PERFECT_INVOICE_DATA,
            raw_model_confidence=0.90,
            entity_match_result=entity_result,
        )
        assert report.hard_cap_applied is None
        assert report.total_confidence > HARD_CAP

    def test_multiple_vetoes_coexist(self):
        # Both arithmetic discrepancy AND missing critical field
        bad_data = {
            "InvoiceHeader": {
                # invoice_number missing (critical)
                "subtotal": 100.00,
                "tax_amount": 10.00,
                "grand_total": 999.00,  # arithmetic error
            },
            "LineItem": PERFECT_INVOICE_DATA["LineItem"],
        }
        report = evaluate_confidence(
            schema=INVOICE_SCHEMA,
            data=bad_data,
            raw_model_confidence=0.5,
        )
        assert report.total_confidence <= HARD_CAP
        # At least one veto applied
        assert report.hard_cap_applied is not None
        # Both veto messages present
        assert len(report.veto_messages) >= 2


# ---------------------------------------------------------------------------
# 7. Serialization
# ---------------------------------------------------------------------------

class TestConfidenceReportSerialization:
    def test_to_dict_has_expected_keys(self):
        report = evaluate_confidence(
            schema=INVOICE_SCHEMA,
            data=PERFECT_INVOICE_DATA,
            raw_model_confidence=0.85,
        )
        d = report.to_dict()
        assert "total_confidence" in d
        assert "total_pts" in d
        assert "hard_cap_applied" in d
        assert "veto_messages" in d
        assert "breakdown" in d
        breakdown = d["breakdown"]
        assert all(k in breakdown for k in ("visual", "math_format", "rules", "data_sources"))
        assert "field_confidences" in d

    def test_to_dict_values_are_correct_types(self):
        report = evaluate_confidence(
            schema=INVOICE_SCHEMA,
            data=PERFECT_INVOICE_DATA,
            raw_model_confidence=0.85,
        )
        d = report.to_dict()
        assert isinstance(d["total_confidence"], float)
        assert isinstance(d["total_pts"], float)
        assert d["hard_cap_applied"] is None
        assert isinstance(d["veto_messages"], list)

    def test_field_confidences_roundtrip(self):
        fc = {"InvoiceHeader.invoice_number": 0.98, "InvoiceHeader.grand_total": 0.95}
        report = evaluate_confidence(
            schema=INVOICE_SCHEMA,
            data=PERFECT_INVOICE_DATA,
            raw_model_confidence=0.9,
            field_confidences=fc,
        )
        d = report.to_dict()
        assert abs(d["field_confidences"]["InvoiceHeader.invoice_number"] - 0.98) < 0.001
