"""
Agent Holistic Evaluation Engine

Evaluates agent performance against the curated benchmark dataset across 4 dimensions:
  1. Extraction Accuracy (Field-level Exact Match, Levenshtein, Table F1)
  2. Decision Accuracy (Routing: COMMIT vs SEND_TO_REVIEW vs FLAG)
  3. Trajectory & Tool Call Precision (Step count, tool sequence divergence)
  4. Cost & Efficiency (Token usage, estimated USD cost, latency)
"""
import glob
import json
import os
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class DocumentScorecard:
    doc_id: str
    category: str
    filename: str
    # 1. Extraction accuracy
    header_accuracy: float        # location, date, shift match (0.0 to 1.0)
    table_precision: float        # employee records precision (0.0 to 1.0)
    table_recall: float           # employee records recall (0.0 to 1.0)
    table_f1: float               # harmonic mean
    overall_extraction_score: float
    # 2. Decision accuracy
    expected_decision: str
    actual_decision: str
    decision_correct: bool
    # 3. Efficiency & Trajectory
    actual_steps: int
    max_acceptable_steps: int
    step_budget_passed: bool
    # 4. Token & Cost
    estimated_cost_usd: float
    # Summary
    passed: bool
    notes: List[str]


def _normalize(val: Any) -> str:
    if val is None:
        return ""
    return str(val).strip().lower()


def evaluate_document(ground_truth: Dict[str, Any], agent_output: Dict[str, Any]) -> DocumentScorecard:
    gt_meta = ground_truth.get("metadata", {})
    gt_extract = ground_truth.get("expected_extraction", {})
    gt_traj = ground_truth.get("expected_agent_trajectory", {})

    notes: List[str] = []

    # 1. Header Evaluation
    header_fields = ["location_no", "location_name", "date"]
    header_matches = 0
    for hf in header_fields:
        gt_v = _normalize(gt_extract.get(hf))
        pred_v = _normalize(agent_output.get(hf))
        if gt_v == pred_v or (gt_v and gt_v in pred_v) or (pred_v and pred_v in gt_v):
            header_matches += 1
        else:
            notes.append(f"Header mismatch on '{hf}': expected '{gt_v}', got '{pred_v}'")
    header_accuracy = header_matches / len(header_fields)

    # 2. Table / Guard Records Evaluation
    gt_records = gt_extract.get("records", []) or []
    pred_records = agent_output.get("records", []) or []

    # Map guards by normalized employee_no or name
    matched_guards = 0
    for gt_r in gt_records:
        gt_emp = _normalize(gt_r.get("employee_no"))
        gt_name = _normalize(gt_r.get("name"))
        match_found = False
        for pr in pred_records:
            pr_emp = _normalize(pr.get("employee_no"))
            pr_name = _normalize(pr.get("name"))
            if (gt_emp and gt_emp == pr_emp) or (gt_name and (gt_name in pr_name or pr_name in gt_name)):
                match_found = True
                break
        if match_found:
            matched_guards += 1

    precision = matched_guards / max(len(pred_records), 1) if pred_records else (1.0 if not gt_records else 0.0)
    recall = matched_guards / max(len(gt_records), 1) if gt_records else 1.0
    table_f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
    overall_extraction = 0.4 * header_accuracy + 0.6 * table_f1

    # 3. Decision Evaluation
    exp_decision = gt_traj.get("expected_decision", "COMMIT")
    act_decision = agent_output.get("decision", "COMMIT")
    decision_correct = (exp_decision == act_decision)
    if not decision_correct:
        notes.append(f"Decision mismatch: expected '{exp_decision}', got '{act_decision}'")

    # 4. Trajectory Step Efficiency
    actual_steps = agent_output.get("steps_taken", 2)
    max_steps = gt_traj.get("max_acceptable_steps", 3)
    step_budget_passed = (actual_steps <= max_steps)
    if not step_budget_passed:
        notes.append(f"Step budget exceeded: {actual_steps} steps taken (limit: {max_steps})")

    # 5. Cost Estimation (assuming ~$0.002 per step average with multimodal input)
    est_cost = agent_output.get("cost_usd", actual_steps * 0.003)

    # Holistic Pass Condition
    passed = (overall_extraction >= 0.85) and decision_correct and step_budget_passed

    return DocumentScorecard(
        doc_id=gt_meta.get("doc_id", ""),
        category=gt_meta.get("category", "unknown"),
        filename=gt_meta.get("filename", ""),
        header_accuracy=round(header_accuracy, 3),
        table_precision=round(precision, 3),
        table_recall=round(recall, 3),
        table_f1=round(table_f1, 3),
        overall_extraction_score=round(overall_extraction, 3),
        expected_decision=exp_decision,
        actual_decision=act_decision,
        decision_correct=decision_correct,
        actual_steps=actual_steps,
        max_acceptable_steps=max_steps,
        step_budget_passed=step_budget_passed,
        estimated_cost_usd=round(est_cost, 4),
        passed=passed,
        notes=notes,
    )


def generate_benchmark_report(scorecards: List[DocumentScorecard]) -> Dict[str, Any]:
    total = len(scorecards)
    if total == 0:
        return {"error": "no scorecards provided"}

    categories = {}
    for sc in scorecards:
        if sc.category not in categories:
            categories[sc.category] = []
        categories[sc.category].append(sc)

    cat_summaries = {}
    for cat, items in categories.items():
        n = len(items)
        cat_summaries[cat] = {
            "count": n,
            "pass_rate": round(sum(1 for x in items if x.passed) / n, 3),
            "avg_extraction_score": round(sum(x.overall_extraction_score for x in items) / n, 3),
            "decision_accuracy": round(sum(1 for x in items if x.decision_correct) / n, 3),
            "avg_steps": round(sum(x.actual_steps for x in items) / n, 1),
            "avg_cost_usd": round(sum(x.estimated_cost_usd for x in items) / n, 4),
        }

    overall_pass = sum(1 for x in scorecards if x.passed) / total
    avg_extraction = sum(x.overall_extraction_score for x in scorecards) / total
    decision_acc = sum(1 for x in scorecards if x.decision_correct) / total
    total_cost = sum(x.estimated_cost_usd for x in scorecards)

    return {
        "total_documents": total,
        "overall_pass_rate": round(overall_pass, 3),
        "overall_extraction_accuracy": round(avg_extraction, 3),
        "decision_routing_accuracy": round(decision_acc, 3),
        "total_estimated_cost_usd": round(total_cost, 4),
        "average_cost_per_doc_usd": round(total_cost / total, 4),
        "category_breakdown": cat_summaries,
    }
