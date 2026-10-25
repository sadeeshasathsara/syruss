"""
Knowledge sources: reference data a workspace already has (a CSV or JSON file, or an API),
mapped by the agent onto the template's identified objects and imported as known entities
once a person approves the mapping.

The split of work: people add sources and approve mappings; the agent reads the data and
decides the mapping; this module checks a mapping against the template and the data, shows
what it would do, and does the import. Credentials never leave this module unencrypted.
"""
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from fastapi import HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.secrets import seal, unseal
from app.models.audit import AuditLog
from app.models.knowledge import Entity, EntityAlias
from app.models.knowledge_source import KnowledgeSource
from app.services.agent_events import enqueue_agent_event
from app.services.identity_check import normalize_id, normalize_label
from app.services.source_data import SourceError, columns_of, fetch_api, parse_bytes, record_sets, rows_at

SECRET_AUTH_FIELDS = ("token", "password", "key_value", "client_secret")
PUBLIC_AUTH_FIELDS = ("type", "username", "key_name", "key_in", "token_url", "client_id", "scope")


def identified_objects(schema: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """The template's objects that stand for real things: {type: {"identity", "fields"}}."""
    out = {}
    for obj in (schema or {}).get("objects", []) or []:
        if isinstance(obj, dict) and isinstance(obj.get("identity"), dict):
            fields = [f if isinstance(f, str) else f.get("name") for f in obj.get("fields", [])]
            out[obj["type"]] = {"identity": obj["identity"], "fields": [f for f in fields if f]}
    return out


# ---------------------------------------------------------------------------
# What people see (never a secret)
# ---------------------------------------------------------------------------

def public_view(source: KnowledgeSource) -> Dict[str, Any]:
    cfg = source.config or {}
    view = {
        "id": source.id, "name": source.name, "kind": source.kind, "notes": source.notes, "status": source.status,
        "last_error": source.last_error, "imported_count": source.imported_count,
        "last_synced_at": source.last_synced_at, "created_at": source.created_at, "updated_at": source.updated_at,
        "record_sets": cfg.get("record_sets", []), "mapping": source.mapping,
    }
    if source.kind == "API":
        auth = cfg.get("auth") or {}
        view["api"] = {
            "url": cfg.get("url"), "method": cfg.get("method", "GET"), "query": cfg.get("query") or {},
            "header_names": cfg.get("header_names") or [], "body": cfg.get("body"),
            "auth": {**{k: auth.get(k) for k in PUBLIC_AUTH_FIELDS}, "saved_secrets": cfg.get("saved_secrets") or []},
        }
    return view


def split_api_config(body: Dict[str, Any], current_secrets: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Separates what may be shown from what must be sealed. A secret left blank keeps the saved
    value, so a person can change the URL without typing the API key again.
    """
    current = current_secrets or {}
    auth = body.get("auth") or {}
    secrets: Dict[str, Any] = {}
    for key in SECRET_AUTH_FIELDS:
        value = auth.get(key) or current.get(key)
        if value:
            secrets[key] = value
    headers = {}
    for name, value in (body.get("headers") or {}).items():
        value = value or (current.get("headers") or {}).get(name)
        if name and value:
            headers[name] = value
    if headers:
        secrets["headers"] = headers
    config = {
        "url": (body.get("url") or "").strip(), "method": body.get("method") or "GET",
        "query": {k: v for k, v in (body.get("query") or {}).items() if k},
        "body": body.get("body") or None, "header_names": list(headers),
        "auth": {k: auth.get(k) for k in PUBLIC_AUTH_FIELDS if auth.get(k)} or {"type": "none"},
        "saved_secrets": [k for k in SECRET_AUTH_FIELDS if k in secrets],
    }
    return config, secrets


def summarize(data: Any) -> List[Dict[str, Any]]:
    return [{"records_path": s["records_path"], "count": s["count"], "columns": s["columns"]} for s in record_sets(data)]


# ---------------------------------------------------------------------------
# Creating and refreshing
# ---------------------------------------------------------------------------

async def create_file_source(db: AsyncSession, *, tenant_id: str, template_id: str, user_id: str, file: UploadFile, notes: str) -> KnowledgeSource:
    name = file.filename or "Uploaded file"
    if not name.lower().endswith((".csv", ".json")):
        raise HTTPException(status_code=400, detail="Upload a .csv or .json file.")
    try:
        data = parse_bytes(await file.read(), filename=name)
        sets = summarize(data)
    except SourceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not sets:
        raise HTTPException(status_code=422, detail="The file holds no records (no list of objects or rows).")
    source = KnowledgeSource(
        tenant_id=tenant_id, template_id=template_id, name=name, kind="FILE", notes=notes or "",
        config={"record_sets": sets}, data=data, status="MAPPING", created_by=user_id,
        last_synced_at=datetime.now(timezone.utc),
    )
    db.add(source)
    await db.flush()
    return source


async def fetch_preview(body: Dict[str, Any], current_secrets: Optional[Dict[str, Any]] = None) -> Tuple[Dict[str, Any], Dict[str, Any], Any]:
    """Calls the API as configured; returns (config, secrets, data). Problems become a 422."""
    config, secrets = split_api_config(body, current_secrets)
    try:
        data = await fetch_api(config, secrets)
    except SourceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not record_sets(data):
        raise HTTPException(status_code=422, detail="The API answered, but its response holds no list of records.")
    return config, secrets, data


async def create_api_source(db: AsyncSession, *, tenant_id: str, template_id: str, user_id: str, body: Dict[str, Any]) -> KnowledgeSource:
    config, secrets, data = await fetch_preview(body)
    source = KnowledgeSource(
        tenant_id=tenant_id, template_id=template_id, name=(body.get("name") or config["url"]).strip(), kind="API",
        notes=body.get("notes") or "", config={**config, "record_sets": summarize(data)}, secrets=seal(secrets),
        data=data, status="MAPPING", created_by=user_id, last_synced_at=datetime.now(timezone.utc),
    )
    db.add(source)
    await db.flush()
    return source


async def update_api_source(db: AsyncSession, source: KnowledgeSource, body: Dict[str, Any]) -> None:
    config, secrets, data = await fetch_preview(body, unseal(source.secrets))
    source.name = (body.get("name") or source.name).strip()
    source.notes = body.get("notes") or source.notes
    source.config = {**config, "record_sets": summarize(data)}
    source.secrets = seal(secrets)
    source.data = data
    source.last_error = None
    source.last_synced_at = datetime.now(timezone.utc)


async def refresh(source: KnowledgeSource) -> None:
    """Fetches an API source again (a file has nothing to fetch). Failures are kept on the source."""
    if source.kind != "API":
        return
    try:
        data = await fetch_api(source.config, unseal(source.secrets))
    except SourceError as exc:
        source.status, source.last_error = "ERROR", str(exc)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    source.data = data
    source.config = {**source.config, "record_sets": summarize(data)}
    source.last_error = None
    source.last_synced_at = datetime.now(timezone.utc)


def wake_agent(db: AsyncSession, source: KnowledgeSource, *, reason: str, actor_id: str, actor_name: Optional[str] = None,
               conversation_id: Optional[str] = None, notes: str = "") -> None:
    """Asks the agent to work out (or rework) how this source maps onto the template."""
    source.status = "MAPPING"
    enqueue_agent_event(
        db, event_type="knowledge.source_added", tenant_id=source.tenant_id, template_id=source.template_id,
        payload={"source_id": source.id, "name": source.name, "kind": source.kind, "reason": reason,
                 "notes": notes or source.notes, "channel_id": conversation_id},
        actor_id=actor_id, actor_type="user", actor_name=actor_name, conversation_id=conversation_id,
    )


# ---------------------------------------------------------------------------
# Mappings
# ---------------------------------------------------------------------------

def _clean(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return str(value).strip()


def check_mapping(schema: Optional[Dict[str, Any]], data: Any, mapping: Dict[str, Any]) -> Tuple[List[str], List[Dict[str, Any]]]:
    """Problems with a mapping (empty when it fits), and the rows it applies to."""
    known = identified_objects(schema)
    if not known:
        return ["The template identifies nothing yet: give the objects that stand for real things an identity "
                "(propose a template change) before importing reference data."], []
    objects = mapping.get("objects") if isinstance(mapping, dict) else None
    if not isinstance(objects, list) or not objects:
        return ['A mapping needs "objects": a list of {entity_type, id_column, name_column, attributes, alias_columns}.'], []
    try:
        rows = rows_at(data, mapping.get("records_path") or "")
    except SourceError as exc:
        return [str(exc)], []
    columns = set(columns_of(rows))
    problems: List[str] = []
    seen_types = set()
    for i, obj in enumerate(objects):
        where = f"objects[{i}]"
        if not isinstance(obj, dict):
            problems.append(f"{where} must be an object.")
            continue
        etype = obj.get("entity_type")
        if etype not in known:
            problems.append(f"{where}: '{etype}' is not something this template identifies; identified: {sorted(known)}.")
        elif etype in seen_types:
            problems.append(f"{where}: '{etype}' is mapped twice; map each kind of thing once.")
        seen_types.add(etype)
        if not obj.get("id_column"):
            problems.append(f"{where}: id_column is required (the column that identifies each {etype}).")
        used = [obj.get("id_column"), obj.get("name_column"), *(obj.get("attributes") or {}).values(), *(obj.get("alias_columns") or [])]
        for column in [c for c in used if c]:
            if column not in columns:
                problems.append(f"{where}: there is no column '{column}'. Columns: {sorted(columns)}.")
        if not isinstance(obj.get("attributes") or {}, dict):
            problems.append(f"{where}: attributes must map attribute names to columns.")
    return problems, rows


async def _existing(db: AsyncSession, template_id: str, entity_type: str) -> Dict[str, Entity]:
    entities = (await db.execute(select(Entity).where(Entity.template_id == template_id, Entity.entity_type == entity_type))).scalars().all()
    return {normalize_id(e.identifier_value): e for e in entities if e.identifier_value}


def _records(rows: List[Dict[str, Any]], obj: Dict[str, Any]) -> Tuple[Dict[str, Dict[str, Any]], int, int]:
    """One record per identity (later rows win), the rows without an identity, and ids written two ways."""
    by_id: Dict[str, Dict[str, Any]] = {}
    skipped = inconsistent = 0
    for row in rows:
        ident = _clean(row.get(obj["id_column"]))
        if not ident:
            skipped += 1
            continue
        name = _clean(row.get(obj.get("name_column"))) if obj.get("name_column") else ""
        key = normalize_id(ident)
        prev = by_id.get(key)
        if prev and name and prev["name"] and normalize_label(prev["name"]) != normalize_label(name):
            inconsistent += 1
        aliases = []
        for column in obj.get("alias_columns") or []:
            value = row.get(column)
            aliases += [_clean(v) for v in value] if isinstance(value, list) else [_clean(value)]
        by_id[key] = {
            "id": ident, "name": name or (prev or {}).get("name") or ident,
            "attributes": {attr: row.get(col) for attr, col in (obj.get("attributes") or {}).items() if row.get(col) not in (None, "")},
            "aliases": [a for a in aliases if a],
        }
    return by_id, skipped, inconsistent


async def preview(db: AsyncSession, template_id: str, rows: List[Dict[str, Any]], mapping: Dict[str, Any]) -> List[Dict[str, Any]]:
    """What importing would do, per object: new, already known, renamed, skipped."""
    out = []
    for obj in mapping["objects"]:
        records, skipped, inconsistent = _records(rows, obj)
        existing = await _existing(db, template_id, obj["entity_type"])
        known = [k for k in records if k in existing]
        renamed = [k for k in known if normalize_label(existing[k].name) != normalize_label(records[k]["name"])]
        out.append({
            "entity_type": obj["entity_type"], "rows": len(rows), "records": len(records),
            "new": len(records) - len(known), "already_known": len(known), "names_differ": len(renamed),
            "rows_without_id": skipped, "ids_written_two_ways": inconsistent,
            "examples": [records[k] for k in list(records)[:3]],
        })
    return out


async def import_records(db: AsyncSession, source: KnowledgeSource, mapping: Dict[str, Any], *, schema: Dict[str, Any], actor: str) -> Dict[str, Any]:
    """
    Creates or updates one entity per identity. A known entity keeps the name people or the
    agent gave it; a differing name from the source becomes an alias, so both readings are
    recognized. Entities that came from an import take the source's current name.
    """
    problems, rows = check_mapping(schema, source.data, mapping)
    if problems:
        raise HTTPException(status_code=409, detail={"message": "The mapping no longer fits the data", "problems": problems})
    known_objects = identified_objects(schema)
    totals = {"created": 0, "updated": 0, "aliases_added": 0, "rows_without_id": 0}
    for obj in mapping["objects"]:
        records, skipped, _ = _records(rows, obj)
        totals["rows_without_id"] += skipped
        existing = await _existing(db, source.template_id, obj["entity_type"])
        id_field = known_objects[obj["entity_type"]]["identity"]["id_field"]
        for key, rec in records.items():
            entity = existing.get(key)
            if entity is None:
                entity = Entity(
                    id=str(uuid.uuid4()), tenant_id=source.tenant_id, template_id=source.template_id,
                    entity_type=obj["entity_type"], name=rec["name"], identifier_key=id_field,
                    identifier_value=rec["id"], attributes=rec["attributes"], confidence=1.0, created_by="import",
                )
                db.add(entity)
                names = {normalize_label(rec["name"])}
                totals["created"] += 1
            else:
                names = {normalize_label(entity.name)} | {normalize_label(a.alias) for a in entity.aliases}
                if normalize_label(entity.name) != normalize_label(rec["name"]):
                    if entity.created_by == "import":
                        rec["aliases"].append(entity.name)
                        entity.name = rec["name"]
                    else:
                        rec["aliases"].append(rec["name"])
                entity.attributes = {**(entity.attributes or {}), **rec["attributes"]}
                totals["updated"] += 1
            for alias in rec["aliases"]:
                if normalize_label(alias) not in names | {normalize_label(entity.name)}:
                    db.add(EntityAlias(entity_id=entity.id, template_id=source.template_id, alias=alias, source=f"source {source.name}"))
                    names.add(normalize_label(alias))
                    totals["aliases_added"] += 1
    source.mapping = mapping
    source.status = "READY"
    source.last_error = None
    source.imported_count = sum(len(_records(rows, o)[0]) for o in mapping["objects"])
    db.add(AuditLog(
        tenant_id=source.tenant_id, template_id=source.template_id, event_type="KNOWLEDGE_IMPORTED",
        entity_type="KnowledgeSource", entity_id=source.id, actor=actor, details={"source": source.name, **totals},
    ))
    return totals
