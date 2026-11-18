"""
Unified Confidence Evaluator Service — Phase 2 of the Production-Grade Confidence Engine.

Computes a fixed 100-point composite confidence score from four independent dimensions:

    Final Score = S_visual + S_math_format + S_rules + S_data_sources

Fixed weights (NEVER redistributed):
  - Visual & Field Integrity   : 40 pts
  - Formats & Arithmetic       : 20 pts
  - Workspace Rules            : 20 pts (0 if no rules configured)
  - Master Data Sources        : 20 pts (0 if no sources connected)

Hard-Cap Vetoes (any single condition forces final confidence <= 0.50):
  - Arithmetic discrepancy in reconciled fields (e.g. 10 + 5 = 20)
  - Master identity collision detected by identity_check.py
  - Any critical field (critical: true in schema) is missing or empty
  - Any workspace rule with severity: "error" fails

Returned as a structured ConfidenceReport with full audit breakdown.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from app.services.rules.engine import normalize_schema, run_rule, schema_rules, _check_rule
from app.services.validation import check_format_rule, reconciler
from app.services.identity_check import resolve_entity_matches


# ---------------------------------------------------------------------------
# Constants — fixed forever
# ---------------------------------------------------------------------------
MAX_VISUAL: float = 40.0
MAX_MATH_FORMAT: float = 20.0
MAX_RULES: float = 20.0
MAX_DATA_SOURCES: float = 20.0
HARD_CAP: float = 0.50


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

class VetoReason(str, Enum):
    ARITHMETIC_DISCREPANCY = "arithmetic_discrepancy"
    IDENTITY_COLLISION = "identity_collision"
    CRITICAL_FIELD_MISSING = "critical_field_missing"
    ERROR_RULE_FAILED = "error_rule_failed"
    ROW_OMISSION = "row_omission"
    MULTI_DOCUMENT_COLLISION = "multi_document_collision"


@dataclass
class VisualScore:
    """
    Score from visual model clarity and field integrity (max 40 pts).

    Callers (agent loop / extraction worker) provide `raw_score` as a
    fraction 0.0–1.0 from the model's self-assessment. The service scales
    it to 40 pts, then applies critical-field presence deductions.
    """
    raw_score: float              # 0.0–1.0 from LLM/vision model
    base_pts: float               # raw_score * MAX_VISUAL before deductions
    critical_field_penalties: float = 0.0  # deductions for missing critical fields
    final_pts: float = 0.0
    missing_critical_fields: List[str] = field(default_factory=list)
    dual_pass_verified: Optional[bool] = None
    dual_pass_bonus: float = 0.0


@dataclass
class MathFormatScore:
    """Score from format validators and arithmetic reconciler (max 20 pts)."""
    reconciler_score: float       # 0.0–1.0 from ArithmeticReconciler
    format_violations: List[str] = field(default_factory=list)
    reconciler_checks: int = 0
    reconciler_passed: int = 0
    has_arithmetic_discrepancy: bool = False
    final_pts: float = 0.0


@dataclass
class RulesScore:
    """Score from active workspace rules (max 20 pts, 0 if no rules)."""
    rules_configured: bool
    total_active: int = 0
    passed: int = 0
    warning_failures: int = 0
    error_failures: int = 0
    has_error_rule_failure: bool = False
    final_pts: float = 0.0
    failures: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class DataSourcesScore:
    """
    Score from master data sources / knowledge graph (max 20 pts, 0 if no sources).

    When sources ARE connected, callers may pass pre-computed entity match results.
    The scoring logic is documented inline; full async entity resolution is left
    to the caller (confidence_service is sync-safe; DB calls happen outside).
    """
    sources_connected: bool
    # Pre-computed match signals from entity resolution (optional)
    exact_matches: int = 0       # → 20 / 20 pts per entity
    alias_matches: int = 0       # → 18 / 20 pts per entity
    unknown_entities: int = 0    # → 5 / 20 pts per entity (closed source)
    identity_collisions: int = 0 # → 0 pts + HARD VETO
    total_identity_fields: int = 0
    final_pts: float = 0.0


@dataclass
class ConfidenceReport:
    """Full auditable confidence breakdown for one document extraction."""
    # Composite result
    total_confidence: float            # 0.0–1.0
    total_pts: float                   # 0.0–100.0
    hard_cap_applied: Optional[VetoReason]  # None if no veto
    veto_messages: List[str]

    # Dimension breakdowns
    visual: VisualScore
    math_format: MathFormatScore
    rules: RulesScore
    data_sources: DataSourcesScore

    # Field-level confidences (populated by callers who have per-field signals)
    field_confidences: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialisable snapshot for persistence in validation.confidence_details."""
        return {
            "total_confidence": round(self.total_confidence, 4),
            "total_pts": round(self.total_pts, 2),
            "hard_cap_applied": self.hard_cap_applied.value if self.hard_cap_applied else None,
            "veto_messages": self.veto_messages,
            "breakdown": {
                "visual": {
                    "score": round(self.visual.final_pts, 2),
                    "max": MAX_VISUAL,
                    "raw_model_confidence": round(self.visual.raw_score, 4),
                    "missing_critical_fields": self.visual.missing_critical_fields,
                    "dual_pass_verified": self.visual.dual_pass_verified,
                    "dual_pass_bonus": self.visual.dual_pass_bonus,
                },
                "math_format": {
                    "score": round(self.math_format.final_pts, 2),
                    "max": MAX_MATH_FORMAT,
                    "arithmetic_checks": self.math_format.reconciler_checks,
                    "arithmetic_passed": self.math_format.reconciler_passed,
                    "has_discrepancy": self.math_format.has_arithmetic_discrepancy,
                    "format_violations": self.math_format.format_violations,
                },
                "rules": {
                    "score": round(self.rules.final_pts, 2),
                    "max": MAX_RULES,
                    "configured": self.rules.rules_configured,
                    "total_active": self.rules.total_active,
                    "passed": self.rules.passed,
                    "warning_failures": self.rules.warning_failures,
                    "error_failures": self.rules.error_failures,
                },
                "data_sources": {
                    "score": round(self.data_sources.final_pts, 2),
                    "max": MAX_DATA_SOURCES,
                    "connected": self.data_sources.sources_connected,
                    "exact_matches": self.data_sources.exact_matches,
                    "alias_matches": self.data_sources.alias_matches,
                    "unknown_entities": self.data_sources.unknown_entities,
                    "identity_collisions": self.data_sources.identity_collisions,
                },
            },
            "field_confidences": {k: round(v, 4) for k, v in self.field_confidences.items()},
        }


