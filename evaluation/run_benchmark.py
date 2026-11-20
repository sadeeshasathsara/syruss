"""
Benchmark Runner CLI

Scores real agent outputs from a JSONL results file against the curated benchmark dataset
located in `dataset/evaluation_benchmark/`.

IMPORTANT — this runner does NOT simulate agent behaviour. To get a valid score you
MUST provide a `--results-file` JSONL file produced by an actual agent run. Each line
in that file must be a JSON object with these fields:

    {
        "filename":    "<image filename, e.g. 94704599305_20260722_154026_...jpeg>",
        "decision":    "COMMIT" | "REVIEW" | "FAILED",
        "location_no": "<str or null>",
        "location_name": "<str or null>",
        "date":        "<str or null>",
        "records":     [ { "employee_no": "...", "name": "...", "rank": "..." }, ... ],
        "steps_taken": <int>,
        "cost_usd":    <float, optional>
    }

Usage:
    # Show benchmark spec (decisions, rationale, distribution) — no agent output needed:
    python evaluation/run_benchmark.py --mode spec

    # Score a real agent JSONL output file:
    python evaluation/run_benchmark.py --results-file path/to/agent_outputs.jsonl

    # Score only one category:
    python evaluation/run_benchmark.py --results-file ... --category adversarial

    # Limit to first N documents:
    python evaluation/run_benchmark.py --results-file ... --limit 10
"""
import argparse
import glob
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from evaluation.evaluator import DocumentScorecard, evaluate_document, generate_benchmark_report


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_ground_truths(
    benchmark_dir: str, category: str = "all", limit: int = 0
) -> List[Dict[str, Any]]:
    manifest_path = os.path.join(benchmark_dir, "benchmark_manifest.json")
    ground_truths: List[Dict[str, Any]] = []

    if os.path.exists(manifest_path):
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        for doc_entry in manifest.get("documents", []):
            cat = doc_entry.get("category", "")
            if category != "all" and cat != category:
                continue
            gt_path = os.path.join(benchmark_dir, doc_entry.get("relative_gt_path", ""))
            if os.path.exists(gt_path):
                with open(gt_path, "r", encoding="utf-8") as gf:
                    ground_truths.append(json.load(gf))
    else:
        # Fallback: glob
        pattern = os.path.join(benchmark_dir, "**", "*.ground_truth.json")
        for p in glob.glob(pattern, recursive=True):
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
                cat = d.get("metadata", {}).get("category", "")
                if category == "all" or cat == category:
                    ground_truths.append(d)

    if limit > 0:
        ground_truths = ground_truths[:limit]
    return ground_truths


def load_agent_outputs(results_file: str) -> Dict[str, Dict[str, Any]]:
    outputs: Dict[str, Dict[str, Any]] = {}
    with open(results_file, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"[WARN] Skipping malformed JSON on line {lineno}: {e}")
                continue
            fn = item.get("filename")
            if fn:
                outputs[fn] = item
            else:
                print(f"[WARN] Line {lineno} missing 'filename' field — skipped.")
    return outputs


def _col(col: str, w: int) -> str:
    return col.ljust(w)[:w]


def print_table_row(cols: List[str], widths: List[int]) -> None:
    print("| " + " | ".join(_col(c, w) for c, w in zip(cols, widths)) + " |")


def print_separator(widths: List[int]) -> None:
    print("+-" + "-+-".join("-" * w for w in widths) + "-+")


# ---------------------------------------------------------------------------
# Mode: spec — print benchmark spec without evaluating
# ---------------------------------------------------------------------------

