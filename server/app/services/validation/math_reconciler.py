"""
Intrinsic Arithmetic Reconciler for Document Extraction.

Reconciles:
  1. Line Items Sum vs Subtotal: sum(line_total) == subtotal
  2. Subtotal + Tax vs Grand Total: subtotal + tax == grand_total
  3. Quantity * Unit Price vs Line Total: qty * unit_price == line_total
  4. Time Difference vs Duration: (end_time - start_time) == hours

Supports both explicitly tagged `math_role` in schema and heuristic detection
for standard financial and roster field names.
"""
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.services.rules.engine import normalize_schema, parse_number, parse_time_minutes


@dataclass
class MathCheckDetail:
    check_name: str
    passed: bool
    expected: float
    actual: float
    delta: float
    tolerance: float
    description: str


@dataclass
class ReconciliationResult:
    reconciled: bool           # True if all applicable math checks passed, False if any failed
    applicable_checks_count: int
    passed_checks_count: int
    score: float               # 0.0 to 1.0 (1.0 if all passed, proportional or 0.0 if failed)
    has_discrepancy: bool      # True if at least one applicable check failed beyond tolerance
    details: List[MathCheckDetail] = field(default_factory=list)

    @property
    def summary_message(self) -> str:
        if self.applicable_checks_count == 0:
            return "No arithmetic relationships detected on template."
        if not self.has_discrepancy:
            return f"All {self.passed_checks_count}/{self.applicable_checks_count} arithmetic checks verified."
        failures = [d.description for d in self.details if not d.passed]
        return f"Arithmetic discrepancy ({len(failures)} failed): " + "; ".join(failures)


# Standard role names
ROLE_QTY = "quantity"
ROLE_UNIT_PRICE = "unit_price"
ROLE_LINE_TOTAL = "line_total"
ROLE_SUBTOTAL = "subtotal"
ROLE_TAX = "tax"
ROLE_GRAND_TOTAL = "grand_total"
ROLE_START_TIME = "start_time"
ROLE_END_TIME = "end_time"
ROLE_HOURS = "hours"

# Standard heuristic name aliases
HEURISTIC_MAP = {
    ROLE_QTY: {"qty", "quantity", "count", "units"},
    ROLE_UNIT_PRICE: {"unit_price", "unitprice", "rate", "price", "unit_rate"},
    ROLE_LINE_TOTAL: {"line_total", "linetotal", "amount", "total_price", "line_amount", "item_total"},
    ROLE_SUBTOTAL: {"subtotal", "sub_total", "net_amount", "total_net", "total_before_tax"},
    ROLE_TAX: {"tax", "tax_amount", "vat", "vat_amount", "gst", "sales_tax"},
    ROLE_GRAND_TOTAL: {"grand_total", "total", "invoice_total", "total_amount", "amount_due", "balance_due"},
    ROLE_START_TIME: {"start_time", "time_in", "in_time", "start", "clock_in"},
    ROLE_END_TIME: {"end_time", "time_out", "out_time", "end", "clock_out"},
    ROLE_HOURS: {"hours", "total_hours", "worked_hours", "duration"},
}


def _detect_role(fspec: Dict[str, Any], fname: str) -> Optional[str]:
    """Detects math role from explicit schema tag or field name heuristic."""
    explicit = fspec.get("math_role")
    if explicit:
        return explicit.lower().strip()
    clean_name = re.sub(r"[\W_]+", "", fname).lower()
    for role, aliases in HEURISTIC_MAP.items():
        for alias in aliases:
            if clean_name == re.sub(r"[\W_]+", "", alias).lower():
                return role
    return None