# ---------------------------------------------------------------------------
# Sub-scorers
# ---------------------------------------------------------------------------

def _score_visual(
    schema: Optional[Dict[str, Any]],
    data: Dict[str, Any],
    raw_model_confidence: float,
    dual_pass_verified: Optional[bool] = None,
) -> VisualScore:
    """
    Scale LLM raw confidence to 40 pts.
    Deduct 5 pts per missing critical field (floor 0).
    Award +5 pts dual-pass bonus if dual_pass_verified is True (clamped at 40 pts).
    """
    raw = max(0.0, min(1.0, raw_model_confidence))
    base_pts = round(raw * MAX_VISUAL, 4)
    dual_pass_bonus = 5.0 if dual_pass_verified is True else 0.0

    missing_critical: List[str] = []
    objects = normalize_schema(schema)
    for otype, ospec in objects.items():
        rows = data.get(otype)
        row_list: List[Dict[str, Any]]
        if isinstance(rows, dict):
            row_list = [rows]
        elif isinstance(rows, list):
            row_list = [r for r in rows if isinstance(r, dict)]
        else:
            row_list = []

        for fname, fspec in ospec["fields"].items():
            if not fspec.get("critical"):
                continue
            # Check every row for this critical field
            for idx, row in enumerate(row_list):
                val = row.get(fname)
                if val is None or val == "":
                    path = f"{otype}[{idx}].{fname}" if ospec["multiple"] else f"{otype}.{fname}"
                    missing_critical.append(path)

    penalty_pts = len(missing_critical) * 5.0
    final_pts = max(0.0, min(MAX_VISUAL, base_pts + dual_pass_bonus - penalty_pts))

    return VisualScore(
        raw_score=raw,
        base_pts=base_pts,
        critical_field_penalties=penalty_pts,
        final_pts=final_pts,
        missing_critical_fields=missing_critical,
        dual_pass_verified=dual_pass_verified,
        dual_pass_bonus=dual_pass_bonus,
    )


