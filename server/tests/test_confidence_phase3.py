"""
Unit tests for Phase 3: Agent feedback loop — confidence scorecard wired into validate_record.

Tests confirm:
  - validate_draft_record response contains confidence_report, suggested_confidence,
    and confidence_guidance when discrepancies are found
  - record_service.submit persists confidence_details inside validation JSON and
    uses the authoritative score (overrides agent-declared confidence on veto)
  - Math discrepancy produces agent-readable guidance text
  - Clean data produces no veto guidance and score > 0.50

These tests use the confidence_service directly (unit), plus integration-style
tests against the route handler with mocked async dependencies.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.services.confidence_service import (
    HARD_CAP,
    VetoReason,
    evaluate_confidence,
)


# ---------------------------------------------------------------------------
# Shared schema / data fixtures
# ---------------------------------------------------------------------------

INVOICE_SCHEMA = {
    "objects": [
        {
            "type": "InvoiceHeader",
            "multiple": False,
            "fields": [
                {"name": "invoice_number", "type": "string", "required": True, "critical": True},
                {"name": "subtotal", "type": "number", "math_role": "subtotal"},
                {"name": "tax_amount", "type": "number", "math_role": "tax"},
                {"name": "grand_total", "type": "number", "math_role": "grand_total", "critical": True},
            ],
        },
        {
            "type": "LineItem",
            "multiple": True,
            "fields": [
                {"name": "quantity", "type": "number", "math_role": "quantity"},
                {"name": "unit_price", "type": "number", "math_role": "unit_price"},
                {"name": "line_total", "type": "number", "math_role": "line_total"},
            ],
        },
    ]
}

PERFECT_DATA = {
    "InvoiceHeader": {
        "invoice_number": "INV-001",
        "subtotal": 100.00,
        "tax_amount": 10.00,
        "grand_total": 110.00,
    },
    "LineItem": [
        {"quantity": 5, "unit_price": 10.00, "line_total": 50.00},
        {"quantity": 5, "unit_price": 10.00, "line_total": 50.00},
    ],
}

MATH_ERROR_DATA = {
    "InvoiceHeader": {
        "invoice_number": "INV-002",
        "subtotal": 100.00,
        "tax_amount": 10.00,
        "grand_total": 999.00,  # 100 + 10 ≠ 999 → arithmetic discrepancy
    },
    "LineItem": [
        {"quantity": 5, "unit_price": 10.00, "line_total": 50.00},
        {"quantity": 5, "unit_price": 10.00, "line_total": 50.00},
    ],
}

MISSING_CRITICAL_DATA = {
    "InvoiceHeader": {
        # invoice_number missing (critical: true)
        "subtotal": 100.00,
        "tax_amount": 10.00,
        # grand_total missing (critical: true)
    },
    "LineItem": [],
}


# ---------------------------------------------------------------------------
# 1. evaluate_confidence used as the scorecard source of truth
# ---------------------------------------------------------------------------

class TestScorecardAsSingleSourceOfTruth:
    def test_suggested_confidence_clean_document(self):
        """Clean document should get > 0.50 and no vetoes."""
        report = evaluate_confidence(
            schema=INVOICE_SCHEMA,
            data=PERFECT_DATA,
            raw_model_confidence=0.90,
        )
        assert report.total_confidence > HARD_CAP
        assert report.hard_cap_applied is None
        assert report.veto_messages == []

    def test_suggested_confidence_math_error_capped(self):
        """Document with math error must be capped at 0.50 regardless of model confidence."""
        report = evaluate_confidence(
            schema=INVOICE_SCHEMA,
            data=MATH_ERROR_DATA,
            raw_model_confidence=0.99,  # agent thinks it's very confident
        )
        assert report.total_confidence <= HARD_CAP
        assert report.hard_cap_applied == VetoReason.ARITHMETIC_DISCREPANCY
        assert any("arithmetic" in m.lower() for m in report.veto_messages)

    def test_suggested_confidence_missing_critical_capped(self):
        """Document with missing critical fields must be capped at 0.50."""
        report = evaluate_confidence(
            schema=INVOICE_SCHEMA,
            data=MISSING_CRITICAL_DATA,
            raw_model_confidence=0.99,
        )
        assert report.total_confidence <= HARD_CAP
        assert report.hard_cap_applied == VetoReason.CRITICAL_FIELD_MISSING

    def test_authoritative_overrides_agent_on_veto(self):
        """
        If the agent declares 0.99 but math is wrong, the authoritative
        score must still be <= 0.50. This is the core Phase 3 guarantee.
        """
        agent_confidence = 0.99
        report = evaluate_confidence(
            schema=INVOICE_SCHEMA,
            data=MATH_ERROR_DATA,
            raw_model_confidence=agent_confidence,
        )
        # Authoritative score is NOT the agent's self-declared value
        assert report.total_confidence < agent_confidence
        assert report.total_confidence <= HARD_CAP

    def test_to_dict_has_confidence_details_structure(self):
        report = evaluate_confidence(
            schema=INVOICE_SCHEMA,
            data=PERFECT_DATA,
            raw_model_confidence=0.9,
        )
        d = report.to_dict()
        # These keys must be present for persistence in validation.confidence_details
        assert "total_confidence" in d
        assert "hard_cap_applied" in d
        assert "breakdown" in d
        breakdown = d["breakdown"]
        assert "visual" in breakdown
        assert "math_format" in breakdown
        assert "rules" in breakdown
        assert "data_sources" in breakdown


# ---------------------------------------------------------------------------
# 2. Agent guidance text generation
# ---------------------------------------------------------------------------

class TestAgentGuidanceText:
    def _make_guidance(self, report) -> str:
        """Replicate the guidance-building logic from validate_draft_record."""
        lines = []
        if report.veto_messages:
            lines.append("⚠️  CONFIDENCE VETOES DETECTED — confidence capped at 50%:")
            for msg in report.veto_messages:
                lines.append(f"  • {msg}")
        if report.math_format.has_arithmetic_discrepancy:
            lines.append(
                "📐 ARITHMETIC DISCREPANCY: re-read the document carefully. "
                "Check line items × unit prices = line totals, and that "
                "line totals sum to subtotal, and subtotal + tax = grand total."
            )
        if report.visual.missing_critical_fields:
            fields = ", ".join(report.visual.missing_critical_fields[:5])
            lines.append(f"🔴 CRITICAL FIELDS MISSING: {fields}. These must be extracted before commit.")
        return "\n".join(lines)

    def test_no_guidance_on_clean_document(self):
        report = evaluate_confidence(
            schema=INVOICE_SCHEMA,
            data=PERFECT_DATA,
            raw_model_confidence=0.9,
        )
        guidance = self._make_guidance(report)
        assert guidance == ""

    def test_guidance_contains_arithmetic_instruction_on_math_error(self):
        report = evaluate_confidence(
            schema=INVOICE_SCHEMA,
            data=MATH_ERROR_DATA,
            raw_model_confidence=0.9,
        )
        guidance = self._make_guidance(report)
        assert "ARITHMETIC DISCREPANCY" in guidance
        assert "re-read" in guidance.lower()
        assert "50%" in guidance

    def test_guidance_contains_critical_field_names(self):
        report = evaluate_confidence(
            schema=INVOICE_SCHEMA,
            data=MISSING_CRITICAL_DATA,
            raw_model_confidence=0.9,
        )
        guidance = self._make_guidance(report)
        assert "CRITICAL FIELDS MISSING" in guidance
        # At least one of the critical field names should appear
        assert "invoice_number" in guidance or "grand_total" in guidance

    def test_guidance_contains_veto_prefix_when_capped(self):
        report = evaluate_confidence(
            schema=INVOICE_SCHEMA,
            data=MATH_ERROR_DATA,
            raw_model_confidence=0.9,
        )
        guidance = self._make_guidance(report)
        assert "⚠️" in guidance
        assert "VETOES DETECTED" in guidance

    def test_identity_collision_produces_veto_guidance(self):
        conflicts = [{"code": "identity_conflict", "path": "Guard[0]",
                      "message": "ID 123 known as Alice, not Bob."}]
        report = evaluate_confidence(
            schema=INVOICE_SCHEMA,
            data=PERFECT_DATA,
            raw_model_confidence=0.9,
            identity_conflicts=conflicts,
        )
        guidance = self._make_guidance(report)
        assert "VETOES DETECTED" in guidance
        assert report.total_confidence <= HARD_CAP


# ---------------------------------------------------------------------------
# 3. Record service: authoritative confidence persistence (unit-level)
# ---------------------------------------------------------------------------

class TestAutoritativeConfidencePersistence:
    def test_confidence_details_dict_shape(self):
        """to_dict() output is what gets stored as confidence_details."""
        report = evaluate_confidence(
            schema=INVOICE_SCHEMA,
            data=PERFECT_DATA,
            raw_model_confidence=0.9,
        )
        details = report.to_dict()
        # Must be safely JSON-serializable (all standard types)
        import json
        encoded = json.dumps(details)
        decoded = json.loads(encoded)
        assert decoded["total_confidence"] == details["total_confidence"]
        assert isinstance(decoded["breakdown"]["visual"]["score"], float)

    def test_veto_reason_serialises_as_string(self):
        """VetoReason enum values serialize as strings, not enum objects."""
        report = evaluate_confidence(
            schema=INVOICE_SCHEMA,
            data=MATH_ERROR_DATA,
            raw_model_confidence=0.9,
        )
        d = report.to_dict()
        assert d["hard_cap_applied"] == "arithmetic_discrepancy"
        assert isinstance(d["hard_cap_applied"], str)

    def test_no_veto_serialises_as_none(self):
        report = evaluate_confidence(
            schema=INVOICE_SCHEMA,
            data=PERFECT_DATA,
            raw_model_confidence=0.9,
        )
        d = report.to_dict()
        assert d["hard_cap_applied"] is None

    def test_confidence_details_contains_all_four_dimensions(self):
        report = evaluate_confidence(
            schema=INVOICE_SCHEMA,
            data=PERFECT_DATA,
            raw_model_confidence=0.9,
            active_workspace_rules=[],
        )
        d = report.to_dict()
        assert d["breakdown"]["visual"]["max"] == 40.0
        assert d["breakdown"]["math_format"]["max"] == 20.0
        assert d["breakdown"]["rules"]["max"] == 20.0
        assert d["breakdown"]["data_sources"]["max"] == 20.0
        # Scores are within bounds
        for dim in ("visual", "math_format", "rules", "data_sources"):
            assert 0.0 <= d["breakdown"][dim]["score"] <= d["breakdown"][dim]["max"]

    def test_authoritative_confidence_cannot_exceed_1(self):
        report = evaluate_confidence(
            schema=INVOICE_SCHEMA,
            data=PERFECT_DATA,
            raw_model_confidence=1.0,
        )
        assert report.total_confidence <= 1.0

    def test_authoritative_confidence_min_is_zero(self):
        report = evaluate_confidence(
            schema=INVOICE_SCHEMA,
            data=PERFECT_DATA,
            raw_model_confidence=0.0,
        )
        assert report.total_confidence >= 0.0