def show_spec(benchmark_dir: str, category: str = "all") -> None:
    manifest_path = os.path.join(benchmark_dir, "benchmark_manifest.json")
    if not os.path.exists(manifest_path):
        print(f"[!] Benchmark manifest not found at: {manifest_path}")
        print("    Run:  python agentic_framework/curate_benchmark.py  to generate it.")
        return

    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    print("\n=====================================================")
    print("   WATCHGUARD AGENT BENCHMARK — SPECIFICATION VIEW  ")
    print("=====================================================")
    print(f"Dataset: {benchmark_dir}")
    print(f"Total documents : {manifest.get('total_documents', '?')}")

    dist = manifest.get("decision_distribution", {})
    print(f"Decision dist   : COMMIT={dist.get('COMMIT','?')}  "
          f"REVIEW={dist.get('REVIEW','?')}  FAILED={dist.get('FAILED','?')}")
    print()

    for cat_name, cat_info in manifest.get("categories", {}).items():
        if category != "all" and cat_name != category:
            continue
        print(f"  [{cat_name.upper()}]  {cat_info.get('target', '')}")
        print(f"   Count: {cat_info.get('count')}  |  "
              f"COMMIT: {cat_info.get('commit', '?')}  "
              f"REVIEW: {cat_info.get('review', '?')}  "
              f"FAILED: {cat_info.get('failed', '?')}")
        print()

    print("--- Per-document expected decisions ---")
    headers = ["Category", "Filename (prefix)", "Decision", "Max Steps", "Rationale (truncated)"]
    widths   = [14, 30, 8, 10, 60]
    print_separator(widths)
    print_table_row(headers, widths)
    print_separator(widths)

    for doc in manifest.get("documents", []):
        if category != "all" and doc.get("category") != category:
            continue
        fn_short = doc.get("filename", "")[:27] + "..."
        rationale = doc.get("decision_rationale", "")
        print_table_row([
            doc.get("category", ""),
            fn_short,
            doc.get("expected_decision", ""),
            str(doc.get("max_acceptable_steps", "?")),
            rationale[:58],
        ], widths)

    print_separator(widths)
    print("\nTo run evaluation against real agent outputs:")
    print("  python evaluation/run_benchmark.py --results-file <path/to/agent_outputs.jsonl>\n")


# ---------------------------------------------------------------------------
# Mode: evaluate — score real agent JSONL against ground truth
# ---------------------------------------------------------------------------