def _score_math_format(
    schema: Optional[Dict[str, Any]],
    data: Dict[str, Any],
) -> MathFormatScore:
    """
    Run format validators on all fields with format_rule and
    intrinsic arithmetic reconciler on the full record.
    Returns a MathFormatScore out of 20 pts.
    """
    format_violations: List[str] = []
    format_total = 0
    format_passed = 0

    objects = normalize_schema(schema)
    for otype, ospec in objects.items():
        rows = data.get(otype)
        row_list: List[Dict[str, Any]]
        if isinstance(rows, dict):
            row_list = [rows]
        elif isinstance(rows, list):
            row_list = [r for r in rows if isinstance(r, dict)]
        else:
            row_list = []

        for fname, fspec in ospec["fields"].items():
            fmt_rule = fspec.get("format_rule")
            if not fmt_rule:
                continue
            for idx, row in enumerate(row_list):
                val = row.get(fname)
                if val is None or val == "":
                    continue  # missing fields handled by visual scorer
                format_total += 1
                ok, err = check_format_rule(val, fmt_rule)
                if ok:
                    format_passed += 1
                else:
                    path = f"{otype}[{idx}].{fname}" if ospec["multiple"] else f"{otype}.{fname}"
                    format_violations.append(f"{path}: {err}")

    # Arithmetic reconciler
    recon = reconciler.reconcile(schema, data)

    # Combine: format violations reduce the format portion; reconciler score covers arithmetic
    # Strategy:
    #   - If no arithmetic checks AND no format rules → full 20 pts (nothing to verify)
    #   - Otherwise: score = weighted average of format pass rate and reconciler score
    total_checks = format_total + recon.applicable_checks_count
    if total_checks == 0:
        combined_score = 1.0
    else:
        format_score = (format_passed / format_total) if format_total > 0 else 1.0
        recon_score = recon.score if recon.applicable_checks_count > 0 else 1.0

        # Weighted by count of checks in each category
        format_weight = format_total / total_checks
        recon_weight = recon.applicable_checks_count / total_checks
        combined_score = (format_score * format_weight) + (recon_score * recon_weight)

    final_pts = round(combined_score * MAX_MATH_FORMAT, 4)

    return MathFormatScore(
        reconciler_score=recon.score,
        format_violations=format_violations,
        reconciler_checks=recon.applicable_checks_count,
        reconciler_passed=recon.passed_checks_count,
        has_arithmetic_discrepancy=recon.has_discrepancy,
        final_pts=final_pts,
    )


def _score_rules(
    schema: Optional[Dict[str, Any]],
    data: Dict[str, Any],
    active_workspace_rules: Optional[List[Dict[str, Any]]],
) -> RulesScore:
    """
    Evaluate schema built-in rules and workspace rules against data.
    Returns RulesScore out of 20 pts (0 if no rules are configured).

    Rules with severity "error" that fail → has_error_rule_failure = True (triggers veto).
    Rules with severity "warning" that fail → warning_failures count (partial deduction).
    """
    workspace_rules = list(active_workspace_rules or [])
    builtin_rules = schema_rules(schema)

    has_any_rules = bool(builtin_rules or workspace_rules)
    if not has_any_rules:
        return RulesScore(rules_configured=False, final_pts=0.0)

    failures: List[Dict[str, Any]] = []
    warning_failures = 0
    error_failures = 0
    total_active = len(builtin_rules) + len(workspace_rules)
    passed = 0

    # Evaluate schema built-in rules (all treated as error-level)
    for rule in builtin_rules:
        rule_errors = _check_rule(rule, data, schema)
        if rule_errors:
            error_failures += 1
            failures.extend(rule_errors)
        else:
            passed += 1

    # Evaluate workspace (lang.py) rules
    for rule in workspace_rules:
        rule_failures = run_rule(rule, schema, data)
        severity = rule.get("severity", "error")
        if rule_failures:
            if severity == "warning":
                warning_failures += 1
            else:
                error_failures += 1
            failures.extend(rule_failures)
        else:
            passed += 1

    has_error_failure = error_failures > 0

    # Scoring: each passed rule earns equal weight of 20 pts
    # Warning failures cost half a slot; error failures cost a full slot
    # (they also trigger veto separately)
    total_weight = passed + error_failures + (warning_failures * 0.5)
    if total_weight == 0:
        score = 1.0
    else:
        score = passed / total_weight

    final_pts = round(score * MAX_RULES, 4)

    return RulesScore(
        rules_configured=True,
        total_active=total_active,
        passed=passed,
        warning_failures=warning_failures,
        error_failures=error_failures,
        has_error_rule_failure=has_error_failure,
        final_pts=final_pts,
        failures=failures,
    )


