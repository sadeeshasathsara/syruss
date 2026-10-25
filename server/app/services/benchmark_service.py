"""
Benchmark a template version against its golden dataset.

Compares each golden item's ground truth with the record the agent actually produced for
that document under the given version. Items the agent has not processed with this version
are reported as not evaluated; nothing is synthesized.
"""
import difflib
from typing import Any, Dict, List, Tuple
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.document import Document
from app.models.extraction import ExtractionResult
from app.models.template_enterprise import DocumentTemplateVersion, GoldenDatasetItem


def _flatten(value: Any, prefix: str = "") -> Dict[str, str]:
    out: Dict[str, str] = {}
    if isinstance(value, dict):
        for k, v in value.items():
            out.update(_flatten(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            out.update(_flatten(v, f"{prefix}[{i}]"))
    elif value not in (None, ""):
        out[prefix] = str(value).strip().lower()
    return out


def _cer(ref: str, hyp: str) -> float:
    if not ref:
        return 0.0 if not hyp else 1.0
    ops = difflib.SequenceMatcher(None, ref, hyp).get_opcodes()
    dist = sum(max(i2 - i1, j2 - j1) for tag, i1, i2, j1, j2 in ops if tag != "equal")
    return min(1.0, dist / len(ref))


def compare(expected: Dict[str, Any], actual: Dict[str, Any]) -> Dict[str, Any]:
    exp, act = _flatten(expected), _flatten(actual)
    correct = sum(1 for k, v in exp.items() if act.get(k) == v)
    precision = correct / len(act) if act else 0.0
    recall = correct / len(exp) if exp else 1.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    cers = [_cer(v, act.get(k, "")) for k, v in exp.items()]
    return {
        "precision": round(precision, 4), "recall": round(recall, 4), "f1_score": round(f1, 4),
        "exact_match": exp == act, "cer": round(sum(cers) / len(cers), 4) if cers else 0.0,
        "missing_fields": sorted(k for k in exp if k not in act),
        "wrong_fields": sorted(k for k, v in exp.items() if k in act and act[k] != v),
    }


async def run_benchmark(db: AsyncSession, version: DocumentTemplateVersion) -> Dict[str, Any]:
    items = list((await db.execute(
        select(GoldenDatasetItem).where(GoldenDatasetItem.template_id == version.template_id)
    )).scalars().all())

    results: List[Dict[str, Any]] = []
    scored: List[Tuple[Dict[str, Any], bool]] = []
    for item in items:
        doc = (await db.execute(select(Document).where(
            Document.template_id == version.template_id, Document.minio_object_key == item.storage_path,
        ))).scalars().first()
        ext = None
        if doc:
            ext = (await db.execute(select(ExtractionResult).where(ExtractionResult.document_id == doc.id))).scalar_one_or_none()
        if not ext or ext.template_version_id != version.id:
            results.append({"name": item.name, "evaluated": False,
                            "reason": "No record produced with this version. Process the document first."})
            continue
        metrics = compare(item.ground_truth_json, ext.extracted_data)
        rules_ok = bool(ext.validation.get("valid", False))
        results.append({"name": item.name, "evaluated": True, "rules_valid": rules_ok, **metrics})
        scored.append((metrics, rules_ok))

    n = len(scored)
    avg = lambda key: round(sum(m[key] for m, _ in scored) / n, 4) if n else 0.0
    return {
        "item_count": len(items),
        "evaluated_count": n,
        "precision": avg("precision"),
        "recall": avg("recall"),
        "f1_score": avg("f1_score"),
        "exact_match_rate": round(sum(1 for m, _ in scored if m["exact_match"]) / n, 4) if n else 0.0,
        "average_cer": avg("cer"),
        "rule_pass_rate": round(sum(1 for _, ok in scored if ok) / n, 4) if n else 0.0,
        "failed_count": sum(1 for m, ok in scored if not m["exact_match"] or not ok),
        "status": "NOT_EVALUATED" if n == 0 else ("PASSED" if all(m["exact_match"] and ok for m, ok in scored) else "FAILED"),
        "results": results,
    }
