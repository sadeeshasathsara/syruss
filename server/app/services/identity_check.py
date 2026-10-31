"""
Identity consistency: the guard against "recognizing" someone who is not on the page.

Template objects may declare their identity:
    {"type": "Guard", "identity": {"id_field": "employee_no", "label_field": "name"}, ...}

Before an agent COMMIT, every identified record is compared with what the workspace already
knows (registered entities with their aliases, and committed records of other documents):
  * an ID already known under a clearly different name, or
  * a name already known under a different ID
is a conflict. Conflicts don't say the agent is wrong (people share names; IDs get misread),
only that a person must confirm, so the record cannot be committed silently.
"""
import difflib
import re
from collections import defaultdict
from typing import Any, Dict, List, Set
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.document import Document, DocumentStatus
from app.models.extraction import ExtractionResult
from app.models.knowledge import Entity

SAME_NAME_THRESHOLD = 0.75


def normalize_label(value: Any) -> str:
    return re.sub(r"[\W_]+", "", str(value or "")).lower()


def normalize_id(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).upper()


def _similar(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a in b or b in a:
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= SAME_NAME_THRESHOLD


def _identities(schema: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    out = {}
    for obj in (schema or {}).get("objects", []) or []:
        ident = obj.get("identity") if isinstance(obj, dict) else None
        if isinstance(ident, dict) and ident.get("id_field") and ident.get("label_field"):
            out[obj["type"]] = ident
    return out


def _rows(data: Dict[str, Any], object_type: str) -> List[Dict[str, Any]]:
    value = data.get(object_type)
    if isinstance(value, dict):
        return [value]
    return [r for r in value or [] if isinstance(r, dict)] if isinstance(value, list) else []


async def find_identity_conflicts(
    db: AsyncSession, *, template_id: str, schema: Dict[str, Any], data: Dict[str, Any], document_id: str,
) -> List[Dict[str, str]]:
    identities = {t: i for t, i in _identities(schema).items() if t in data}
    if not identities:
        return []

    labels_by_id: Dict[str, Set[str]] = defaultdict(set)   # normalized id -> normalized labels
    ids_by_label: Dict[str, Set[str]] = defaultdict(set)   # normalized label -> normalized ids
    shown: Dict[str, str] = {}                              # normalized -> as written, for messages

    def learn(raw_id: Any, raw_label: Any) -> None:
        i, l = normalize_id(raw_id), normalize_label(raw_label)
        if i and l:
            labels_by_id[i].add(l)
            ids_by_label[l].add(i)
            shown.setdefault(i, str(raw_id))
            shown.setdefault(l, str(raw_label))

    for e in (await db.execute(select(Entity).where(Entity.template_id == template_id))).scalars().all():
        for label in [e.name] + [a.alias for a in e.aliases]:
            learn(e.identifier_value, label)

    past = await db.execute(
        select(ExtractionResult.extracted_data)
        .join(Document, Document.id == ExtractionResult.document_id)
        .where(ExtractionResult.template_id == template_id, Document.status == DocumentStatus.COMPLETED,
               Document.id != document_id)
    )
    for (record,) in past.all():
        for obj_type, ident in identities.items():
            for row in _rows(record or {}, obj_type):
                learn(row.get(ident["id_field"]), row.get(ident["label_field"]))

    conflicts = []
    for obj_type, ident in identities.items():
        for i, row in enumerate(_rows(data, obj_type)):
            raw_id, raw_label = row.get(ident["id_field"]), row.get(ident["label_field"])
            nid, nlabel = normalize_id(raw_id), normalize_label(raw_label)
            if not nid or not nlabel:
                continue
            known_labels = labels_by_id.get(nid, set())
            if known_labels and not any(_similar(nlabel, k) for k in known_labels):
                names = ", ".join(sorted(shown.get(k, k) for k in known_labels))
                conflicts.append({
                    "code": "identity_conflict", "path": f"{obj_type}[{i}]",
                    "message": f"{ident['id_field']} {raw_id} is already known as {names}, not '{raw_label}'.",
                })
            other_ids = {k for l, ids in ids_by_label.items() if l == nlabel for k in ids} - {nid}
            if other_ids:
                ids = ", ".join(sorted(shown.get(k, k) for k in other_ids))
                conflicts.append({
                    "code": "identity_conflict", "path": f"{obj_type}[{i}]",
                    "message": f"'{raw_label}' is already known with {ident['id_field']} {ids}, not {raw_id}.",
                })
    return conflicts


async def find_unsupported_identities(
    db: AsyncSession, *, template_id: str, schema: Dict[str, Any], data: Dict[str, Any],
    evidence: List[Dict[str, Any]], document_id: str,
) -> List[Dict[str, str]]:
    """
    Every identity name the agent commits must be supported by the page:
      * its evidence `raw_text` (what is literally written) matches the value, or
      * it cites a known entity (`entity_id`) that independently links the ink to the value:
        the entity was not created from this document, the value is its name or an alias, and
        either the ID on the page is the entity's ID or the raw text matches its name or an alias.
    This is what stops "recognizing" a name that is not on the page.
    """
    identities = {t: i for t, i in _identities(schema).items() if t in data}
    if not identities:
        return []
    by_key = {}
    for ev in evidence or []:
        if isinstance(ev, dict):
            by_key[(ev.get("object"), ev.get("index"), ev.get("field"))] = ev

    problems = []
    for obj_type, ident in identities.items():
        for i, row in enumerate(_rows(data, obj_type)):
            value = row.get(ident["label_field"])
            if not normalize_label(value):
                continue
            path = f"{obj_type}[{i}].{ident['label_field']}"
            ev = by_key.get((obj_type, i, ident["label_field"]))
            if not ev or not str(ev.get("raw_text") or "").strip():
                problems.append({"code": "identity_evidence_missing", "path": path, "message":
                                 f"Give evidence with raw_text (exactly what is written) for {path}."})
                continue
            raw = normalize_label(ev["raw_text"])
            if _similar(normalize_label(value), raw):
                continue
            entity = await db.get(Entity, ev["entity_id"]) if ev.get("entity_id") else None
            if entity and entity.template_id == template_id and entity.source_document_id != document_id:
                known = [normalize_label(n) for n in [entity.name] + [a.alias for a in entity.aliases]]
                value_is_entity = any(_similar(normalize_label(value), k) for k in known)
                ink_links = (normalize_id(row.get(ident["id_field"])) == normalize_id(entity.identifier_value)
                             and entity.identifier_value) or any(_similar(raw, k) for k in known)
                if value_is_entity and ink_links:
                    continue
            problems.append({"code": "identity_unsupported", "path": path, "message":
                             f"'{value}' is not what is written ('{ev['raw_text']}') and no known entity independently "
                             "confirms it. Record what is written, or send the document to review."})
    return problems