def _score_data_sources(
    entity_match_result: Optional[Dict[str, Any]],
) -> DataSourcesScore:
    """
    Compute data sources score from pre-resolved entity match results.

    Entity match resolution is async (requires DB) so it must be done by the
    caller before calling evaluate_confidence(). Pass the result dict here.

    Expected shape of entity_match_result:
    {
        "sources_connected": bool,
        "exact_matches": int,
        "alias_matches": int,
        "unknown_entities": int,
        "identity_collisions": int,
        "total_identity_fields": int,
    }

    If sources_connected is False or entity_match_result is None → 0 pts.
    """
    if not entity_match_result or not entity_match_result.get("sources_connected"):
        return DataSourcesScore(sources_connected=False, final_pts=0.0)

    exact = int(entity_match_result.get("exact_matches", 0))
    alias = int(entity_match_result.get("alias_matches", 0))
    unknown = int(entity_match_result.get("unknown_entities", 0))
    collisions = int(entity_match_result.get("identity_collisions", 0))
    total = int(entity_match_result.get("total_identity_fields", 0))

    if total == 0:
        # No identity fields to match — sources connected but nothing to verify
        final_pts = MAX_DATA_SOURCES
    else:
        # Weighted per-entity score (each entity contributes 20/N pts maximum)
        pts_per_entity = MAX_DATA_SOURCES / total
        pts = (exact * pts_per_entity * 1.00
               + alias * pts_per_entity * 0.90
               + unknown * pts_per_entity * 0.25
               + collisions * 0)
        final_pts = round(min(MAX_DATA_SOURCES, max(0.0, pts)), 4)

    return DataSourcesScore(
        sources_connected=True,
        exact_matches=exact,
        alias_matches=alias,
        unknown_entities=unknown,
        identity_collisions=collisions,
        total_identity_fields=total,
        final_pts=final_pts,
    )




# ---------------------------------------------------------------------------
# Enterprise Hardening Safeguards (Section 7 of Master Plan)
# ---------------------------------------------------------------------------

@dataclass
class EnterpriseSafeguardsAudit:
    spatial_contradiction: bool = False
    spatial_messages: List[str] = field(default_factory=list)
    spatial_penalty: float = 0.0

    row_omission_detected: bool = False
    row_omission_message: Optional[str] = None

    multi_page_collision_detected: bool = False
    multi_page_collision_message: Optional[str] = None

    perplexity_penalty_applied: bool = False
    perplexity_penalty: float = 0.0


