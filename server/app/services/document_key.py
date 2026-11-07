"""
How a document is named to people: its identification key.

A template can say which values identify one document, e.g. the site and the date of a roster, shown
as "Colombo Port_2026-07-22" instead of "94771079086_20260722_223851.jpeg". The key is built from fields of the
objects that appear once per document (the details that describe the page as a whole), in the order the
template lists them. The agent proposes it with the template; a person can change it at any time.

    "identification": {"parts": [{"object": "Roster", "field": "location"}, {"object": "Roster", "field": "date"}],
                       "separator": "_"}

The key is worked out from the record when it is needed, never stored, so changing the parts renames every
document at once. A document that has no record yet, or whose record lacks the values, keeps its file name.
"""
from typing import Any, Dict, Iterable, List, Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.document import Document
from app.services.rules.engine import normalize_schema

DEFAULT_SEPARATOR = "_"
MAX_PARTS = 5
MAX_LENGTH = 120


def _rows(data: Dict[str, Any], obj_type: str) -> List[Dict[str, Any]]:
    value = data.get(obj_type)
    if isinstance(value, dict):
        return [value]
    return [r for r in value or [] if isinstance(r, dict)] if isinstance(value, list) else []


def identification_spec(schema: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The template's identification as {"parts": [{"object", "field"}], "separator"}; no parts when none is set."""
    raw = (schema or {}).get("identification")
    if not isinstance(raw, dict):
        return {"parts": [], "separator": DEFAULT_SEPARATOR}
    parts = [{"object": p["object"], "field": p["field"]} for p in raw.get("parts") or []
             if isinstance(p, dict) and p.get("object") and p.get("field")]
    separator = raw.get("separator")
    return {"parts": parts, "separator": separator if isinstance(separator, str) else DEFAULT_SEPARATOR}


def check_identification(schema: Optional[Dict[str, Any]]) -> List[str]:
    """Problems with the template's identification; each part must be a field of a one-per-document object."""
    if (schema or {}).get("identification") is None:
        return []
    raw = schema["identification"]
    if not isinstance(raw, dict) or not isinstance(raw.get("parts"), list):
        return ["identification must be {\"parts\": [{\"object\": ..., \"field\": ...}], \"separator\": ...}."]
    separator = raw.get("separator")
    problems: List[str] = [] if separator is None or isinstance(separator, str) else ["identification.separator must be text."]
    parts = raw["parts"]
    if not parts:
        problems.append("identification needs at least one part.")
    if len(parts) > MAX_PARTS:
        problems.append(f"identification can combine at most {MAX_PARTS} fields.")
    objects = normalize_schema(schema)
    seen = set()
    for part in parts:
        obj, field = (part.get("object"), part.get("field")) if isinstance(part, dict) else (None, None)
        spec = objects.get(obj)
        if spec is None:
            problems.append(f"identification uses unknown object '{obj}'.")
        elif spec["multiple"]:
            problems.append(f"identification uses '{obj}', which appears many times in a document; "
                            "use an object that appears once per document.")
        elif field not in spec["fields"]:
            problems.append(f"identification uses '{obj}.{field}', which is not a field of '{obj}'.")
        elif (obj, field) in seen:
            problems.append(f"identification lists '{obj}.{field}' twice.")
        seen.add((obj, field))
    return problems


def identification_key(schema: Optional[Dict[str, Any]], data: Optional[Dict[str, Any]]) -> Optional[str]:
    """The key for this record, or None when the template has none or the record has no value for any part."""
    spec = identification_spec(schema)
    if not spec["parts"] or not isinstance(data, dict):
        return None
    values: List[str] = []
    for part in spec["parts"]:
        rows = _rows(data, part["object"])
        value = rows[0].get(part["field"]) if rows else None
        text = " ".join(str(value).split()) if value not in (None, "") and not isinstance(value, (dict, list)) else ""
        if text:
            values.append(text)
    if not values:
        return None
    return spec["separator"].join(values)[:MAX_LENGTH]


def document_label(document: Any) -> str:
    """What to call a document when talking to people: its key, else its file name."""
    return getattr(document, "display_name", None) or document.original_filename


def label_for(document: Any, schema: Optional[Dict[str, Any]], data: Optional[Dict[str, Any]]) -> str:
    """Like document_label, for a record that was only just written (the document's relations may not show it yet)."""
    return identification_key(schema, data) or document.identified_key or document.original_filename


async def labels_for(db: AsyncSession, document_ids: Iterable[str]) -> Dict[str, str]:
    """{document id: what to call it now}, for the documents that exist."""
    ids = {i for i in document_ids if i}
    if not ids:
        return {}
    docs = (await db.execute(select(Document).where(Document.id.in_(ids)))).scalars().all()
    return {d.id: document_label(d) for d in docs}
