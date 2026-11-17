"""
Object and thing profiles: one page for each object of a workspace's template and one for each thing it has met.

- An object's profile: its icon and attributes (fields), how many documents have it, its figures (documents per week,
  how often each attribute is filled, number totals, the commonest values), its things and its documents.
- A thing's profile (an identified instance of an object with an identity): its latest attributes, when it was first
  and last seen, its documents, what it appears with, and what the workspace's knowledge says about it.

Counts and lists come from the record index (`record_objects`, app.services.object_index); attribute figures from the
saved records themselves (app.services.record_query, the same cap as the Analytics screens). Both the customer API
(api/v1/objects) and the staff API (api/v1/sysadmin/objects) serve these.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from fastapi import HTTPException
from sqlalchemy import String, cast, distinct, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
from app.models.extraction import ExtractionResult
from app.models.graph import RecordObject
from app.models.knowledge import Entity
from app.models.screen import WorkspaceScreen
from app.models.template import DocumentTemplate
from app.services import graph_service, object_icons
from app.services.graph_service import rows_of, thing_key
from app.services.identity_check import normalize_id
from app.services.record_query import MAX_RECORDS, saved_records
from app.services.rules.engine import normalize_schema

WEEKS = 12
PAGE = 20
RECORD_PAGE = 25        # rows of the records table
FACET_SAMPLE = 2000     # the newest rows its filter choices are worked out from
FACET_MAX = 12          # a field with more different values than this isn't offered as a filter
SINCE_DAYS = (7, 30, 90, 365)
NUMBER_TYPES = ("number", "integer")


def thing_id(key: str) -> str:
    """The part of a thing's key that goes in its address."""
    return key.split(":", 1)[1] if ":" in key else key


def thing_path(obj_type: str, key: str) -> str:
    return f"objects/{quote(obj_type, safe='')}/things/{quote(thing_id(key), safe='')}"


def _objects(template: DocumentTemplate) -> Dict[str, Dict[str, Any]]:
    return normalize_schema(template.schema_definition)


def _object(template: DocumentTemplate, obj_type: str) -> Dict[str, Any]:
    spec = _objects(template).get(obj_type)
    if spec is None:
        raise HTTPException(status_code=404, detail={"code": "no_such_object", "message": f'This workspace\'s template has no object "{obj_type}".'})
    return spec


def _definition(obj_type: str, spec: Dict[str, Any]) -> Dict[str, Any]:
    ident = spec["identity"]
    return {
        "type": obj_type, "description": spec.get("description") or "", "icon": object_icons.icon_of(spec),
        "icon_chosen": object_icons.is_icon(spec.get("icon")), "multiple": bool(spec["multiple"]),
        "identity": {"id_field": ident.get("id_field"), "label_field": ident.get("label_field")} if ident else None,
        "fields": [{"name": name, "type": f.get("type") or "string", "required": bool(f.get("required")),
                    "description": f.get("description") or "", "resolves_to": f.get("resolves_to")} for name, f in spec["fields"].items()],
    }


def _weeks(dates: List[datetime], now: datetime) -> List[Dict[str, Any]]:
    """How many of `dates` fall in each of the last WEEKS weeks (Monday to Sunday), oldest first."""
    start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(weeks=WEEKS - 1)
    counts = [0] * WEEKS
    for d in dates:
        d = d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        i = (d - start).days // 7
        if 0 <= i < WEEKS:
            counts[i] += 1
    return [{"week": (start + timedelta(weeks=i)).date().isoformat(), "count": c} for i, c in enumerate(counts)]


