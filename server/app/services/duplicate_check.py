"""
Has this document been recorded already?

When the agent submits or validates a record, it is compared with what is already in the database:
  * the same file: identical bytes uploaded before and already read;
  * the same content: at least 75% of the identified people and things in this record (template
    objects with an identity, and references to them) are in an existing record, and the details
    that describe the document as a whole (objects that appear once per document, such as the site
    and date of a roster) match too. Without that second condition every day's roster of the same
    team would look like a copy of the day before.
A match is a blocking signal carrying the existing record as evidence. The agent double-checks:
if it is the same document it sends this one to review for a person to decide; if it is sure the
document is different, it acknowledges the signal with what differs, and the commit goes through.
"""
from typing import Any, Dict, List, Optional, Set
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.document import Document, DocumentRole, DocumentStatus
from app.models.extraction import ExtractionResult
from app.models.graph import EntitySighting
from app.services.benchmark_service import _flatten
from app.services.graph_service import rows_of, signal_id, things_in_row
from app.services.rule_engine import normalize_schema

THRESHOLD = 0.75
MAX_CANDIDATES = 20
EVIDENCE_ROWS = 30
RECORDED = (DocumentStatus.COMPLETED, DocumentStatus.NEEDS_REVIEW)


def identity_keys(schema: Dict[str, Any], data: Dict[str, Any]) -> Set[str]:
    objects = normalize_schema(schema)
    return {thing.key for obj_type in objects for row in rows_of(data, obj_type)
            for thing, _ in things_in_row(objects, data, obj_type, row)}


def document_details(schema: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, str]:
    """Values of the objects that appear once per document: what the document as a whole is about."""
    out: Dict[str, str] = {}
    for obj_type, spec in normalize_schema(schema).items():
        if not spec["multiple"]:
            for row in rows_of(data, obj_type)[:1]:
                out.update({f"{obj_type}.{k}": v for k, v in _flatten(row).items()})
    return out


def _share_equal(mine: Dict[str, str], theirs: Dict[str, str]) -> float:
    return sum(1 for k, v in mine.items() if theirs.get(k) == v) / len(mine) if mine else 0.0


def _evidence(doc: Document, record: Optional[Dict[str, Any]], shared: List[str]) -> Dict[str, Any]:
    trimmed = {k: (v[:EVIDENCE_ROWS] if isinstance(v, list) else v) for k, v in (record or {}).items()}
    return {"document_id": doc.id, "filename": doc.original_filename, "status": doc.status.value,
            "uploaded_at": doc.created_at.isoformat() if doc.created_at else None,
            "shared": shared[:20], "record": trimmed}


def _status_words(doc: Document) -> str:
    return "saved" if doc.status == DocumentStatus.COMPLETED else "waiting for review"


async def duplicate_signals(db: AsyncSession, *, template_id: str, schema: Dict[str, Any], data: Dict[str, Any],
                            document: Optional[Document]) -> List[Dict[str, Any]]:
    document_id = document.id if document else ""
    signals: List[Dict[str, Any]] = []
    flagged: Set[str] = set()

    if document is not None and document.file_hash:
        same_file = (await db.execute(select(Document).where(
            Document.template_id == template_id, Document.file_hash == document.file_hash, Document.id != document_id,
            Document.role == DocumentRole.DOCUMENT, Document.status.in_(RECORDED),
        ).order_by(Document.created_at).limit(3))).scalars().all()
        for other in same_file:
            flagged.add(other.id)
            signals.append({
                "id": signal_id("duplicate_file", "document", other.id), "code": "duplicate_file", "path": "document",
                "blocking": True, "other": other.id,
                "message": (f"This is the same file as {other.original_filename}, which is already {_status_words(other)} "
                            "(identical content). If it was uploaded twice by mistake, send this one to review so a person "
                            "can remove it; acknowledge only if you know why the same page must be recorded again."),
                "evidence": _evidence(other, other.extraction.extracted_data if other.extraction else None, []),
            })

    keys = identity_keys(schema, data)
    details = document_details(schema, data)
    if keys:
        ranked = await db.execute(
            select(EntitySighting.document_id, func.count(func.distinct(EntitySighting.subject_key)).label("shared"))
            .where(EntitySighting.template_id == template_id, EntitySighting.subject_key.in_(keys),
                   EntitySighting.document_id != document_id)
            .group_by(EntitySighting.document_id).order_by(func.count(func.distinct(EntitySighting.subject_key)).desc())
            .limit(MAX_CANDIDATES)
        )
        candidate_ids = [d for d, shared in ranked.all() if shared / len(keys) >= THRESHOLD]
    elif details:
        candidate_ids = list((await db.execute(
            select(ExtractionResult.document_id).join(Document, Document.id == ExtractionResult.document_id)
            .where(ExtractionResult.template_id == template_id, Document.status.in_(RECORDED),
                   Document.id != document_id, Document.role == DocumentRole.DOCUMENT)
            .order_by(ExtractionResult.updated_at.desc()).limit(200)
        )).scalars().all())
    else:
        candidate_ids = []

    for other_id in candidate_ids:
        if other_id in flagged or len(signals) >= 3:
            continue
        other = await db.get(Document, other_id)
        record = other.extraction.extracted_data if other and other.extraction else None
        if not other or other.status not in RECORDED or not record:
            continue
        theirs = identity_keys(schema, record)
        shared = sorted(keys & theirs)
        if keys and len(shared) / len(keys) < THRESHOLD:
            continue
        same_details = _share_equal(details, document_details(schema, record))
        if details and same_details < THRESHOLD:
            continue  # same people, different document (another day, another site)
        matched = f"{len(shared) / len(keys):.0%} of the people and things in it" if keys else "the document's details"
        context = f", with the same {', '.join(sorted({k.split('.', 1)[1].replace('_', ' ') for k in details}))}" if details and keys else ""
        signals.append({
            "id": signal_id("possible_duplicate", "record", other.id), "code": "possible_duplicate", "path": "record",
            "blocking": True, "other": other.id,
            "message": (f"This looks like {other.original_filename}, already {_status_words(other)}: {matched} match "
                        f"an existing record{context}. Compare with the record in the evidence. If it is the same document, "
                        "send this one to review so a person can decide; if you're sure it's a different document, "
                        "acknowledge this signal and say what differs."),
            "evidence": _evidence(other, record, [k.split(":", 1)[1] for k in shared]),
        })
    return signals