class ArithmeticReconciler:
    def __init__(self, tolerance: float = 0.02):
        self.default_tolerance = tolerance

    def reconcile(self, schema: Optional[Dict[str, Any]], data: Dict[str, Any]) -> ReconciliationResult:
        if not isinstance(data, dict):
            return ReconciliationResult(reconciled=True, applicable_checks_count=0, passed_checks_count=0, score=1.0, has_discrepancy=False)

        objects = normalize_schema(schema)
        details: List[MathCheckDetail] = []

        # Find header objects (multiple: False) and table objects (multiple: True)
        header_data: Dict[str, Any] = {}
        header_roles: Dict[str, Tuple[str, str]] = {}  # role -> (object_type, field_name)

        table_objects: List[Tuple[str, List[Dict[str, Any]], Dict[str, str]]] = []  # [(obj_type, rows, {role: fname})]

        for otype, ospec in objects.items():
            field_roles: Dict[str, str] = {}
            for fname, fspec in ospec["fields"].items():
                role = _detect_role(fspec, fname)
                if role:
                    field_roles[role] = fname

            raw_val = data.get(otype)
            if not ospec["multiple"]:
                rec = raw_val if isinstance(raw_val, dict) else (raw_val[0] if isinstance(raw_val, list) and raw_val else {})
                header_data[otype] = rec
                for role, fname in field_roles.items():
                    header_roles[role] = (otype, fname)
            else:
                rows = [r for r in raw_val if isinstance(r, dict)] if isinstance(raw_val, list) else []
                table_objects.append((otype, rows, field_roles))

        # Check 1: Quantity * Unit Price == Line Total (per line item)
        for otype, rows, roles in table_objects:
            q_field = roles.get(ROLE_QTY)
            p_field = roles.get(ROLE_UNIT_PRICE)
            tot_field = roles.get(ROLE_LINE_TOTAL)

            if q_field and p_field and tot_field and rows:
                for idx, r in enumerate(rows):
                    q = parse_number(r.get(q_field))
                    p = parse_number(r.get(p_field))
                    stated_tot = parse_number(r.get(tot_field))
                    if q is not None and p is not None and stated_tot is not None:
                        expected = round(q * p, 2)
                        delta = abs(expected - stated_tot)
                        passed = delta <= self.default_tolerance
                        details.append(MathCheckDetail(
                            check_name=f"{otype}[{idx}].line_calculation",
                            passed=passed,
                            expected=expected,
                            actual=stated_tot,
                            delta=delta,
                            tolerance=self.default_tolerance,
                            description=f"{otype}[{idx}]: {q_field}({q}) * {p_field}({p}) = {expected:.2f}, got {stated_tot:.2f}"
                        ))

        # Check 2: Sum of Line Items == Subtotal
        subtotal_target = header_roles.get(ROLE_SUBTOTAL)
        if subtotal_target:
            h_obj, h_field = subtotal_target
            stated_subtotal = parse_number(header_data.get(h_obj, {}).get(h_field))
            if stated_subtotal is not None:
                for otype, rows, roles in table_objects:
                    tot_field = roles.get(ROLE_LINE_TOTAL)
                    if tot_field and rows:
                        line_sum = round(sum(parse_number(r.get(tot_field)) or 0.0 for r in rows), 2)
                        delta = abs(line_sum - stated_subtotal)
                        passed = delta <= self.default_tolerance
                        details.append(MathCheckDetail(
                            check_name=f"sum({otype}.{tot_field})_vs_{h_obj}.{h_field}",
                            passed=passed,
                            expected=stated_subtotal,
                            actual=line_sum,
                            delta=delta,
                            tolerance=self.default_tolerance,
                            description=f"Sum of {otype}.{tot_field} ({line_sum:.2f}) vs stated subtotal {h_obj}.{h_field} ({stated_subtotal:.2f})"
                        ))

        # Check 3: Subtotal + Tax == Grand Total
        sub_info = header_roles.get(ROLE_SUBTOTAL)
        tax_info = header_roles.get(ROLE_TAX)
        grand_info = header_roles.get(ROLE_GRAND_TOTAL)

        if sub_info and grand_info:
            sub_val = parse_number(header_data.get(sub_info[0], {}).get(sub_info[1]))
            tax_val = parse_number(header_data.get(tax_info[0], {}).get(tax_info[1])) if tax_info else 0.0
            grand_val = parse_number(header_data.get(grand_info[0], {}).get(grand_info[1]))

            if sub_val is not None and grand_val is not None:
                expected_grand = round(sub_val + (tax_val or 0.0), 2)
                delta = abs(expected_grand - grand_val)
                passed = delta <= self.default_tolerance
                details.append(MathCheckDetail(
                    check_name="subtotal_plus_tax_vs_grand_total",
                    passed=passed,
                    expected=expected_grand,
                    actual=grand_val,
                    delta=delta,
                    tolerance=self.default_tolerance,
                    description=f"Subtotal ({sub_val:.2f}) + Tax ({tax_val or 0.0:.2f}) = {expected_grand:.2f}, stated grand total is {grand_val:.2f}"
                ))

        # Check 4: Time Range vs Duration (Rosters / Timesheets)
        for otype, rows, roles in table_objects:
            s_field = roles.get(ROLE_START_TIME)
            e_field = roles.get(ROLE_END_TIME)
            h_field = roles.get(ROLE_HOURS)

            if s_field and e_field and h_field and rows:
                for idx, r in enumerate(rows):
                    start_min = parse_time_minutes(r.get(s_field))
                    end_min = parse_time_minutes(r.get(e_field))
                    stated_hrs = parse_number(r.get(h_field))

                    if start_min is not None and end_min is not None and stated_hrs is not None:
                        calc_hrs = round(((end_min - start_min) % (24 * 60)) / 60.0, 2)
                        delta = abs(calc_hrs - stated_hrs)
                        passed = delta <= 0.05  # 3 minutes tolerance
                        details.append(MathCheckDetail(
                            check_name=f"{otype}[{idx}].time_range_vs_hours",
                            passed=passed,
                            expected=calc_hrs,
                            actual=stated_hrs,
                            delta=delta,
                            tolerance=0.05,
                            description=f"{otype}[{idx}]: {s_field}-{e_field} is {calc_hrs:.2f}h, stated hours is {stated_hrs:.2f}h"
                        ))

        # Synthesize outcome
        total_checks = len(details)
        if total_checks == 0:
            return ReconciliationResult(
                reconciled=True,
                applicable_checks_count=0,
                passed_checks_count=0,
                score=1.0,
                has_discrepancy=False,
                details=[],
            )

        passed_checks = sum(1 for d in details if d.passed)
        has_discrepancy = any(not d.passed for d in details)
        score = round(passed_checks / total_checks, 4)

        return ReconciliationResult(
            reconciled=not has_discrepancy,
            applicable_checks_count=total_checks,
            passed_checks_count=passed_checks,
            score=score,
            has_discrepancy=has_discrepancy,
            details=details,
        )


reconciler = ArithmeticReconciler()
