"""
Agent Holistic Evaluation Engine

Evaluates agent performance against the curated benchmark dataset across 4 dimensions:
  1. Extraction Accuracy (Field-level match, Table Precision/Recall/F1)
  2. Decision Accuracy (Routing: COMMIT vs REVIEW vs FAILED — the only 3 canonical outcomes)
  3. Trajectory & Tool Call Precision (Step count vs budget)
  4. Cost & Efficiency (Token usage, estimated USD cost)

NOTE on FLAG: FLAG is an intermediate tool call, not a final decision. The agent may
call flag/ask_question during processing, but its FINAL outcome must be one of:
  COMMIT, REVIEW, or FAILED. This evaluator tests FINAL outcomes only.
"""
import json
import os
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional


@dataclass
class DocumentScorecard:
    doc_id: str
    category: str
    filename: str
    # 1. Extraction accuracy (N/A for FAILED docs)
    header_accuracy: float          # location, date, shift match (0.0–1.0)
    table_precision: float          # guard records precision
    table_recall: float             # guard records recall
    table_f1: float                 # harmonic mean of precision & recall
    overall_extraction_score: float
    extraction_applicable: bool     # False for FAILED docs
    # 2. Decision accuracy
    expected_decision: str          # COMMIT | REVIEW | FAILED
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
    fail_reasons: List[str]         # human-readable list of why it failed


def _normalize(val: Any) -> str:
    if val is None:
        return ""
    return str(val).strip().lower()


def _normalize_decision(d: Any) -> str:
    """
    Map any decision variant to one of the 3 canonical final outcomes:
      COMMIT, REVIEW, FAILED

    FLAG is intentionally NOT a valid canonical final decision.
    If an agent returns FLAG as its final decision, it is treated as UNKNOWN
    and will fail the decision check.
    """
    s = str(d or "").strip().upper()

    # COMMIT aliases
    if s in ("COMMIT", "COMMITTED", "APPROVE", "APPROVED"):
        return "COMMIT"

    # REVIEW aliases
    if s in (
        "REVIEW", "SEND_TO_REVIEW", "REVIEW_QUEUE", "HUMAN_REVIEW",
        "TO_REVIEW", "ESCALATE", "UNCERTAIN",
    ):
        return "REVIEW"

    # FAILED aliases
    if s in (
        "FAILED", "FAIL", "MARK_DOCUMENT_FAILED", "REJECT",
        "REJECTED", "UNREADABLE", "CORRUPT",
    ):
        return "FAILED"

    # Anything else (including FLAG, ASK, FLAGGED, etc.) → UNKNOWN
    # This will cause decision_correct = False, which is intentional.
    return f"UNKNOWN({s})"


