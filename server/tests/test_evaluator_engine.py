"""
Tests for the Holistic Agent Evaluation Engine
"""
import pytest
from evaluation.evaluator import evaluate_document, generate_benchmark_report


def test_evaluator_perfect_match():
    gt = {
        "metadata": {"doc_id": "doc1", "category": "clean", "filename": "sample.jpeg"},
        "expected_extraction": {
            "location_no": "6566",
            "location_name": "Royal Courts",
            "date": "2026-07-21",
            "records": [
                {"employee_no": "76261", "name": "DMCK Dissanayake", "rank": "OIC"},
                {"employee_no": "76260", "name": "NMD Sampath Kumar", "rank": "SSO"},
            ],
        },
        "expected_agent_trajectory": {
            "expected_decision": "COMMIT",
            "max_acceptable_steps": 2,
        },
    }

    agent_output = {
        "location_no": "6566",
        "location_name": "Royal Courts",
        "date": "2026-07-21",
        "decision": "COMMIT",
        "steps_taken": 2,
        "records": [
            {"employee_no": "76261", "name": "DMCK Dissanayake", "rank": "OIC"},
            {"employee_no": "76260", "name": "NMD Sampath Kumar", "rank": "SSO"},
        ],
    }

    sc = evaluate_document(gt, agent_output)
    assert sc.passed is True
    assert sc.header_accuracy == 1.0
    assert sc.table_f1 == 1.0
    assert sc.decision_correct is True
    assert sc.step_budget_passed is True


def test_evaluator_adversarial_catch():
    """If ground truth expects SEND_TO_REVIEW and agent commits, it must fail decision check."""
    gt = {
        "metadata": {"doc_id": "doc_adv", "category": "adversarial", "filename": "adv.jpeg"},
        "expected_extraction": {"location_name": "Anasa Hotel", "records": []},
        "expected_agent_trajectory": {
            "expected_decision": "SEND_TO_REVIEW",
            "max_acceptable_steps": 2,
        },
    }

    # Hallucinating or naive agent blindly commits
    bad_agent_output = {
        "location_name": "Anasa Hotel",
        "decision": "COMMIT",
        "steps_taken": 2,
        "records": [],
    }

    sc = evaluate_document(gt, bad_agent_output)
    assert sc.passed is False
    assert sc.decision_correct is False
    assert any("Decision mismatch" in n for n in sc.notes)


def test_generate_report():
    gt = {
        "metadata": {"doc_id": "doc1", "category": "clean", "filename": "sample.jpeg"},
        "expected_extraction": {"location_name": "X", "records": []},
        "expected_agent_trajectory": {"expected_decision": "COMMIT", "max_acceptable_steps": 2},
    }
    pred = {"location_name": "X", "decision": "COMMIT", "steps_taken": 2, "records": []}
    sc = evaluate_document(gt, pred)
    rep = generate_benchmark_report([sc])
    assert rep["total_documents"] == 1
    assert rep["overall_pass_rate"] == 1.0
    assert "clean" in rep["category_breakdown"]