def _number(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _figures(spec: Dict[str, Any], rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Per attribute: how often it is filled, and for numbers their totals, for other values the commonest ones."""
    out: List[Dict[str, Any]] = []
    total = len(rows)
    for name, f in spec["fields"].items():
        values = [r.get(name) for r in rows if r.get(name) not in (None, "", [])]
        fig: Dict[str, Any] = {"field": name, "type": f.get("type") or "string", "filled": len(values), "rows": total}
        if (f.get("type") or "string") in NUMBER_TYPES:
            nums = [n for n in (_number(v) for v in values) if n is not None]
            if nums:
                fig["number"] = {"sum": sum(nums), "average": sum(nums) / len(nums), "min": min(nums), "max": max(nums)}
        elif (f.get("type") or "string") in ("date", "time"):
            texts = sorted(str(v) for v in values)
            if texts:
                fig["range"] = {"first": texts[0], "last": texts[-1]}
        else:
            common = Counter(str(v)[:120] for v in values if not isinstance(v, (dict, list))).most_common(5)
            if common and common[0][1] > 1:
                fig["top"] = [{"value": v, "count": n} for v, n in common]
            fig["distinct"] = len({str(v) for v in values})
        out.append(fig)
    return out


async def _documents(db: AsyncSession, *where: Any, page: int) -> Dict[str, Any]:
    base = select(RecordObject.document_id, func.max(RecordObject.seen_at).label("seen"), func.sum(RecordObject.rows).label("rows")) \
        .where(*where).group_by(RecordObject.document_id)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar() or 0
    found = (await db.execute(base.order_by(func.max(RecordObject.seen_at).desc()).offset(page * PAGE).limit(PAGE))).all()
    docs = {d.id: d for d in (await db.execute(select(Document).where(Document.id.in_([r.document_id for r in found])))).scalars().all()}
    items = []
    for r in found:
        d = docs.get(r.document_id)
        if d is None:
            continue
        items.append({"id": d.id, "name": d.identified_key or d.original_filename, "file_name": d.original_filename,
                      "status": d.status.value if hasattr(d.status, "value") else str(d.status), "received_at": d.created_at,
                      "rows": int(r.rows or 0)})
    return {"items": items, "total": int(total), "page": page, "page_size": PAGE}


async def _things(db: AsyncSession, template_id: str, obj_type: str, *, q: str = "", page: int = 0, limit: int = PAGE) -> Dict[str, Any]:
    where = [RecordObject.template_id == template_id, RecordObject.object_type == obj_type, RecordObject.thing_key.is_not(None)]
    if q.strip():
        like = f"%{q.strip().lower()}%"
        where.append(func.lower(RecordObject.label).like(like) | func.lower(RecordObject.thing_key).like(like))
    base = select(RecordObject.thing_key, func.max(RecordObject.label).label("label"), func.count(distinct(RecordObject.document_id)).label("documents"),
                  func.min(RecordObject.seen_at).label("first"), func.max(RecordObject.seen_at).label("last")).where(*where).group_by(RecordObject.thing_key)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar() or 0
    rows = (await db.execute(base.order_by(func.count(distinct(RecordObject.document_id)).desc(), func.max(RecordObject.label))
                             .offset(page * limit).limit(limit))).all()
    return {"items": [{"key": r.thing_key, "id": thing_id(r.thing_key), "label": r.label or thing_id(r.thing_key), "documents": int(r.documents),
                       "first_seen": r.first, "last_seen": r.last, "path": thing_path(obj_type, r.thing_key)} for r in rows],
            "total": int(total), "page": page, "page_size": limit}


async def overview(db: AsyncSession, template: DocumentTemplate) -> List[Dict[str, Any]]:
    """Every object of the template, in the template's order, with its counts."""
    counts = {r.object_type: r for r in (await db.execute(
        select(RecordObject.object_type, func.count(distinct(RecordObject.document_id)).label("documents"),
               func.count(distinct(RecordObject.thing_key)).label("things"), func.max(RecordObject.seen_at).label("last"))
        .where(RecordObject.template_id == template.id).group_by(RecordObject.object_type))).all()}
    out = []
    for obj_type, spec in _objects(template).items():
        c = counts.get(obj_type)
        d = _definition(obj_type, spec)
        out.append({**{k: d[k] for k in ("type", "description", "icon", "multiple", "identity")}, "fields": len(spec["fields"]),
                    "documents": int(c.documents) if c else 0, "things": int(c.things) if c else 0, "last_seen": c.last if c else None})
    return out


async def object_profile(db: AsyncSession, template: DocumentTemplate, obj_type: str, *, page: int = 0) -> Dict[str, Any]:
    spec = _object(template, obj_type)
    now = datetime.now(timezone.utc)
    where = (RecordObject.template_id == template.id, RecordObject.object_type == obj_type)
    stats = (await db.execute(select(func.count(distinct(RecordObject.document_id)), func.count(distinct(RecordObject.thing_key)),
                                     func.min(RecordObject.seen_at), func.max(RecordObject.seen_at)).where(*where))).one()
    recent = (await db.execute(select(RecordObject.document_id, func.max(RecordObject.seen_at)).where(
        *where, RecordObject.seen_at >= now - timedelta(weeks=WEEKS + 1)).group_by(RecordObject.document_id))).all()

    records = await saved_records(db, template_id=template.id)
    rows = [r for _id, _name, _status, data in records[:MAX_RECORDS] for r in rows_of(data or {}, obj_type)]
    screens = [{"slug": s.slug, "name": s.name} for s in (await db.execute(select(WorkspaceScreen).where(
        WorkspaceScreen.template_id == template.id, WorkspaceScreen.status == "active").order_by(WorkspaceScreen.position))).scalars().all()
        if ((s.definition or {}).get("source") or {}).get("object") == obj_type]
    links = {name: f["resolves_to"] for name, f in spec["fields"].items() if f.get("resolves_to")}
    pointed_from = [{"type": t, "field": name} for t, s in _objects(template).items() for name, f in s["fields"].items()
                    if f.get("resolves_to") == obj_type]
    return {
        "object": _definition(obj_type, spec),
        "links": links, "referenced_by": pointed_from,
        "stats": {"documents": int(stats[0] or 0), "things": int(stats[1] or 0), "first_seen": stats[2], "last_seen": stats[3],
                  "rows": len(rows), "records_read": min(len(records), MAX_RECORDS), "records_capped": len(records) > MAX_RECORDS},
        "weeks": _weeks([seen for _doc, seen in recent], now),
        "figures": _figures(spec, rows),
        "things": await _things(db, template.id, obj_type, limit=8) if spec["identity"] else None,
        "documents": await _documents(db, *where, page=page),
        "screens": screens,
    }


async def things(db: AsyncSession, template: DocumentTemplate, obj_type: str, *, q: str = "", page: int = 0) -> Dict[str, Any]:
    spec = _object(template, obj_type)
    if not spec["identity"]:
        raise HTTPException(status_code=404, detail={"code": "no_identity", "message": f'"{obj_type}" has no identity, so it has no things of its own.'})
    return await _things(db, template.id, obj_type, q=q, page=page)


async def _entity(db: AsyncSession, template_id: str, obj_type: str, nid: str) -> Optional[Entity]:
    candidates = (await db.execute(select(Entity).where(
        Entity.template_id == template_id, Entity.entity_type == obj_type,
        func.upper(func.replace(Entity.identifier_value, " ", "")) == nid))).scalars().all()
    return next((e for e in candidates if normalize_id(e.identifier_value) == nid), None)


async def thing_profile(db: AsyncSession, template: DocumentTemplate, obj_type: str, raw_id: str, *, page: int = 0) -> Dict[str, Any]:
    spec = _object(template, obj_type)
    key = thing_key(obj_type, raw_id)
    if not spec["identity"] or not key:
        raise HTTPException(status_code=404, detail={"code": "no_such_thing", "message": "There's no such thing in this workspace."})
    nid = thing_id(key)
    where = (RecordObject.template_id == template.id, RecordObject.thing_key == key)
    mentions = (await db.execute(select(RecordObject.document_id, RecordObject.seen_at, RecordObject.label, RecordObject.values)
                                 .where(*where).order_by(RecordObject.seen_at.desc()))).all()
    entity = await _entity(db, template.id, obj_type, nid)
    if not mentions and entity is None:
        raise HTTPException(status_code=404, detail={"code": "no_such_thing", "message": "No document mentions this, and the workspace doesn't know it."})

    now = datetime.now(timezone.utc)
    # Its attributes as the newest record that has a row of it read them, with how many records agreed
    latest: Dict[str, Any] = {}
    seen_values: Dict[str, Counter] = defaultdict(Counter)
    for m in mentions:
        for k, v in (m.values or {}).items():
            latest.setdefault(k, v)
            seen_values[k][str(v)] += 1
    attributes = [{"field": name, "type": f.get("type") or "string", "value": latest.get(name),
                   "other_values": [v for v, _n in seen_values[name].most_common(4) if v != str(latest.get(name))][:3],
                   "resolves_to": f.get("resolves_to")} for name, f in spec["fields"].items()]
    label = next((m.label for m in mentions if m.label), None) or (entity.name if entity else nid)
    neighbours = await graph_service.appears_with(db, template.id, key, limit=12)
    icons = {t: object_icons.icon_of(s) for t, s in _objects(template).items()}
    return {
        "object": {"type": obj_type, "icon": object_icons.icon_of(spec), "description": spec.get("description") or ""},
        "thing": {"key": key, "id": nid, "label": label},
        "attributes": attributes,
        "stats": {"documents": len({m.document_id for m in mentions}), "first_seen": mentions[-1].seen_at if mentions else None,
                  "last_seen": mentions[0].seen_at if mentions else None},
        "weeks": _weeks([m.seen_at for m in mentions], now),
        "appears_with": [{"key": n.key, "type": n.type, "label": n.label or thing_id(n.key), "count": n.count,
                          "icon": icons.get(n.type, object_icons.DEFAULT_ICON), "path": thing_path(n.type, n.key)} for n in neighbours],
        "knowledge": {"id": entity.id, "name": entity.name, "aliases": [a.alias for a in entity.aliases], "attributes": entity.attributes or {},
                      "created_by": entity.created_by} if entity else None,
        "documents": await _documents(db, *where, page=page),
        # Objects whose fields point to this object type (e.g. DutyRow.employee -> Employee)
        "referenced_by": [{"type": t, "field": fname} for t, s in _objects(template).items()
                          for fname, f in s["fields"].items() if f.get("resolves_to") == obj_type],
    }



async def thing_full_records(
    db: AsyncSession, template: DocumentTemplate, obj_type: str, thing_raw_id: str, *,
    q: str = "", since_days: Optional[int] = None,
    sort: str = "seen", direction: str = "desc", page: int = 0,
) -> Dict[str, Any]:
    """
    One unified flat table of everything a template knows about a specific thing.

    For every document that mentions this thing, the rows of *every* object type
    whose field resolves_to this object type (attendance entries, duty rows, invoice
    lines, trip logs — whatever the template defines) are returned as individual
    rows.  All fields from all referencing types are merged into one flat column
    set (cells are empty when a row comes from a different referencing type).

    The thing's own identity fields are also included as columns (they repeat on
    every row, but that makes the table self-contained and sortable).

    Generalised: works for any template domain — rosters, invoices, logistics, HR.
    The template schema is the only configuration source.
    """
    spec = _object(template, obj_type)
    if not spec.get("identity"):
        raise HTTPException(status_code=400, detail={
            "code": "no_identity",
            "message": f'"{obj_type}" has no identity so it has no thing profiles.',
        })

    key = thing_key(obj_type, thing_raw_id)
    if not key:
        raise HTTPException(status_code=404, detail={"code": "no_such_thing", "message": "No such thing."})
    nid     = thing_id(key)
    norm_nid = normalize_id(nid)

    # ── Discover referencing types from the template ────────────────────────
    # A referencing type is any object (with or without identity) that has at
    # least one field whose resolves_to == obj_type.
    all_objects = _objects(template)
    ref_types: List[Dict[str, Any]] = []          # [{type, field, fields_spec}]
    for t_name, t_spec in all_objects.items():
        if t_name == obj_type:
            continue
        for fname, fspec in t_spec.get("fields", {}).items():
            if fspec.get("resolves_to") == obj_type:
                ref_types.append({"type": t_name, "field": fname, "fields": t_spec["fields"]})
                break  # one entry per referencing type (even if multiple link fields)

    # ── Build column list ───────────────────────────────────────────────────
    # Own identity fields first, then referencing-type fields (excluding the
    # link field that always equals this thing's id).
    own_ident = spec.get("identity", {})
    own_id_field    = own_ident.get("id_field", "")
    own_label_field = own_ident.get("label_field", "")
    own_field_names = [own_id_field, own_label_field] + [
        n for n in spec.get("fields", {}) if n not in (own_id_field, own_label_field)
    ]

    columns: List[Dict[str, Any]] = []
    for n in own_field_names:
        if not n:
            continue
        fspec = spec.get("fields", {}).get(n, {})
        columns.append({
            "name": n,
            "type": fspec.get("type") or "string",
            "identity": n in (own_id_field, own_label_field),
            "source": "own",
            "source_type": obj_type,
        })
    for rt in ref_types:
        for n, fspec in rt["fields"].items():
            if n == rt["field"]:
                continue          # skip the back-link field (always = this thing's id)
            # Avoid duplicate column names across referencing types
            if any(c["name"] == n for c in columns):
                col_name = f"{rt['type']}.{n}"
            else:
                col_name = n
            columns.append({
                "name": col_name,
                "type": fspec.get("type") or "string",
                "identity": False,
                "source": "referencing",
                "source_type": rt["type"],
            })

    # ── Fetch documents that mention this thing ─────────────────────────────
    matching_doc_ids_sq = select(RecordObject.document_id).where(
        RecordObject.template_id == template.id,
        RecordObject.thing_key   == key,
    )
    doc_where = [
        Document.template_id == template.id,
        Document.id.in_(matching_doc_ids_sq),
    ]
    if since_days in SINCE_DAYS:
        doc_where.append(
            Document.created_at >= datetime.now(timezone.utc) - timedelta(days=since_days)
        )

    doc_rows = (await db.execute(
        select(Document.id, Document.original_filename, Document.identified_key,
               Document.status, Document.created_at, ExtractionResult.extracted_data)
        .join(ExtractionResult, ExtractionResult.document_id == Document.id)
        .where(*doc_where)
    )).all()

    # ── Build rows ──────────────────────────────────────────────────────────
    text_q = q.strip().lower()[:120]

    def _own_row(data: Dict) -> Dict:
        """The thing's own attribute values from the extracted data."""
        for row in rows_of(data, obj_type):
            id_val = row.get(own_id_field)
            if id_val is not None and normalize_id(id_val) == norm_nid:
                return dict(row)
        return {}

    matched_items: List[Dict[str, Any]] = []

    for doc_id, filename, ikey, status, created_at, data in doc_rows:
        doc_name   = ikey or filename
        status_str = status.value if hasattr(status, "value") else str(status)
        own_vals   = _own_row(data or {})

        # Collect rows from every referencing type
        ref_rows: List[Dict[str, Any]] = []
        for rt in ref_types:
            for ref_row in rows_of(data or {}, rt["type"]):
                ref_val = ref_row.get(rt["field"])
                if ref_val is None or normalize_id(ref_val) != norm_nid:
                    continue
                # Remap duplicate column names (prefix with type name)
                remapped: Dict[str, Any] = {}
                for col in columns:
                    if col["source"] == "referencing" and col["source_type"] == rt["type"]:
                        # Reverse the col_name mangling: strip "Type." prefix if present
                        orig_field = col["name"].split(".", 1)[-1] if "." in col["name"] else col["name"]
                        remapped[col["name"]] = ref_row.get(orig_field)
                ref_rows.append(remapped)

        if not ref_rows:
            # Document mentions this thing but has no referencing rows — emit
            # one row with own values and empty referencing fields.
            ref_rows = [{}]

        for ridx, ref_vals in enumerate(ref_rows):
            merged: Dict[str, Any] = {**own_vals, **ref_vals}

            # Text search
            if text_q:
                haystack = " ".join(str(v) for v in merged.values() if v is not None).lower()
                haystack += f" {doc_name} {filename}".lower()
                if text_q not in haystack:
                    continue

            matched_items.append({
                "id"            : f"{doc_id}_{ridx}",
                "document_id"   : doc_id,
                "document_name" : doc_name,
                "file_name"     : filename,
                "status"        : status_str,
                "seen_at"       : created_at,
                "thing"         : None,
                "values"        : merged,
            })

    # ── Sort ────────────────────────────────────────────────────────────────
    rev = direction == "desc"
    _epoch = datetime.min.replace(tzinfo=timezone.utc)
    if sort == "seen":
        matched_items.sort(key=lambda x: x["seen_at"] or _epoch, reverse=rev)
    elif sort == "doc":
        matched_items.sort(key=lambda x: (x["document_name"] or "").lower(), reverse=rev)
    elif sort.startswith("f:"):
        field = sort[2:]
        matched_items.sort(key=lambda x: str(x["values"].get(field) or "").lower(), reverse=rev)

    total       = len(matched_items)
    paged_items = matched_items[page * RECORD_PAGE : (page + 1) * RECORD_PAGE]

    return {
        "columns"  : columns,
        "items"    : paged_items,
        "total"    : total,
        "page"     : page,
        "page_size": RECORD_PAGE,
        "facets"   : [],
    }

async def referenced_records(db: AsyncSession, template: DocumentTemplate, obj_type: str, thing_raw_id: str,
                             ref_type: str, ref_field: str, *, q: str = "", since_days: Optional[int] = None,
                             sort: str = "seen", direction: str = "desc", page: int = 0) -> Dict[str, Any]:
    """
    Records of `ref_type` (e.g. DutyRow, AttendanceEntry) that reference a specific thing of `obj_type` (e.g. Employee)
    via `ref_field` (e.g. "employee"). Extracted from documents that mention this thing.
    """
    spec = _object(template, obj_type)
    ref_spec = _object(template, ref_type)
    key = thing_key(obj_type, thing_raw_id)
    if not key:
        raise HTTPException(status_code=404, detail={"code": "no_such_thing", "message": "No such thing."})
    nid = thing_id(key)
    norm_nid = normalize_id(nid)

    matching_doc_ids = select(RecordObject.document_id).where(
        RecordObject.template_id == template.id,
        RecordObject.thing_key == key,
    )
    doc_where = [
        Document.template_id == template.id,
        Document.id.in_(matching_doc_ids),
    ]
    if since_days in SINCE_DAYS:
        doc_where.append(Document.created_at >= datetime.now(timezone.utc) - timedelta(days=since_days))

    stmt = (
        select(Document.id, Document.original_filename, Document.identified_key, Document.status, Document.created_at, ExtractionResult.extracted_data)
        .join(ExtractionResult, ExtractionResult.document_id == Document.id)
        .where(*doc_where)
        .order_by(Document.created_at.desc())
    )
    doc_rows = (await db.execute(stmt)).all()

    text_q = q.strip().lower()[:120]
    matched_items = []
    for doc_id, filename, ikey, status, created_at, data in doc_rows:
        doc_name = ikey or filename
        for idx, row in enumerate(rows_of(data or {}, ref_type)):
            ref_val = row.get(ref_field)
            if ref_val is None or normalize_id(ref_val) != norm_nid:
                continue

            if text_q:
                row_str = " ".join(str(v) for v in row.values() if v is not None).lower()
                doc_str = f"{filename} {ikey or ''}".lower()
                if text_q not in row_str and text_q not in doc_str:
                    continue

            status_str = status.value if hasattr(status, "value") else str(status)
            matched_items.append({
                "id": f"{doc_id}_{idx}",
                "document_id": doc_id,
                "document_name": doc_name,
                "file_name": filename,
                "status": status_str,
                "seen_at": created_at,
                "thing": None,
                "values": dict(row),
            })

    rev = direction == "desc"
    if sort == "seen":
        matched_items.sort(key=lambda x: x["seen_at"] or datetime.min.replace(tzinfo=timezone.utc), reverse=rev)
    elif sort == "doc":
        matched_items.sort(key=lambda x: (x["document_name"] or "").lower(), reverse=rev)
    elif sort.startswith("f:"):
        field = sort[2:]
        matched_items.sort(key=lambda x: str(x["values"].get(field) or "").lower(), reverse=rev)

    total = len(matched_items)
    paged_items = matched_items[page * RECORD_PAGE : (page + 1) * RECORD_PAGE]

    columns = [{"name": n, "type": f.get("type") or "string", "identity": False}
               for n, f in ref_spec["fields"].items() if n != ref_field]

    return {
        "columns": columns,
        "items": paged_items,
        "total": total,
        "page": page,
        "page_size": RECORD_PAGE,
        "facets": [],
    }


async def records(db: AsyncSession, template: DocumentTemplate, obj_type: str, *, thing: Optional[str] = None, q: str = "",
                  filters: Optional[Dict[str, str]] = None, since_days: Optional[int] = None, sort: str = "seen", direction: str = "desc",
                  page: int = 0) -> Dict[str, Any]:
    """
    The rows of an object with an identity as the documents read them: one for each (document, thing) with the thing's attributes in
    that document. For the whole object, or for one `thing`. Searched (its name, id, attributes and document), narrowed by a date and by
    values of its attributes, and sorted. `facets` (first page only) offers the attributes that have a few different values as filters.

    When viewing one thing's records, also enriches each row with the attributes of connected objects (fields with
    `resolves_to`) from the same document, keyed as ``connected.<LinkedObjectType>.<field>``.
    """
    spec = _object(template, obj_type)
    if not spec["identity"]:
        raise HTTPException(status_code=404, detail={"code": "no_identity", "message": f'"{obj_type}" has no identity, so it has no records of things.'})
    key = thing_key(obj_type, thing) if thing else None
    if thing and not key:
        raise HTTPException(status_code=404, detail={"code": "no_such_thing", "message": "There's no such thing in this workspace."})
    fields = list(spec["fields"])
    scope = [RecordObject.template_id == template.id, RecordObject.object_type == obj_type, RecordObject.rows > 0]
    if key:
        scope.append(RecordObject.thing_key == key)
    where = list(scope)
    if since_days in SINCE_DAYS:
        where.append(RecordObject.seen_at >= datetime.now(timezone.utc) - timedelta(days=since_days))
    for name, value in (filters or {}).items():
        if name in spec["fields"]:
            where.append(RecordObject.values[name].as_string() == value)
    text = q.strip().lower()[:120]
    if text:
        like = f"%{text.replace(chr(92), chr(92) * 2).replace('%', chr(92) + '%').replace('_', chr(92) + '_')}%"
        where.append(or_(*(func.lower(c).like(like, escape="\\") for c in (
            RecordObject.label, RecordObject.thing_key, cast(RecordObject.values, String), Document.original_filename,
            func.coalesce(Document.identified_key, "")))))
    base = select(RecordObject.id).join(Document, Document.id == RecordObject.document_id).where(*where)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar() or 0

    if sort.startswith("f:") and sort[2:] in spec["fields"]:
        order = RecordObject.values[sort[2:]].as_string()
    else:
        order = {"label": func.lower(RecordObject.label), "doc": func.lower(Document.original_filename)}.get(sort, RecordObject.seen_at)
    rows = (await db.execute(select(RecordObject, Document).join(Document, Document.id == RecordObject.document_id).where(*where)
                             .order_by(order.asc() if direction == "asc" else order.desc(), RecordObject.id)
                             .offset(page * RECORD_PAGE).limit(RECORD_PAGE))).all()
    items = [{"id": r.id, "document_id": d.id, "document_name": d.identified_key or d.original_filename, "file_name": d.original_filename,
              "status": d.status.value if hasattr(d.status, "value") else str(d.status), "seen_at": r.seen_at,
              "thing": {"key": r.thing_key, "id": thing_id(r.thing_key), "label": r.label or thing_id(r.thing_key), "path": thing_path(obj_type, r.thing_key)}
              if r.thing_key else None, "values": dict(r.values or {})} for r, d in rows]

    # --- Connected-object enrichment (only when viewing one thing's records) ----------------------------
    # For each field that resolves_to another object type, look up co-document RecordObject rows of that
    # linked type whose thing_key matches the value in the row, and merge their attributes into `values`
    # as "connected.<LinkedType>.<field>" keys.  Also surface those fields as extra columns.
    all_objects = _objects(template)
    linked_fields: Dict[str, str] = {name: f["resolves_to"] for name, f in spec["fields"].items() if f.get("resolves_to")}
    connected_columns: List[Dict[str, Any]] = []
    if thing and linked_fields and items:
        doc_ids = list({item["document_id"] for item in items})
        for field_name, linked_type in linked_fields.items():
            linked_spec = all_objects.get(linked_type)
            if linked_spec is None or not linked_spec.get("identity"):
                continue
            # Fetch ALL RecordObject rows for the linked type in those docs (rows>0 = has own data,
            # rows=0 = only referenced).  Prefer rows>0 entries so we get attribute values, but
            # fall back to rows=0 entries which still carry the label.
            linked_rows = (await db.execute(
                select(RecordObject.document_id, RecordObject.thing_key, RecordObject.label, RecordObject.values, RecordObject.rows)
                .where(RecordObject.template_id == template.id, RecordObject.object_type == linked_type,
                       RecordObject.document_id.in_(doc_ids))
            )).all()
            # Index: prefer the row with actual data (rows>0) when both exist
            linked_index: Dict[tuple, tuple] = {}
            for lr in linked_rows:
                k = (lr.document_id, lr.thing_key)
                existing = linked_index.get(k)
                if existing is None or (lr.rows > 0 and existing[2] == 0):
                    linked_index[k] = (lr.label, lr.values or {}, lr.rows)
            linked_fields_spec = linked_spec["fields"]
            linked_identity = linked_spec.get("identity") or {}
            id_field = linked_identity.get("id_field")
            label_field = linked_identity.get("label_field")
            # Label column for the connected object
            col_key_label = f"connected.{linked_type}.__label__"
            if not any(c["name"] == col_key_label for c in connected_columns):
                connected_columns.append({"name": col_key_label, "type": "string", "identity": False,
                                          "connected": True, "connected_type": linked_type, "connected_label": True})
            for lf_name, lf in linked_fields_spec.items():
                if lf_name in (id_field, label_field):
                    continue
                col_key = f"connected.{linked_type}.{lf_name}"
                if not any(c["name"] == col_key for c in connected_columns):
                    connected_columns.append({"name": col_key, "type": lf.get("type") or "string",
                                              "identity": False, "connected": True, "connected_type": linked_type})
            for item in items:
                raw_id_val = (item["values"] or {}).get(field_name)
                if raw_id_val is None:
                    continue
                linked_key = thing_key(linked_type, str(raw_id_val))
                match = linked_index.get((item["document_id"], linked_key))
                if match:
                    link_label, link_values, _link_rows = match
                    item["values"][col_key_label] = link_label or thing_id(linked_key)
                    for lf_name in linked_fields_spec:
                        if lf_name in (id_field, label_field):
                            continue
                        val = link_values.get(lf_name)
                        if val is not None:
                            item["values"][f"connected.{linked_type}.{lf_name}"] = val
    # --------------------------------------------------------------------------------------------------------

    facets: List[Dict[str, Any]] = []
    if page == 0:
        sample = (await db.execute(select(RecordObject.values).where(*scope).order_by(RecordObject.seen_at.desc()).limit(FACET_SAMPLE))).scalars().all()
        for name in fields:
            if (spec["fields"][name].get("type") or "string") in NUMBER_TYPES + ("date", "time") or name in (spec["identity"].get("id_field"), spec["identity"].get("label_field")):
                continue   # numbers, dates and the thing's own name and id are searched, not picked from a list
            counts = Counter(str(v)[:80] for v in ((s or {}).get(name) for s in sample) if v not in (None, "") and not isinstance(v, (dict, list)))
            if 2 <= len(counts) <= FACET_MAX:
                facets.append({"field": name, "values": [{"value": v, "count": n} for v, n in counts.most_common()]})
    own_columns = [{"name": n, "type": spec["fields"][n].get("type") or "string",
                    "identity": n in (spec["identity"].get("id_field"), spec["identity"].get("label_field"))}
                   for n in fields]
    return {"columns": own_columns + connected_columns,
            "items": items, "total": int(total), "page": page, "page_size": RECORD_PAGE, "facets": facets}


# ---- search (both search boxes) -------------------------------------------------------------------------------------

async def search(db: AsyncSession, s: Any, template_ids: Optional[List[str]] = None, *, limit: int = 5) -> Dict[str, List[Dict[str, Any]]]:
    """
    Objects whose name matches what was typed, and things whose name or identifier does, in the given workspaces (all
    of them when None). `s` is an app.services.search.Query. Each comes with its workspace (template) id and icon.
    """
    from sqlalchemy import String, cast
    if template_ids is not None and not template_ids:
        return {"objects": [], "things": []}
    scoped = (lambda col: col.in_(template_ids)) if template_ids is not None else (lambda col: True)  # noqa: E731

    candidates = (await db.execute(select(DocumentTemplate).where(
        scoped(DocumentTemplate.id), DocumentTemplate.active_version_id.is_not(None),
        func.lower(cast(DocumentTemplate.schema_definition, String)).like(s.contains, escape="\\")).limit(200))).scalars().all()
    objects = []
    for t in candidates:
        for obj_type, spec in _objects(t).items():
            name = obj_type.lower()
            if s.lower in name:
                objects.append((0 if name == s.lower else 1 if name.startswith(s.lower) else 2, obj_type.lower(), t, obj_type, spec))
    objects.sort(key=lambda o: o[:2])

    label = func.max(RecordObject.label)
    rows = (await db.execute(
        select(RecordObject.template_id, RecordObject.object_type, RecordObject.thing_key, label.label("label"),
               func.count(distinct(RecordObject.document_id)).label("documents"))
        .where(scoped(RecordObject.template_id), RecordObject.thing_key.is_not(None), s.matches(RecordObject.label, RecordObject.thing_key))
        .group_by(RecordObject.template_id, RecordObject.object_type, RecordObject.thing_key)
        .order_by(s.rank(label), func.count(distinct(RecordObject.document_id)).desc()).limit(limit + 1))).all()
    schemas = {t.id: t for t in candidates}
    missing = {r.template_id for r in rows} - set(schemas)
    if missing:
        schemas.update({t.id: t for t in (await db.execute(select(DocumentTemplate).where(DocumentTemplate.id.in_(missing)))).scalars().all()})
    icon = lambda tid, ot: object_icons.icon_of(_objects(schemas[tid]).get(ot)) if tid in schemas else object_icons.DEFAULT_ICON  # noqa: E731
    return {
        "objects": [{"template_id": t.id, "tenant_id": t.tenant_id, "type": ot, "icon": object_icons.icon_of(spec), "fields": len(spec["fields"]),
                     "path": f"objects/{quote(ot, safe='')}"} for _r, _n, t, ot, spec in objects[:limit]],
        "things": [{"template_id": r.template_id, "type": r.object_type, "key": r.thing_key, "label": r.label or thing_id(r.thing_key),
                    "documents": int(r.documents), "icon": icon(r.template_id, r.object_type), "path": thing_path(r.object_type, r.thing_key)}
                   for r in rows[:limit]],
    }
