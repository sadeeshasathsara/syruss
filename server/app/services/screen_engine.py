"""
Custom screens: a saved table over the workspace's saved records.

A screen is a definition (what a person or the agent chose): one object of the template (its rows across every document),
the columns to show, calculated columns, filters, a sort and optionally a grouping with totals. This module checks a
definition against the template and runs it, so the numbers on a screen are always computed here, never by a model:

    validate_definition(definition, schema)  -> problems, as sentences (empty when it can be saved)
    run_screen(db, ...)                      -> columns, rows, totals, counts, per-column errors
    run_rows(loaded, ...)                    -> the same, over rows already loaded (what the tests use)

Records come from `record_query.saved_records` (committed documents, newest first, at most MAX_RECORDS). Values are typed by the
template's field types (numbers as numbers, text as text), so sorting and sums are exact.
"""
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Optional, Sequence, Set, Tuple
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.document import DocumentStatus
from app.services.formula import Blank, Compiled, FUNCTIONS, FormulaError, compile_formula
from app.services.graph_service import rows_of
from app.services.record_query import MAX_RECORDS, Condition, _context, _matches, saved_records
from app.services.rule_engine import normalize_schema, parse_number

MAX_COLUMNS = 30
MAX_FILTERS = 20
MAX_PAGE = 500
AGGREGATES = ("sum", "avg", "min", "max", "count", "count_distinct")
FILTER_OPS = ("eq", "ne", "contains", "in", "gte", "lte", "between", "empty", "not_empty")
IDENT = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


# --- the definition ---------------------------------------------------------------------

class ColumnFormat(BaseModel):
    type: Literal["text", "number", "currency", "percent", "date", "time", "duration"] = "text"
    decimals: Optional[int] = Field(None, ge=0, le=6)
    currency: Optional[str] = Field(None, max_length=8)
    align: Optional[Literal["left", "right", "center"]] = None


class Highlight(BaseModel):
    when: str = Field(..., max_length=400, description="A formula; where it is true the cell is marked")
    tone: Literal["positive", "warning", "negative", "info"] = "warning"


class Column(BaseModel):
    id: str = Field(..., description="Short name, lowercase letters, digits and _; formulas refer to a column by it")
    label: str = Field(..., min_length=1, max_length=80)
    kind: Literal["field", "formula"] = "field"
    field: Optional[str] = Field(None, description="For kind field: a field of the object, or 'Object.field' of a one-per-document object")
    formula: Optional[str] = Field(None, max_length=400, description="For kind formula")
    scope: Literal["row", "group"] = Field("row", description="group: a formula over the grouped columns' results (grouped screens only)")
    format: ColumnFormat = Field(default_factory=ColumnFormat)
    width: Optional[int] = Field(None, ge=60, le=600)
    hidden: bool = Field(False, description="Not shown, but formulas and filters can still use it")
    aggregate: Optional[Literal["sum", "avg", "min", "max", "count", "count_distinct"]] = Field(
        None, description="How the column is totalled: in each group, and in the totals row")
    highlight: List[Highlight] = Field(default_factory=list, max_length=4)

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not IDENT.match(v) or v in FUNCTIONS:
            raise ValueError(f"'{v}' is not a usable column name: start with a letter, then letters, digits or _ (and not a function name)")
        return v


class Filter(BaseModel):
    field: Optional[str] = Field(None, description="A column id, or a field of the object ('Object.field' for document fields)")
    formula: Optional[str] = Field(None, max_length=400, description="Instead of field/op/value: a formula that must be true")
    op: Literal["eq", "ne", "contains", "in", "gte", "lte", "between", "empty", "not_empty"] = "eq"
    value: Any = None


class Sort(BaseModel):
    column: str
    dir: Literal["asc", "desc"] = "asc"


class Source(BaseModel):
    object: str = Field(..., description="The template object whose rows the screen lists")
    include_pending_review: bool = Field(False, description="Also rows of records still waiting for a person's review")


class ScreenDefinition(BaseModel):
    source: Source
    columns: List[Column] = Field(..., min_length=1, max_length=MAX_COLUMNS)
    filters: List[Filter] = Field(default_factory=list, max_length=MAX_FILTERS)
    sort: List[Sort] = Field(default_factory=list, max_length=3)
    group_by: List[str] = Field(default_factory=list, max_length=4)
    totals: bool = Field(False, description="A totals row under the table, from the columns that have an aggregate")


