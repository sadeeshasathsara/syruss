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
from sqlalchemy import distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import Document
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
    }


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
