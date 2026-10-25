"""
Questions across records, answered from the database in one call.

"Who worked the day shift", "which documents mention site S12", "how many hours did Perera log in
July" are questions about rows of one template object across every saved record. The agent asks
them with a filter instead of opening records one by one (or looking at pages):

  object    the template object whose rows are wanted (e.g. the people on a roster)
  where     conditions on the row's fields, or on fields of the document's one-per-document objects
            written "Object.field" (e.g. the roster's date). Comparisons ignore case and spaces at
            the ends; gte/lte compare numbers as numbers and everything else as text (so ISO dates
            and HH:MM times order correctly).
  distinct  fields to group by: unique combinations with how often and in how many documents
            (e.g. the unique workers matching the filter)

When nothing matches, the values that do occur in the filtered fields are returned, so the agent
can see how the documents write them ("D" rather than "Day") and ask again.
"""
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple
from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.document import Document, DocumentRole, DocumentStatus
from app.models.extraction import ExtractionResult
from app.services.graph_service import rows_of
from app.services.rule_engine import normalize_schema

MAX_RECORDS = 5000
OPS = ("eq", "ne", "contains", "in", "gte", "lte", "empty", "not_empty")


class Condition(BaseModel):
    field: str = Field(..., description="A field of the object's rows, or 'Object.field' of a one-per-document object")
    op: str = Field("eq", description=", ".join(OPS))
    value: Any = None


class RecordQuery(BaseModel):
    object: str = Field(..., description="The template object whose rows you want")
    where: List[Condition] = Field(default_factory=list)
    distinct: List[str] = Field(default_factory=list, description="Fields to group by (unique combinations)")
    fields: List[str] = Field(default_factory=list, description="Only these fields in the rows (default: all)")
    limit: int = Field(100, ge=1, le=500)
    include_pending_review: bool = Field(False, description="Also records waiting for a person's review")


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip().lower()


def _number(value: Any) -> Optional[float]:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return None


def _matches(actual: Any, c: Condition) -> bool:
    a = _text(actual)
    if c.op == "empty":
        return a == ""
    if c.op == "not_empty":
        return a != ""
    if c.op == "in":
        values = c.value if isinstance(c.value, list) else [c.value]
        return a in {_text(v) for v in values}
    v = _text(c.value)
    if c.op == "eq":
        return a == v
    if c.op == "ne":
        return a != v
    if c.op == "contains":
        return v in a
    if a == "":
        return False
    an, vn = _number(actual), _number(c.value)
    left, right = (an, vn) if an is not None and vn is not None else (a, v)
    return left >= right if c.op == "gte" else left <= right


def _context(objects: Dict[str, Dict[str, Any]], data: Dict[str, Any]) -> Dict[str, Any]:
    """The one-per-document objects' fields, as "Object.field": what the document as a whole is about."""
    out: Dict[str, Any] = {}
    for obj_type, spec in objects.items():
        if not spec["multiple"]:
            for row in rows_of(data, obj_type)[:1]:
                out.update({f"{obj_type}.{k}": v for k, v in row.items()})
    return out


async def saved_records(db: AsyncSession, *, template_id: str, include_pending_review: bool = False, hidden: Sequence[str] = ()):
    """Every saved record of the workspace, newest document first: (document id, file name, status, extracted data).
    One more than MAX_RECORDS is fetched so callers can tell the list was cut."""
    statuses = [DocumentStatus.COMPLETED] + ([DocumentStatus.NEEDS_REVIEW] if include_pending_review else [])
    return (await db.execute(
        select(Document.id, Document.original_filename, Document.status, ExtractionResult.extracted_data)
        .join(ExtractionResult, ExtractionResult.document_id == Document.id)
        .where(Document.template_id == template_id, Document.role == DocumentRole.DOCUMENT,
               Document.status.in_(statuses), Document.id.not_in(list(hidden)))
        .order_by(Document.created_at.desc()).limit(MAX_RECORDS + 1)
    )).all()


async def query(db: AsyncSession, *, template_id: str, schema: Dict[str, Any], q: RecordQuery,
                hidden: Sequence[str] = ()) -> Dict[str, Any]:
    objects = normalize_schema(schema)
    if q.object not in objects:
        raise HTTPException(status_code=400, detail=f"No object '{q.object}' in the template. Objects: {', '.join(objects)}.")
    row_fields = set(objects[q.object]["fields"])
    context_fields = {f"{o}.{f}" for o, s in objects.items() if not s["multiple"] for f in s["fields"]}
    own_prefixed = {f"{q.object}.{f}" for f in row_fields}
    known = row_fields | context_fields | own_prefixed
    for name in [c.field for c in q.where] + q.distinct + q.fields:
        if name not in known:
            raise HTTPException(status_code=400, detail=(
                f"Unknown field '{name}'. {q.object} fields: {', '.join(sorted(row_fields))}"
                + (f"; document fields: {', '.join(sorted(context_fields))}" if context_fields else "") + "."))
    for c in q.where:
        if c.op not in OPS:
            raise HTTPException(status_code=400, detail=f"Unknown op '{c.op}'. Use one of: {', '.join(OPS)}.")

    records = await saved_records(db, template_id=template_id, include_pending_review=q.include_pending_review, hidden=hidden)

    def value(row: Dict[str, Any], context: Dict[str, Any], name: str) -> Any:
        if name in row_fields:
            return row.get(name)
        if name in own_prefixed:
            return row.get(name.split(".", 1)[1])
        return context.get(name)

    matched: List[Dict[str, Any]] = []
    groups: Dict[Tuple, Dict[str, Any]] = {}
    group_docs: Dict[Tuple, Set[str]] = defaultdict(set)
    seen_values: Dict[str, Counter] = defaultdict(Counter)
    documents_with_rows: Set[str] = set()
    for doc_id, filename, status, data in records[:MAX_RECORDS]:
        context = _context(objects, data or {})
        for row in rows_of(data or {}, q.object):
            for c in q.where:
                if c.op not in ("empty", "not_empty"):
                    seen_values[c.field][str(value(row, context, c.field) or "").strip()] += 1
            if not all(_matches(value(row, context, c.field), c) for c in q.where):
                continue
            documents_with_rows.add(doc_id)
            if q.distinct:
                key = tuple(_text(value(row, context, f)) for f in q.distinct)
                groups.setdefault(key, {"values": {f: value(row, context, f) for f in q.distinct}, "rows": 0})
                groups[key]["rows"] += 1
                group_docs[key].add(doc_id)
            item = {f: value(row, context, f) for f in q.fields} if q.fields else dict(row)
            matched.append({"document_id": doc_id, "filename": filename,
                            **({"pending_review": True} if status == DocumentStatus.NEEDS_REVIEW else {}),
                            "document": context, "row": item})

    result: Dict[str, Any] = {
        "object": q.object,
        "documents_searched": min(len(records), MAX_RECORDS),
        "documents_matched": len(documents_with_rows),
        "rows_matched": len(matched),
        "rows": matched[: q.limit],
        "rows_truncated": len(matched) > q.limit,
    }
    if q.distinct:
        result["distinct"] = sorted(
            ({**g, "documents": len(group_docs[k])} for k, g in groups.items()), key=lambda g: (-g["rows"], str(g["values"]))
        )[:500]
    if len(records) > MAX_RECORDS:
        result["note"] = f"Only the newest {MAX_RECORDS} records were searched."
    if not matched and q.where:
        result["values_seen"] = {f: [v for v, _ in counts.most_common(25)] for f, counts in seen_values.items()}
        result["hint"] = "Nothing matched. These are the values the documents actually have; adjust the filter (op 'in' or 'contains')."
    return result