def _audit_enterprise_safeguards(
    *,
    schema: Optional[Dict[str, Any]],
    data: Dict[str, Any],
    evidence: Optional[List[Dict[str, Any]]] = None,
    visual_row_count_detected: Optional[int] = None,
    multi_page_collision: bool = False,
    has_low_probability_tokens: bool = False,
) -> EnterpriseSafeguardsAudit:
    """
    Evaluates the 4 definitive enterprise hardening safeguards:
      Safeguard A: Spatial Grounding & Bounding-Box Sanity (-15 pts on contradiction)
      Safeguard B: Table Row Completeness Audit (<= 0.50 Hard-Cap Veto on delta)
      Safeguard C: Multi-Page Bleed & Document Collision (<= 0.40 Hard-Cap Veto)
      Safeguard D: Token-Level Statistical Perplexity (-10 pts on low prob digit)
    """
    audit = EnterpriseSafeguardsAudit()

    # --- Safeguard A: Spatial Grounding ---
    if evidence:
        row_y_coords: Dict[str, List[tuple]] = {}  # object_type -> [(index, ymin)]
        for ev in evidence:
            if not isinstance(ev, dict):
                continue
            box = ev.get("box_2d")
            page = ev.get("page", 1)
            field_name = str(ev.get("field", "")).lower()
            obj_name = str(ev.get("object", ""))
            idx = ev.get("index")

            if isinstance(box, (list, tuple)) and len(box) >= 4:
                ymin, xmin, ymax, xmax = box[:4]
                # Region sanity on Page 1
                if page == 1:
                    # Header fields (vendor, company) should reside in upper region (ymin < 400)
                    if any(h in field_name for h in ("vendor", "company", "supplier", "header")) and ymin > 400:
                        audit.spatial_contradiction = True
                        audit.spatial_messages.append(
                            f"Spatial region contradiction: header field '{field_name}' located at ymin={ymin} (> 400)."
                        )
                    # Grand totals and signatures should not reside in top 10% header (ymin > 100)
                    if any(t in field_name for t in ("grand_total", "total_amount", "amount_due", "signature")) and ymin < 100:
                        audit.spatial_contradiction = True
                        audit.spatial_messages.append(
                            f"Spatial region contradiction: total/signature field '{field_name}' located in top 10% header (ymin={ymin} < 100)."
                        )

                # Track table row progression for monotonic ordering
                if idx is not None and isinstance(idx, int):
                    row_y_coords.setdefault(obj_name, []).append((idx, ymin))

        for obj_name, coords in row_y_coords.items():
            sorted_coords = sorted(coords, key=lambda x: x[0])
            for i in range(len(sorted_coords) - 1):
                cur_idx, cur_ymin = sorted_coords[i]
                next_idx, next_ymin = sorted_coords[i + 1]
                # If subsequent row jumps backwards significantly (> 20 units) on same page
                if next_ymin < cur_ymin - 20:
                    audit.spatial_contradiction = True
                    audit.spatial_messages.append(
                        f"Table row non-monotonic progression in '{obj_name}': row {next_idx} (ymin={next_ymin}) "
                        f"appears above row {cur_idx} (ymin={cur_ymin})."
                    )
                    break

        if audit.spatial_contradiction:
            audit.spatial_penalty = 15.0

    # --- Safeguard B: Table Row Completeness Audit ---
    if visual_row_count_detected is not None:
        # Determine extracted items count across multiple-item tables in data
        extracted_row_count = 0
        objects = normalize_schema(schema)
        for otype, ospec in objects.items():
            if ospec["multiple"]:
                rows = data.get(otype)
                if isinstance(rows, list):
                    extracted_row_count = max(extracted_row_count, len(rows))
        
        delta_rows = abs(visual_row_count_detected - extracted_row_count)
        if delta_rows > 0:
            audit.row_omission_detected = True
            audit.row_omission_message = (
                f"Possible row omission: vision detected {visual_row_count_detected} rows on the sheet, "
                f"but only {extracted_row_count} were extracted."
            )

    # --- Safeguard C: Multi-Page Bleed & Collision ---
    if multi_page_collision:
        audit.multi_page_collision_detected = True
        audit.multi_page_collision_message = (
            "Multi-page collision detected: primary identity shifted mid-document without continuation label. "
            "Confidence capped at 0.40."
        )

    # --- Safeguard D: Token Perplexity ---
    if has_low_probability_tokens:
        audit.perplexity_penalty_applied = True
        audit.perplexity_penalty = 10.0

    return audit


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def evaluate_confidence(
    *,
    schema: Optional[Dict[str, Any]],
    data: Dict[str, Any],
    raw_model_confidence: float,
    active_workspace_rules: Optional[List[Dict[str, Any]]] = None,
    entity_match_result: Optional[Dict[str, Any]] = None,
    field_confidences: Optional[Dict[str, float]] = None,
    identity_conflicts: Optional[List[Dict[str, Any]]] = None,
    dual_pass_verified: Optional[bool] = None,
    evidence: Optional[List[Dict[str, Any]]] = None,
    visual_row_count_detected: Optional[int] = None,
    multi_page_collision: bool = False,
    has_low_probability_tokens: bool = False,
) -> ConfidenceReport:
    """
    Compute the full 100-point audited confidence score for a document extraction.

    Args:
        schema:                  Template schema definition dict.
        data:                    Extracted record data dict (keyed by object type).
        raw_model_confidence:    LLM/vision model's self-reported confidence (0.0–1.0).
        active_workspace_rules:  List of active lang.py rule dicts for this workspace.
        entity_match_result:     Pre-resolved entity matching stats dict (from async DB query).
        field_confidences:       Optional per-field confidence signals from the model.
        identity_conflicts:      Pre-resolved identity collision list from identity_check.py.

    Returns:
        ConfidenceReport with full breakdown and final confidence (0.0–1.0).
    """
    # --- Score each dimension ---
    # Run Enterprise Hardening Safeguards audit
    safeguards = _audit_enterprise_safeguards(
        schema=schema,
        data=data,
        evidence=evidence,
        visual_row_count_detected=visual_row_count_detected,
        multi_page_collision=multi_page_collision,
        has_low_probability_tokens=has_low_probability_tokens,
    )

    v_score = _score_visual(schema, data, raw_model_confidence, dual_pass_verified=dual_pass_verified)
    # Apply Safeguard A & D visual deductions
    if safeguards.spatial_penalty > 0 or safeguards.perplexity_penalty > 0:
        total_safeguard_deductions = safeguards.spatial_penalty + safeguards.perplexity_penalty
        v_score.final_pts = max(0.0, v_score.final_pts - total_safeguard_deductions)
    mf_score = _score_math_format(schema, data)
    r_score = _score_rules(schema, data, active_workspace_rules)
    ds_score = _score_data_sources(entity_match_result)

    # --- Determine hard-cap vetoes ---
    veto: Optional[VetoReason] = None
    veto_messages: List[str] = []

    # Veto 1: Arithmetic discrepancy
    if mf_score.has_arithmetic_discrepancy:
        veto = VetoReason.ARITHMETIC_DISCREPANCY
        veto_messages.append(
            "Arithmetic discrepancy detected: extracted figures do not reconcile "
            "(e.g. line items do not sum to stated total). Score capped at 0.50."
        )

    # Veto 2: Identity collision (from identity_check.py)
    has_collision = bool(identity_conflicts) or (
        entity_match_result and entity_match_result.get("identity_collisions", 0) > 0
    )
    if has_collision:
        if veto is None:
            veto = VetoReason.IDENTITY_COLLISION
        collision_detail = ""
        if identity_conflicts:
            collision_detail = " " + "; ".join(c.get("message", "") for c in identity_conflicts[:2])
        veto_messages.append(
            f"Master identity collision detected: an extracted identifier matches a different "
            f"entity in the master data source.{collision_detail} Score capped at 0.50."
        )

    # Veto 3: Critical field missing
    if v_score.missing_critical_fields:
        if veto is None:
            veto = VetoReason.CRITICAL_FIELD_MISSING
        field_list = ", ".join(v_score.missing_critical_fields[:5])
        if len(v_score.missing_critical_fields) > 5:
            field_list += f" ... (+{len(v_score.missing_critical_fields) - 5} more)"
        veto_messages.append(
            f"Critical field(s) missing or empty: {field_list}. Score capped at 0.50."
        )

    # Veto 4: Error-severity rule failure
    if r_score.has_error_rule_failure:
        if veto is None:
            veto = VetoReason.ERROR_RULE_FAILED
        veto_messages.append(
            f"{r_score.error_failures} workspace rule(s) with severity 'error' failed. "
            "Score capped at 0.50."
        )

    # Veto 5 (Safeguard B): Table Row Completeness Omission
    if safeguards.row_omission_detected:
        if veto is None:
            veto = VetoReason.ROW_OMISSION
        veto_messages.append(safeguards.row_omission_message or "Row omission detected: score capped at 0.50.")

    # Veto 6 (Safeguard C): Multi-Page Document Collision
    if safeguards.multi_page_collision_detected:
        if veto is None:
            veto = VetoReason.MULTI_DOCUMENT_COLLISION
        veto_messages.append(safeguards.multi_page_collision_message or "Multi-page collision detected: score capped at 0.40.")

    # --- Compute raw total ---
    raw_pts = v_score.final_pts + mf_score.final_pts + r_score.final_pts + ds_score.final_pts
    raw_confidence = raw_pts / 100.0

    # --- Apply hard cap ---
    if veto is not None:
        effective_cap = 0.40 if veto == VetoReason.MULTI_DOCUMENT_COLLISION else HARD_CAP
        final_confidence = min(effective_cap, raw_confidence)
        final_pts = final_confidence * 100.0
    else:
        final_confidence = min(1.0, raw_confidence)
        final_pts = raw_pts

    return ConfidenceReport(
        total_confidence=round(final_confidence, 4),
        total_pts=round(final_pts, 2),
        hard_cap_applied=veto,
        veto_messages=veto_messages,
        visual=v_score,
        math_format=mf_score,
        rules=r_score,
        data_sources=ds_score,
        field_confidences=field_confidences or {},
    )