def run_evaluation(
    benchmark_dir: str,
    results_file: str,
    category: str = "all",
    limit: int = 0,
) -> Dict[str, Any]:
    print(f"\n=======================================================")
    print(f"  WORKSPACE AGENT HOLISTIC ACCURACY BENCHMARK RUNNER  ")
    print(f"=======================================================")
    print(f"Benchmark Directory : {benchmark_dir}")
    print(f"Agent Results File  : {results_file}")
    print(f"Category Filter     : {category}")
    if limit > 0:
        print(f"Document Limit      : {limit}")
    print("-------------------------------------------------------\n")

    ground_truths = load_ground_truths(benchmark_dir, category=category, limit=limit)
    if not ground_truths:
        print(f"[!] No ground-truth documents found in {benchmark_dir} for category '{category}'.")
        return {}

    agent_outputs = load_agent_outputs(results_file)
    if not agent_outputs:
        print(f"[!] No valid agent outputs loaded from {results_file}.")
        return {}

    print(f"Loaded {len(ground_truths)} ground-truth documents.")
    print(f"Loaded {len(agent_outputs)} agent output records.")

    missing = [
        gt.get("metadata", {}).get("filename", "?")
        for gt in ground_truths
        if gt.get("metadata", {}).get("filename", "?") not in agent_outputs
    ]
    if missing:
        print(f"\n[WARN] {len(missing)} documents in benchmark have NO agent output:")
        for m in missing:
            print(f"  - {m}")
        print()

    scorecards: List[DocumentScorecard] = []

    headers = ["Category", "Filename (prefix)", "Extr", "F1", "Decision", "Steps", "Cost($)", "Result"]
    widths   = [14, 28, 6, 6, 18, 6, 8, 7]
    print_separator(widths)
    print_table_row(headers, widths)
    print_separator(widths)

    for gt in ground_truths:
        fn  = gt.get("metadata", {}).get("filename", "")
        cat = gt.get("metadata", {}).get("category", "")

        if fn not in agent_outputs:
            # Document has no agent output — treat as a missing result (auto-fail)
            agent_out: Dict[str, Any] = {
                "filename": fn,
                "decision": "MISSING",
                "records": [],
                "steps_taken": 0,
                "cost_usd": 0.0,
            }
        else:
            agent_out = agent_outputs[fn]

        sc = evaluate_document(gt, agent_out)
        scorecards.append(sc)

        status_str   = "PASS" if sc.passed else "FAIL"
        fn_short     = fn[:25] + "..." if len(fn) > 28 else fn
        decision_str = f"{sc.actual_decision[:10]} ({'✓' if sc.decision_correct else '✗'})"
        extr_str     = "N/A" if not sc.extraction_applicable else f"{sc.overall_extraction_score:.2f}"

        print_table_row([
            sc.category,
            fn_short,
            extr_str,
            f"{sc.table_f1:.2f}" if sc.extraction_applicable else "N/A",
            decision_str,
            str(sc.actual_steps),
            f"${sc.estimated_cost_usd:.4f}",
            status_str,
        ], widths)

        if sc.fail_reasons:
            for reason in sc.fail_reasons:
                print(f"  ↳ {reason}")

    print_separator(widths)

    # ------- Summary report -------
    report = generate_benchmark_report(scorecards)

    print("\n================ BENCHMARK SUMMARY REPORT ================")
    print(f"Total Documents Evaluated : {report['total_documents']}")
    print(f"Overall Pass Rate         : {report['overall_pass_rate'] * 100:.1f}%")
    print(f"Mean Extraction Accuracy  : {report['overall_extraction_accuracy'] * 100:.1f}%  "
          f"(COMMIT/REVIEW docs only)")
    print(f"Decision Routing Accuracy : {report['decision_routing_accuracy'] * 100:.1f}%")
    print(f"Total Estimated Cost      : ${report['total_estimated_cost_usd']:.4f} USD")
    print(f"Average Cost / Document   : ${report['average_cost_per_doc_usd']:.4f} USD")
    print("----------------------------------------------------------")

    print("\n--- Breakdown By Category ---")
    cat_headers = ["Category", "Count", "Pass %", "Extr %", "Decision %", "Avg Steps", "Avg Cost"]
    cat_widths  = [14, 6, 10, 10, 11, 10, 10]
    print_separator(cat_widths)
    print_table_row(cat_headers, cat_widths)
    print_separator(cat_widths)
    for cat_name, c_data in report.get("category_breakdown", {}).items():
        print_table_row([
            cat_name,
            str(c_data["count"]),
            f"{c_data['pass_rate'] * 100:.1f}%",
            f"{c_data['avg_extraction_score'] * 100:.1f}%",
            f"{c_data['decision_accuracy'] * 100:.1f}%",
            str(c_data["avg_steps"]),
            f"${c_data['avg_cost_usd']:.4f}",
        ], cat_widths)
    print_separator(cat_widths)

    # Save full report
    out_path = os.path.join(benchmark_dir, "last_benchmark_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print(f"\n[+] Full report saved to: {out_path}\n")

    return report


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run Agent Accuracy Benchmark",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--benchmark-dir",
        default=os.path.join(str(ROOT_DIR), "dataset", "evaluation_benchmark"),
        help="Path to curated benchmark directory",
    )
    parser.add_argument(
        "--category",
        default="all",
        choices=["all", "clean", "multi_record", "messy", "adversarial"],
        help="Filter by category",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit number of documents to evaluate",
    )
    parser.add_argument(
        "--results-file",
        default=None,
        help="Path to JSONL file containing real agent outputs (required for evaluation)",
    )
    parser.add_argument(
        "--mode",
        default=None,
        choices=["spec", "evaluate"],
        help="'spec' prints benchmark spec; 'evaluate' scores agent outputs (default when --results-file given)",
    )

    args = parser.parse_args()

    # Determine mode
    if args.mode == "spec" or (args.results_file is None and args.mode != "evaluate"):
        show_spec(args.benchmark_dir, category=args.category)
        if args.results_file is None and args.mode != "spec":
            print("[!] No --results-file provided. Showing spec only.")
            print("    Provide a JSONL file of agent outputs to run a real evaluation.")
        sys.exit(0)

    # Evaluate mode
    if not os.path.exists(args.results_file):
        print(f"[ERROR] Results file not found: {args.results_file}")
        sys.exit(1)

    run_evaluation(
        benchmark_dir=args.benchmark_dir,
        results_file=args.results_file,
        category=args.category,
        limit=args.limit,
    )