def evaluate_document(
    ground_truth: Dict[str, Any],
    agent_output: Dict[str, Any],
) -> "DocumentScorecard":
    gt_meta = ground_truth.get("metadata", {})
    gt_extract = ground_truth.get("expected_extraction", {})
    gt_traj = ground_truth.get("expected_agent_trajectory", {})

    fail_reasons: List[str] = []

    # ----------------------------------------------------------------
    # 1. Decision evaluation (most important — checked first)
    # ----------------------------------------------------------------
    exp_decision_raw = gt_traj.get("expected_decision", "COMMIT")
    act_decision_raw = agent_output.get("decision", "")
    exp_decision = _normalize_decision(exp_decision_raw)
    act_decision = _normalize_decision(act_decision_raw)

    decision_correct = (exp_decision == act_decision)
    if not decision_correct:
        fail_reasons.append(
            f"Decision wrong: expected '{exp_decision}', got '{act_decision}' "
            f"(raw agent value: '{act_decision_raw}')"
        )

    # ----------------------------------------------------------------
    # 2. Extraction accuracy (skip for FAILED docs — not applicable)
    # ----------------------------------------------------------------
    extraction_applicable = (exp_decision != "FAILED")

    if extraction_applicable:
        # Header fields
        header_fields = ["location_no", "location_name", "date"]
        header_matches = 0
        for hf in header_fields:
            gt_v = _normalize(gt_extract.get(hf))
            pred_v = _normalize(agent_output.get(hf))
            if gt_v and pred_v and (gt_v == pred_v or gt_v in pred_v or pred_v in gt_v):
                header_matches += 1
            elif not gt_v:
                # GT doesn't have the field — neutral (don't penalise)
                header_matches += 1
            else:
                fail_reasons.append(
                    f"Header mismatch '{hf}': expected '{gt_v}', got '{pred_v}'"
                )
        header_accuracy = header_matches / len(header_fields)

        # Guard records (table)
        gt_records = gt_extract.get("records", []) or []
        pred_records = agent_output.get("records", []) or []

        matched = 0
        for gt_r in gt_records:
            gt_emp = _normalize(gt_r.get("employee_no"))
            gt_name = _normalize(gt_r.get("name"))
            for pr in pred_records:
                pr_emp = _normalize(pr.get("employee_no"))
                pr_name = _normalize(pr.get("name"))
                if (gt_emp and gt_emp == pr_emp) or (
                    gt_name and pr_name and (gt_name in pr_name or pr_name in gt_name)
                ):
                    matched += 1
                    break

        precision = matched / max(len(pred_records), 1) if pred_records else (1.0 if not gt_records else 0.0)
        recall = matched / max(len(gt_records), 1) if gt_records else 1.0
        table_f1 = (
            (2 * precision * recall / (precision + recall))
            if (precision + recall) > 0
            else 0.0
        )
        overall_extraction = 0.4 * header_accuracy + 0.6 * table_f1
    else:
        # FAILED docs: extraction not evaluated
        header_accuracy = 0.0
        precision = 0.0
        recall = 0.0
        table_f1 = 0.0
        overall_extraction = 0.0

    # ----------------------------------------------------------------
    # 3. Step efficiency
    # ----------------------------------------------------------------
    actual_steps = agent_output.get("steps_taken", 2)
    max_steps = gt_traj.get("max_acceptable_steps", 3)
    step_budget_passed = actual_steps <= max_steps
    if not step_budget_passed:
        fail_reasons.append(
            f"Step budget exceeded: used {actual_steps} steps (limit: {max_steps})"
        )

    # ----------------------------------------------------------------
    # 4. Cost estimation (~$0.0028/step average with multimodal input)
    # ----------------------------------------------------------------
    est_cost = agent_output.get("cost_usd", actual_steps * 0.0028)

    # ----------------------------------------------------------------
    # 5. Holistic pass condition — per decision type
    # ----------------------------------------------------------------
    if exp_decision == "FAILED":
        # Completely unreadable: only test correct routing + step budget
        # Extraction score is irrelevant
        passed = decision_correct and step_budget_passed

    elif exp_decision == "REVIEW":
        # Correct routing is primary.
        # Extraction threshold 0.60 — agent should extract whatever IS readable.
        # (Lower threshold than COMMIT because some fields are intentionally uncertain.)
        extraction_ok = (overall_extraction >= 0.60) if extraction_applicable else True
        passed = decision_correct and extraction_ok and step_budget_passed
        if decision_correct and not extraction_ok:
            fail_reasons.append(
                f"Extraction below REVIEW threshold: {overall_extraction:.2f} < 0.60"
            )

    else:  # COMMIT
        # High confidence required: extraction ≥ 0.85 AND correct routing AND budget
        extraction_ok = overall_extraction >= 0.85
        passed = decision_correct and extraction_ok and step_budget_passed
        if decision_correct and not extraction_ok:
            fail_reasons.append(
                f"Extraction below COMMIT threshold: {overall_extraction:.2f} < 0.85"
            )

    return DocumentScorecard(
        doc_id=gt_meta.get("doc_id", ""),
        category=gt_meta.get("category", "unknown"),
        filename=gt_meta.get("filename", ""),
        header_accuracy=round(header_accuracy, 3),
        table_precision=round(precision, 3),
        table_recall=round(recall, 3),
        table_f1=round(table_f1, 3),
        overall_extraction_score=round(overall_extraction, 3),
        extraction_applicable=extraction_applicable,
        expected_decision=exp_decision,
        actual_decision=act_decision,
        decision_correct=decision_correct,
        actual_steps=actual_steps,
        max_acceptable_steps=max_steps,
        step_budget_passed=step_budget_passed,
        estimated_cost_usd=round(est_cost, 4),
        passed=passed,
        fail_reasons=fail_reasons,
    )


def generate_benchmark_report(scorecards: List["DocumentScorecard"]) -> Dict[str, Any]:
    total = len(scorecards)
    if total == 0:
        return {"error": "no scorecards provided"}

    categories: Dict[str, List["DocumentScorecard"]] = {}
    for sc in scorecards:
        categories.setdefault(sc.category, []).append(sc)

    cat_summaries: Dict[str, Any] = {}
    for cat, items in categories.items():
        n = len(items)
        # Extraction score only meaningful for COMMIT/REVIEW docs
        extraction_items = [x for x in items if x.extraction_applicable]
        avg_ext = (
            sum(x.overall_extraction_score for x in extraction_items) / len(extraction_items)
            if extraction_items
            else 0.0
        )
        cat_summaries[cat] = {
            "count": n,
            "pass_rate": round(sum(1 for x in items if x.passed) / n, 3),
            "avg_extraction_score": round(avg_ext, 3),
            "decision_accuracy": round(sum(1 for x in items if x.decision_correct) / n, 3),
            "avg_steps": round(sum(x.actual_steps for x in items) / n, 1),
            "avg_cost_usd": round(sum(x.estimated_cost_usd for x in items) / n, 4),
        }

    overall_pass = sum(1 for x in scorecards if x.passed) / total
    all_extraction_items = [x for x in scorecards if x.extraction_applicable]
    avg_extraction = (
        sum(x.overall_extraction_score for x in all_extraction_items) / len(all_extraction_items)
        if all_extraction_items
        else 0.0
    )
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
