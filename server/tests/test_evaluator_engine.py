"""
Tests for the Holistic Agent Evaluation Engine.

Canonical final decisions are COMMIT, REVIEW, FAILED only.
FLAG is an intermediate tool call — NOT a valid final decision.
"""
import pytest
from evaluation.evaluator import evaluate_document, generate_benchmark_report


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_gt(
    doc_id="doc1",
    category="clean",
    expected_decision="COMMIT",
    max_steps=3,
    location_no="6566",
    location_name="Royal Courts",
    date="2026-07-21",
    records=None,
):
    if records is None:
        records = [
            {"employee_no": "76261", "name": "DMCK Dissanayake", "rank": "OIC"},
            {"employee_no": "76260", "name": "NMD Sampath Kumar", "rank": "SSO"},
        ]
    return {
        "metadata": {"doc_id": doc_id, "category": category, "filename": f"{doc_id}.jpeg"},
        "expected_extraction": {
            "location_no": location_no,
            "location_name": location_name,
            "date": date,
            "records": records,
        },
        "expected_agent_trajectory": {
            "expected_decision": expected_decision,
            "max_acceptable_steps": max_steps,
        },
    }


def _make_agent_output(
    decision="COMMIT",
    steps=2,
    location_no="6566",
    location_name="Royal Courts",
    date="2026-07-21",
    records=None,
):
    if records is None:
        records = [
            {"employee_no": "76261", "name": "DMCK Dissanayake", "rank": "OIC"},
            {"employee_no": "76260", "name": "NMD Sampath Kumar", "rank": "SSO"},
        ]
    return {
        "decision": decision,
        "steps_taken": steps,
        "location_no": location_no,
        "location_name": location_name,
        "date": date,
        "records": records,
    }


# ---------------------------------------------------------------------------
# Test 1: Perfect COMMIT — should PASS
# ---------------------------------------------------------------------------

def test_evaluator_perfect_commit():
    """Clean doc: agent commits with perfect extraction — must pass."""
    gt = _make_gt(expected_decision="COMMIT", max_steps=3)
    pred = _make_agent_output(decision="COMMIT", steps=2)

    sc = evaluate_document(gt, pred)

    assert sc.passed is True
    assert sc.header_accuracy == 1.0
    assert sc.table_f1 == 1.0
    assert sc.decision_correct is True
    assert sc.step_budget_passed is True
    assert sc.extraction_applicable is True
    assert sc.fail_reasons == []


# ---------------------------------------------------------------------------
# Test 2: COMMIT when REVIEW expected — must FAIL (safety check)
# ---------------------------------------------------------------------------

def test_commit_when_should_review_fails():
    """
    Agent commits on a document that should go to REVIEW.
    This is a critical safety failure — must be caught.
    """
    gt = _make_gt(
        doc_id="doc_adv",
        category="adversarial",
        expected_decision="REVIEW",
        max_steps=3,
        records=[],
    )
    bad_output = _make_agent_output(decision="COMMIT", steps=2, records=[])

    sc = evaluate_document(gt, bad_output)

    assert sc.passed is False
    assert sc.decision_correct is False
    assert any("Decision wrong" in r for r in sc.fail_reasons)


# ---------------------------------------------------------------------------
# Test 3: REVIEW — correct routing + adequate extraction — must PASS
# ---------------------------------------------------------------------------

def test_review_correct_routing_passes():
    """
    Agent correctly routes to REVIEW and achieves ≥ 0.60 extraction — must pass.
    """
    gt = _make_gt(
        doc_id="doc_review",
        category="adversarial",
        expected_decision="REVIEW",
        max_steps=3,
        records=[
            {"employee_no": "76261", "name": "DMCK Dissanayake", "rank": "OIC"},
        ],
    )
    pred = _make_agent_output(
        decision="REVIEW",
        steps=3,
        records=[
            {"employee_no": "76261", "name": "DMCK Dissanayake", "rank": "OIC"},
        ],
    )

    sc = evaluate_document(gt, pred)

    assert sc.decision_correct is True
    assert sc.overall_extraction_score >= 0.60
    assert sc.passed is True


# ---------------------------------------------------------------------------
# Test 4: FAILED document — correct routing — must PASS (no extraction check)
# ---------------------------------------------------------------------------

def test_failed_document_correct_routing():
    """
    Completely unreadable doc: agent calls mark_document_failed.
    Extraction is N/A — only decision + step budget matter.
    """
    gt = {
        "metadata": {"doc_id": "doc_failed", "category": "adversarial", "filename": "doc_failed.jpeg"},
        "expected_extraction": {
            "location_no": None,
            "location_name": None,
            "date": None,
            "records": [],
        },
        "expected_agent_trajectory": {
            "expected_decision": "FAILED",
            "max_acceptable_steps": 2,
        },
    }
    pred = {
        "decision": "FAILED",
        "steps_taken": 2,
        "records": [],
    }

    sc = evaluate_document(gt, pred)

    assert sc.extraction_applicable is False
    assert sc.decision_correct is True
    assert sc.step_budget_passed is True
    assert sc.passed is True
    # Extraction score is 0.0 for FAILED — that's correct and should NOT cause failure
    assert sc.overall_extraction_score == 0.0


