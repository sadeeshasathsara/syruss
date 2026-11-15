"""
The index of what each committed record contains, object by object (`record_objects`), behind the object and thing
profiles and search.

A record is indexed when it is committed and forgotten when it isn't any more (record_service). A whole workspace is
indexed again by the scheduler's "objects" job: on its first pass (records committed before the index existed) and
whenever the active template version changes (an object gained or lost its identity, or was renamed).
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document, DocumentStatus
from app.models.extraction import ExtractionResult
from app.models.graph import RecordObject
from app.services.graph_service import rows_of, thing_key, things_in_row
from app.services.rules.engine import normalize_schema

MAX_VALUES_BYTES = 4096


def _values(row: Dict[str, Any]) -> Dict[str, Any]:
    """The row's attributes, kept small: the largest values are dropped until it fits."""
    out = {k: v for k, v in row.items() if v not in (None, "")}
    while out and len(json.dumps(out, default=str)) > MAX_VALUES_BYTES:
        out.pop(max(out, key=lambda k: len(json.dumps(out[k], default=str))))
    return out


def entries(schema: Optional[Dict[str, Any]], data: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """What a record contains, as index rows (without document, tenant and time)."""
    objects = normalize_schema(schema)
    data = data or {}
    found: Dict[Tuple[str, Optional[str]], Dict[str, Any]] = {}

    def at(obj_type: str, key: Optional[str], label: str = "") -> Dict[str, Any]:
        e = found.setdefault((obj_type, key), {"object_type": obj_type, "thing_key": key, "label": "", "rows": 0, "values": None})
        if label and not e["label"]:
            e["label"] = label[:255]
        return e

    for obj_type, spec in objects.items():
        ident = spec["identity"]
        for row in rows_of(data, obj_type):
            own = thing_key(obj_type, row.get(ident["id_field"])) if ident else None
            e = at(obj_type, own, str(row.get(ident["label_field"]) or "") if ident else "")
            e["rows"] += 1
            if own and e["values"] is None:
                e["values"] = _values(row)
            # The things this row refers to are mentioned by the record too, even without a row of their own
            for thing, _field in things_in_row(objects, data, obj_type, row):
                if thing.key != own:
                    at(thing.type, thing.key, thing.label)
    for e in found.values():
        if e["thing_key"] and not e["label"]:
            e["label"] = e["thing_key"].split(":", 1)[1][:255]
    return list(found.values())


async def index_record(db: AsyncSession, *, tenant_id: str, template_id: str, document_id: str, seen_at: datetime,
                       schema: Optional[Dict[str, Any]], data: Optional[Dict[str, Any]]) -> int:
    """Replaces the document's index rows with what its committed record says."""
    await db.execute(delete(RecordObject).where(RecordObject.document_id == document_id))
    rows = entries(schema, data)
    for e in rows:
        db.add(RecordObject(tenant_id=tenant_id, template_id=template_id, document_id=document_id, seen_at=seen_at, **e))
    await db.flush()
    return len(rows)


async def forget_record(db: AsyncSession, document_id: str) -> None:
    await db.execute(delete(RecordObject).where(RecordObject.document_id == document_id))


async def rebuild(db: AsyncSession, *, tenant_id: str, template_id: str, schema: Optional[Dict[str, Any]]) -> int:
    """The index for every committed record of a workspace."""
    await db.execute(delete(RecordObject).where(RecordObject.template_id == template_id))
    result = await db.execute(
        select(ExtractionResult.document_id, ExtractionResult.extracted_data, Document.created_at)
        .join(Document, Document.id == ExtractionResult.document_id)
        .where(ExtractionResult.template_id == template_id, Document.status == DocumentStatus.COMPLETED)
    )
    n = 0
    for document_id, data, created in result.all():
        for e in entries(schema, data):
            db.add(RecordObject(tenant_id=tenant_id, template_id=template_id, document_id=document_id, seen_at=created, **e))
            n += 1
    await db.flush()
    return n