# --- names the formulas and filters can use ---------------------------------------------

def slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "field"
    return s if s[0].isalpha() else f"f_{s}"


@dataclass
class Variables:
    """The values of a row a formula may use: its own fields by name, and the document's one-per-document fields."""
    own: Dict[str, str]        # variable -> field name
    document: Dict[str, str]   # variable -> 'Object.field'
    types: Dict[str, str]      # variable -> field type
    by_field: Dict[str, str]   # field name or 'Object.field' -> variable


def variables_for(schema: Optional[Dict[str, Any]], obj: str) -> Variables:
    objects = normalize_schema(schema)
    v = Variables({}, {}, {}, {})
    if obj in objects:
        for name, spec in objects[obj]["fields"].items():
            var = slug(name)
            v.own[var], v.types[var], v.by_field[name], v.by_field[f"{obj}.{name}"] = name, spec.get("type", "string"), var, var
    for other, spec in objects.items():
        if spec["multiple"] or other == obj:
            continue
        for name, fspec in spec["fields"].items():
            var = f"{slug(other)}__{slug(name)}"
            v.document[var], v.types[var], v.by_field[f"{other}.{name}"] = f"{other}.{name}", fspec.get("type", "string"), var
    return v


def catalogue(schema: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """What a builder (or the agent) can pick from: each object with its fields and types, and which are one per document."""
    objects = normalize_schema(schema)
    return {"objects": [{
        "type": t, "multiple": spec["multiple"],
        "fields": [{"name": n, "type": f.get("type", "string"), "variable": slug(n)} for n, f in spec["fields"].items()],
    } for t, spec in objects.items()]}


# --- checking a definition --------------------------------------------------------------

def validate_definition(d: ScreenDefinition, schema: Optional[Dict[str, Any]]) -> List[str]:
    problems: List[str] = []
    objects = normalize_schema(schema)
    if d.source.object not in objects:
        return [f"The template has no object '{d.source.object}'. Objects: {', '.join(objects) or 'none'}."]
    var = variables_for(schema, d.source.object)
    ids = [c.id for c in d.columns]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        problems.append(f"Two columns are called '{dup}'.")
    names = set(ids) | set(var.own) | set(var.document)
    grouped = bool(d.group_by)

    for c in d.columns:
        if c.kind == "field":
            if not c.field:
                problems.append(f"Column '{c.id}' needs a field.")
            elif c.field not in var.by_field:
                problems.append(f"Column '{c.id}': '{c.field}' is not a field of {d.source.object}"
                                f"{' or of its document' if var.document else ''}. Fields: {', '.join(sorted(var.by_field)) or 'none'}.")
        else:
            if not c.formula:
                problems.append(f"Column '{c.id}' needs a formula.")
                continue
            allowed = (set(ids) - {c.id}) if c.scope == "group" else names - {c.id}
            try:
                compile_formula(c.formula, allowed)
            except FormulaError as e:
                problems.append(f"Column '{c.id}': {e}")
            if c.scope == "group" and not grouped:
                problems.append(f"Column '{c.id}' works on groups, but the screen is not grouped.")
        for h in c.highlight:
            try:
                compile_formula(h.when, names)
            except FormulaError as e:
                problems.append(f"Column '{c.id}' highlight: {e}")
    problems += _cycles(d)

    for f in d.filters:
        if f.formula:
            try:
                compile_formula(f.formula, names)
            except FormulaError as e:
                problems.append(f"Filter: {e}")
        elif not f.field:
            problems.append("A filter needs a column or field (or a formula).")
        elif f.field not in names and f.field not in var.by_field:
            problems.append(f"Filter: '{f.field}' is neither a column nor a field. Columns: {', '.join(ids)}.")
        if f.op == "between" and not (isinstance(f.value, list) and len(f.value) == 2):
            problems.append("A 'between' filter needs two values, the lowest and the highest.")
    for s in d.sort:
        if s.column not in ids:
            problems.append(f"Sort: there is no column '{s.column}'.")
    for g in d.group_by:
        if g not in ids:
            problems.append(f"Group by: there is no column '{g}'.")
    if grouped:
        for c in d.columns:
            if c.id in d.group_by or c.aggregate or (c.kind == "formula" and c.scope == "group") or c.hidden:
                continue
            problems.append(f"Column '{c.id}' needs a total (sum, average, count...) or must be hidden, because the screen is grouped.")
    return problems


def _cycles(d: ScreenDefinition) -> List[str]:
    deps: Dict[str, Set[str]] = {}
    ids = {c.id for c in d.columns}
    for c in d.columns:
        if c.kind == "formula" and c.formula:
            try:
                deps[c.id] = set(compile_formula(c.formula).names) & ids - {c.id}
            except FormulaError:
                deps[c.id] = set()
    state: Dict[str, int] = {}

    def visit(n: str, path: List[str]) -> Optional[List[str]]:
        state[n] = 1
        for m in deps.get(n, ()):
            if state.get(m) == 1:
                return path + [m]
            if state.get(m) is None and m in deps:
                found = visit(m, path + [m])
                if found:
                    return found
        state[n] = 2
        return None

    for n in list(deps):
        if state.get(n) is None:
            loop = visit(n, [n])
            if loop:
                return [f"Columns use each other in a circle: {' -> '.join(loop)}."]
    return []


# --- running ----------------------------------------------------------------------------

@dataclass
class Loaded:
    document_id: str
    filename: str
    status: Any
    row: Dict[str, Any]
    context: Dict[str, Any]


def typed(value: Any, kind: str) -> Any:
    """A saved value as the template's type says it is."""
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return None
    if kind in ("number", "integer"):
        n = parse_number(value)
        if n is None:
            return str(value).strip()
        n = float(n)
        return int(n) if n.is_integer() else n
    if kind == "boolean":
        return str(value).strip().lower() in ("true", "yes", "1", "y") if not isinstance(value, bool) else value
    return str(value).strip() if isinstance(value, str) else value


def _order(ids: List[str], d: ScreenDefinition) -> List[str]:
    """Columns in the order they can be worked out: a formula after every column it uses."""
    by = {c.id: c for c in d.columns}
    deps: Dict[str, Set[str]] = {}
    for i in ids:
        deps[i] = set()
        if by[i].kind == "formula" and by[i].formula:
            try:
                deps[i] = set(compile_formula(by[i].formula).names) & set(by) - {i}
            except FormulaError:
                pass
    done: List[str] = []
    pending = list(ids)
    while pending:
        progressed = False
        for i in list(pending):
            if deps[i] <= set(done):
                done.append(i)
                pending.remove(i)
                progressed = True
        if not progressed:
            done += pending
            break
    return done


def _key(value: Any) -> Tuple[int, Any]:
    if value is None:
        return (2, 0)
    if isinstance(value, bool):
        return (0, float(value))
    if isinstance(value, (int, float)):
        return (0, float(value))
    return (1, str(value).lower())


def _clean(x: Any) -> Any:
    return int(x) if isinstance(x, float) and x.is_integer() and abs(x) < 1e15 else (round(x, 6) if isinstance(x, float) else x)


def _aggregate(kind: str, values: List[Any]) -> Any:
    present = [v for v in values if v is not None and v != ""]
    if kind == "count":
        return len(present)
    if kind == "count_distinct":
        return len({str(v).strip().lower() for v in present})
    numbers = [float(v) for v in present if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if not numbers:
        return None
    return _clean({"sum": sum(numbers), "avg": sum(numbers) / len(numbers), "min": min(numbers), "max": max(numbers)}[kind])


def _filter_matches(f: Filter, values: Dict[str, Any], compiled: Optional[Compiled]) -> bool:
    if compiled is not None:
        try:
            return bool(compiled.run(values))
        except Blank:
            return False
    actual = values.get(f.field or "")
    if f.op == "between":
        lo, hi = f.value
        return _matches(actual, Condition(field="x", op="gte", value=lo)) and _matches(actual, Condition(field="x", op="lte", value=hi))
    return _matches(actual, Condition(field="x", op=f.op, value=f.value))


class _Errors:
    def __init__(self) -> None:
        self.found: Dict[str, Dict[str, Any]] = {}

    def add(self, column: str, message: str) -> None:
        e = self.found.setdefault(column, {"column": column, "message": message, "count": 0})
        e["count"] += 1


def run_rows(loaded: Sequence[Loaded], schema: Optional[Dict[str, Any]], d: ScreenDefinition, *, documents_searched: int = 0,
             truncated: bool = False, offset: int = 0, limit: int = 100, extra_filters: Sequence[Filter] = (),
             extra_sort: Optional[Sequence[Sort]] = None, search: str = "") -> Dict[str, Any]:
    var = variables_for(schema, d.source.object)
    by = {c.id: c for c in d.columns}
    errors = _Errors()
    row_ids = _order([c.id for c in d.columns if not (c.kind == "formula" and c.scope == "group")], d)
    compiled: Dict[str, Compiled] = {}
    for c in d.columns:
        if c.kind == "formula" and c.formula:
            try:
                compiled[c.id] = compile_formula(c.formula)
            except FormulaError as e:
                errors.add(c.id, str(e))
    filters = list(d.filters) + list(extra_filters)
    filter_formulas: List[Optional[Compiled]] = []
    for f in filters:
        try:
            filter_formulas.append(compile_formula(f.formula) if f.formula else None)
        except FormulaError as e:
            errors.add("filter", str(e))
            filter_formulas.append(compile_formula("False"))
    highlights: Dict[str, List[Tuple[Compiled, str]]] = {}
    for c in d.columns:
        for h in c.highlight:
            try:
                highlights.setdefault(c.id, []).append((compile_formula(h.when), h.tone))
            except FormulaError:
                pass

    records: List[Dict[str, Any]] = []
    for item in loaded:
        values: Dict[str, Any] = {}
        for name, field_name in var.own.items():
            values[name] = typed(item.row.get(field_name), var.types.get(name, "string"))
        for name, doc_field in var.document.items():
            values[name] = typed(item.context.get(doc_field), var.types.get(name, "string"))
        for cid in row_ids:
            c = by[cid]
            if c.kind == "field":
                name = var.by_field.get(c.field or "")
                values[cid] = values.get(name) if name else None
                if name is None:
                    errors.add(cid, f"'{c.field}' is no longer in the template")
            else:
                fn = compiled.get(cid)
                try:
                    values[cid] = fn.run(values) if fn else None
                except Blank as e:
                    values[cid] = None
                    errors.add(cid, str(e))
        records.append({"item": item, "values": values})

    def keep(rec: Dict[str, Any]) -> bool:
        return all(_filter_matches(f, rec["values"], filter_formulas[i]) for i, f in enumerate(filters))

    records = [r for r in records if keep(r)]
    sort = list(extra_sort) if extra_sort is not None else list(d.sort)
    grouped = bool(d.group_by)
    visible = [c for c in d.columns if not c.hidden]
    words = [w for w in (search or "").lower().split() if w]

    def mentions(cells: Dict[str, Any], extra: str = "") -> bool:
        """Every word typed in the search box appears in what the row shows (any column, any order, any case)."""
        hay = " ".join([str(cells.get(c.id)) for c in visible if cells.get(c.id) is not None] + [extra]).lower()
        return all(w in hay for w in words)

    if words and not grouped:
        records = [r for r in records if mentions(r["values"], r["item"].filename or "")]

    if grouped:
        groups: Dict[Tuple, List[Dict[str, Any]]] = defaultdict(list)
        for r in records:
            groups[tuple(str(r["values"].get(g, "")).strip().lower() for g in d.group_by)].append(r)
        out: List[Dict[str, Any]] = []
        for members in groups.values():
            cells: Dict[str, Any] = {}
            for c in d.columns:
                if c.id in d.group_by:
                    cells[c.id] = members[0]["values"].get(c.id)
                elif c.aggregate:
                    cells[c.id] = _aggregate(c.aggregate, [m["values"].get(c.id) for m in members])
            for cid in _order([c.id for c in d.columns if c.kind == "formula" and c.scope == "group"], d):
                fn = compiled.get(cid)
                try:
                    cells[cid] = fn.run(cells) if fn else None
                except Blank as e:
                    cells[cid] = None
                    errors.add(cid, str(e))
            out.append({"cells": cells, "size": len(members), "document_id": None, "filename": None, "pending_review": False, "_members": members})
        if words:  # the groups that mention it; the totals then cover just those
            out = [g for g in out if mentions(g["cells"])]
            records = [m for g in out for m in g["_members"]]
        for g in out:
            g.pop("_members")
        rows = out
    else:
        rows = [{"cells": {c.id: r["values"].get(c.id) for c in d.columns}, "tones": _tones(r["values"], highlights),
                 "document_id": r["item"].document_id, "filename": r["item"].filename,
                 "pending_review": r["item"].status == DocumentStatus.NEEDS_REVIEW} for r in records]

    for s in reversed(sort):
        if s.column in by:
            rows.sort(key=lambda row: _key(row["cells"].get(s.column)), reverse=s.dir == "desc")
            # blanks stay last either way
            rows.sort(key=lambda row: row["cells"].get(s.column) is None)

    totals = None
    if d.totals and any(c.aggregate for c in visible):
        totals = {c.id: _aggregate(c.aggregate, [r["values"].get(c.id) for r in records]) for c in visible if c.aggregate}

    page = rows[offset: offset + limit]
    for n, row in enumerate(page, start=offset):
        row["id"] = f"{row['document_id']}:{n}" if row["document_id"] else f"group:{n}"
        row["cells"] = {c.id: _clean(row["cells"].get(c.id)) for c in visible}
        row.setdefault("tones", {})
    return {
        "columns": [{"id": c.id, "label": c.label, "kind": c.kind, "format": c.format.model_dump(exclude_none=True), "width": c.width,
                     "aggregate": c.aggregate, "formula": c.formula if c.kind == "formula" else None} for c in visible],
        "rows": page, "totals": ({k: _clean(v) for k, v in totals.items()} if totals else None),
        "total_rows": len(rows), "offset": offset, "limit": limit, "grouped": grouped,
        "documents_searched": documents_searched, "records_truncated": truncated,
        "errors": sorted(errors.found.values(), key=lambda e: e["column"]),
    }


def _tones(values: Dict[str, Any], highlights: Dict[str, List[Tuple[Compiled, str]]]) -> Dict[str, str]:
    tones: Dict[str, str] = {}
    for cid, rules in highlights.items():
        for fn, tone in rules:
            try:
                if fn.run(values):
                    tones[cid] = tone
                    break
            except Blank:
                continue
    return tones


async def run_screen(db: AsyncSession, *, template_id: str, schema: Optional[Dict[str, Any]], definition: ScreenDefinition,
                     hidden: Sequence[str] = (), offset: int = 0, limit: int = 100, extra_filters: Sequence[Filter] = (),
                     extra_sort: Optional[Sequence[Sort]] = None, page_cap: int = MAX_PAGE, search: str = "") -> Dict[str, Any]:
    """Runs a definition over the workspace's saved records. The caller has checked it with validate_definition."""
    limit = max(1, min(limit, page_cap))
    records = await saved_records(db, template_id=template_id, include_pending_review=definition.source.include_pending_review, hidden=hidden)
    truncated = len(records) > MAX_RECORDS
    objects = normalize_schema(schema)
    loaded: List[Loaded] = []
    for doc_id, filename, status, data in records[:MAX_RECORDS]:
        context = _context(objects, data or {})
        for row in rows_of(data or {}, definition.source.object):
            loaded.append(Loaded(doc_id, filename, status, row, context))
    return run_rows(loaded, schema, definition, documents_searched=min(len(records), MAX_RECORDS), truncated=truncated,
                    offset=max(0, offset), limit=limit, extra_filters=extra_filters, extra_sort=extra_sort, search=search)


def to_csv(result: Dict[str, Any]) -> str:
    """The page of rows (and totals) as CSV text, for people who want it in a spreadsheet."""
    import csv
    import io
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow([c["label"] for c in result["columns"]])
    for r in result["rows"]:
        w.writerow(["" if r["cells"].get(c["id"]) is None else r["cells"][c["id"]] for c in result["columns"]])
    if result.get("totals"):
        row = ["" if result["totals"].get(c["id"]) is None else result["totals"][c["id"]] for c in result["columns"]]
        if not result["columns"][0].get("aggregate"):
            row[0] = "Total"  # the first column says what the row is
        w.writerow(row)
    return out.getvalue()