# ---------------------------------------------------------------------------
# Test 5: Step budget exceeded — must FAIL
# ---------------------------------------------------------------------------

def test_step_budget_exceeded_fails():
    """Agent takes 6 steps on a 3-step budget — must fail."""
    gt = _make_gt(expected_decision="COMMIT", max_steps=3)
    pred = _make_agent_output(decision="COMMIT", steps=6)

    sc = evaluate_document(gt, pred)

    assert sc.step_budget_passed is False
    assert sc.passed is False
    assert any("Step budget exceeded" in r for r in sc.fail_reasons)


# ---------------------------------------------------------------------------
# Test 6: FLAG as final decision — must FAIL (FLAG is not a canonical outcome)
# ---------------------------------------------------------------------------

def test_flag_as_final_decision_fails():
    """
    Agent returns FLAG as its final decision — this is wrong.
    FLAG is an intermediate tool call; the final decision must be COMMIT/REVIEW/FAILED.
    """
    gt = _make_gt(expected_decision="COMMIT", max_steps=3)
    pred = _make_agent_output(decision="FLAG", steps=2)

    sc = evaluate_document(gt, pred)

    assert sc.decision_correct is False
    assert sc.passed is False
    assert any("Decision wrong" in r for r in sc.fail_reasons)


# ---------------------------------------------------------------------------
# Test 7: REVIEW aliases normalised correctly
# ---------------------------------------------------------------------------

def test_review_aliases_normalised():
    """SEND_TO_REVIEW, HUMAN_REVIEW etc. must all normalise to REVIEW."""
    gt = _make_gt(
        expected_decision="REVIEW",
        max_steps=3,
        records=[{"employee_no": "76261", "name": "DMCK Dissanayake", "rank": "OIC"}],
    )
    for alias in ("SEND_TO_REVIEW", "REVIEW_QUEUE", "HUMAN_REVIEW", "TO_REVIEW"):
        pred = _make_agent_output(
            decision=alias,
            steps=2,
            records=[{"employee_no": "76261", "name": "DMCK Dissanayake", "rank": "OIC"}],
        )
        sc = evaluate_document(gt, pred)
        assert sc.actual_decision == "REVIEW", f"Alias '{alias}' did not normalise to REVIEW"
        assert sc.decision_correct is True, f"Alias '{alias}' not recognised as correct REVIEW"


# ---------------------------------------------------------------------------
# Test 8: FAILED aliases normalised correctly
# ---------------------------------------------------------------------------

def test_failed_aliases_normalised():
    """MARK_DOCUMENT_FAILED, REJECT etc. must all normalise to FAILED."""
    gt = {
        "metadata": {"doc_id": "d", "category": "adversarial", "filename": "d.jpeg"},
        "expected_extraction": {"records": []},
        "expected_agent_trajectory": {"expected_decision": "FAILED", "max_acceptable_steps": 2},
    }
    for alias in ("MARK_DOCUMENT_FAILED", "FAIL", "REJECT", "REJECTED", "UNREADABLE"):
        pred = {"decision": alias, "steps_taken": 2, "records": []}
        sc = evaluate_document(gt, pred)
        assert sc.actual_decision == "FAILED", f"Alias '{alias}' did not normalise to FAILED"
        assert sc.decision_correct is True


# ---------------------------------------------------------------------------
# Test 9: generate_benchmark_report aggregation
# ---------------------------------------------------------------------------

def test_generate_report_aggregation():
    """Report should correctly aggregate multiple scorecards."""
    gt_commit = _make_gt(expected_decision="COMMIT", max_steps=3)
    gt_review = _make_gt(
        doc_id="doc2", category="adversarial", expected_decision="REVIEW", max_steps=3,
        records=[{"employee_no": "76261", "name": "DMCK Dissanayake", "rank": "OIC"}],
    )

    sc1 = evaluate_document(gt_commit, _make_agent_output(decision="COMMIT", steps=2))
    sc2 = evaluate_document(
        gt_review,
        _make_agent_output(
            decision="REVIEW", steps=3,
            records=[{"employee_no": "76261", "name": "DMCK Dissanayake", "rank": "OIC"}],
        ),
    )

    report = generate_benchmark_report([sc1, sc2])

    assert report["total_documents"] == 2
    assert report["decision_routing_accuracy"] == 1.0
    assert report["overall_pass_rate"] == 1.0
    assert "clean" in report["category_breakdown"]
    assert "adversarial" in report["category_breakdown"]
