"""
The workspace graph, and the identity checks it makes possible.

Every committed record adds sightings: each identified thing in it, together with the things it
appears alongside (the other references in its row, and what the document as a whole is about,
e.g. the site on a roster). From the sightings the platform knows who is usually where, and two
checks follow when the agent commits a record:

  * lookalike (blocking): what was read has never appeared in this context, but a different thing
    that regularly does is a near match (an ID a digit apart, a name a letter or two apart). The
    classic handwriting misread. The agent must look again and either acknowledge the signal with
    its reason, or send the document to review.
  * stranger (advisory): something with a history elsewhere appears, for the first time, in a
    context that has regulars of its kind. Shown to the agent and reviewers; it doesn't block.

Where Apache AGE is installed, sightings are mirrored into one graph per workspace and the graph
questions (who appears with what, who shares contexts with whom) run as Cypher. Without AGE, or
until a workspace's graph has been built (the scheduler's "graph" job), the same questions are
answered in SQL from the sightings themselves, so results never depend on the extension.
"""
import difflib
import hashlib
import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.document import Document, DocumentStatus
from app.models.extraction import ExtractionResult
from app.models.graph import EntitySighting
from app.models.jobs import WorkspaceJob
from app.services.identity_check import normalize_id, normalize_label
from app.services.rule_engine import normalize_schema

logger = logging.getLogger("core.graph")

REGULAR = 3              # sightings with a context that make something one of its regulars
MAX_PAIRS = 2000         # per document, so one enormous record can't flood the graph
LOOKALIKE_NAME = 0.8     # name similarity that counts as "a letter or two apart"
EDGE = "SEEN_WITH"


@dataclass(frozen=True)
class Thing:
    key: str
    type: str
    label: str


@dataclass
class Neighbour:
    key: str
    type: str
    label: str
    count: int


# ---------------------------------------------------------------------------
# What a record says appears together
# ---------------------------------------------------------------------------

def rows_of(data: Dict[str, Any], obj_type: str) -> List[Dict[str, Any]]:
    value = (data or {}).get(obj_type)
    if isinstance(value, dict):
        return [value]
    return [r for r in value or [] if isinstance(r, dict)] if isinstance(value, list) else []


def thing_key(obj_type: str, raw_id: Any) -> Optional[str]:
    nid = normalize_id(raw_id)
    return f"{obj_type}:{nid}"[:400] if nid else None


def things_in_row(objects: Dict[str, Dict[str, Any]], data: Dict[str, Any], obj_type: str,
                   row: Dict[str, Any]) -> List[Tuple[Thing, str]]:
    """The identified things a row names: itself (if its object has an identity) and its references.
    Each comes with the field it was read from."""
    spec = objects[obj_type]
    found: List[Tuple[Thing, str]] = []
    ident = spec["identity"]
    if ident:
        key = thing_key(obj_type, row.get(ident["id_field"]))
        if key:
            found.append((Thing(key, obj_type, str(row.get(ident["label_field"]) or "")[:255]), ident["id_field"]))
    for fname, fspec in spec["fields"].items():
        target = fspec.get("resolves_to")
        target_ident = objects.get(target, {}).get("identity") if target else None
        if not target_ident or row.get(fname) in (None, ""):
            continue
        key = thing_key(target, row.get(fname))
        if not key:
            continue
        label = next((str(r.get(target_ident["label_field"]) or "") for r in rows_of(data, target)
                      if thing_key(target, r.get(target_ident["id_field"])) == key), "")
        found.append((Thing(key, target, label[:255]), fname))
    return found


def pairs_in_record(schema: Dict[str, Any], data: Dict[str, Any]) -> Set[Tuple[Thing, Thing]]:
    """Unordered pairs of things that appear together, each pair once (smaller key first)."""
    objects = normalize_schema(schema)
    document_level: List[Thing] = []
    row_level: List[List[Thing]] = []
    for obj_type, spec in objects.items():
        for row in rows_of(data, obj_type):
            things = [t for t, _ in things_in_row(objects, data, obj_type, row)]
            if spec["multiple"]:
                row_level.append(things)
            else:
                document_level.extend(things)

    pairs: Set[Tuple[Thing, Thing]] = set()

    def pair(a: Thing, b: Thing) -> None:
        if a.key != b.key and len(pairs) < MAX_PAIRS:
            pairs.add((a, b) if a.key < b.key else (b, a))

    for i, a in enumerate(document_level):
        for b in document_level[i + 1:]:
            pair(a, b)
    for things in row_level:
        for i, a in enumerate(things):
            for b in things[i + 1:]:
                pair(a, b)
            for d in document_level:
                pair(a, d)
    return pairs


# ---------------------------------------------------------------------------
# Recording sightings (and mirroring them into AGE)
# ---------------------------------------------------------------------------

async def record_sightings(db: AsyncSession, *, tenant_id: str, template_id: str, document_id: str,
                           schema: Dict[str, Any], data: Dict[str, Any]) -> int:
    """Replaces the document's sightings with what its committed record says."""
    old = await _document_pairs(db, document_id)
    await db.execute(delete(EntitySighting).where(EntitySighting.document_id == document_id))
    pairs = pairs_in_record(schema, data)
    for a, b in pairs:
        for s, c in ((a, b), (b, a)):
            db.add(EntitySighting(
                tenant_id=tenant_id, template_id=template_id, document_id=document_id,
                subject_key=s.key, subject_type=s.type, subject_label=s.label,
                context_key=c.key, context_type=c.type, context_label=c.label,
            ))
    await db.flush()
    await _mirror(db, template_id, {(a.key, b.key) for a, b in pairs} | old)
    return len(pairs)


async def forget_sightings(db: AsyncSession, template_id: str, document_id: str) -> None:
    """The document's record is no longer committed (sent back to review, or deleted)."""
    old = await _document_pairs(db, document_id)
    if old:
        await db.execute(delete(EntitySighting).where(EntitySighting.document_id == document_id))
        await db.flush()
        await _mirror(db, template_id, old)


async def _document_pairs(db: AsyncSession, document_id: str) -> Set[Tuple[str, str]]:
    rows = await db.execute(select(EntitySighting.subject_key, EntitySighting.context_key)
                            .where(EntitySighting.document_id == document_id))
    return {(s, c) for s, c in rows.all() if s < c}


async def rebuild_sightings(db: AsyncSession, *, tenant_id: str, template_id: str, schema: Dict[str, Any]) -> int:
    """Sightings for every committed record (a workspace whose records predate the graph)."""
    rows = await db.execute(
        select(ExtractionResult.document_id, ExtractionResult.extracted_data)
        .join(Document, Document.id == ExtractionResult.document_id)
        .where(ExtractionResult.template_id == template_id, Document.status == DocumentStatus.COMPLETED)
    )
    await db.execute(delete(EntitySighting).where(EntitySighting.template_id == template_id))
    pairs = 0
    for document_id, data in rows.all():
        for a, b in pairs_in_record(schema, data or {}):
            pairs += 1
            for s, c in ((a, b), (b, a)):
                db.add(EntitySighting(
                    tenant_id=tenant_id, template_id=template_id, document_id=document_id,
                    subject_key=s.key, subject_type=s.type, subject_label=s.label,
                    context_key=c.key, context_type=c.type, context_label=c.label,
                ))
    await db.flush()
    return pairs


# ---------------------------------------------------------------------------
# Apache AGE
# ---------------------------------------------------------------------------

_age_available: Optional[bool] = None


async def has_age(db: AsyncSession) -> bool:
    """AGE is installed in this database and loaded by the server (shared_preload_libraries)."""
    global _age_available
    if _age_available is None:
        row = (await db.execute(text(
            "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'age'), "
            "current_setting('shared_preload_libraries') LIKE '%age%'"
        ))).one()
        _age_available = bool(row[0] and row[1])
    return _age_available


def graph_name(template_id: str) -> str:
    return ("ws_" + re.sub(r"[^a-z0-9_]", "", template_id.lower()))[:63]


def _lit(value: Any) -> str:
    """A Cypher string literal. Values come from documents, so they are untrusted: control characters
    and '$' (which could close the SQL dollar quote) are removed, quotes and backslashes escaped."""
    s = re.sub(r"[\x00-\x1f$]", "", str(value))
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


async def _cypher(db: AsyncSession, template_id: str, query: str, columns: Iterable[str]) -> List[Tuple[Any, ...]]:
    """Runs Cypher on the workspace's graph. Bypasses SQLAlchemy's bind-parameter parsing (Cypher
    is full of colons); every value in `query` must come through _lit or int()."""
    cols = ", ".join(f"{c} ag_catalog.agtype" for c in columns)
    sql = f"SELECT * FROM ag_catalog.cypher('{graph_name(template_id)}', $cy$ {query} $cy$) AS ({cols})"
    conn = await db.connection()
    # AGE resolves its own operators through the search path; add it for this statement only. (If the
    # statement fails, the caller's savepoint rollback undoes the setting along with everything else.)
    path = (await conn.exec_driver_sql("SELECT current_setting('search_path')")).scalar()
    await conn.exec_driver_sql("SELECT set_config('search_path', 'ag_catalog, ' || current_setting('search_path'), true)")
    rows = (await conn.exec_driver_sql(sql)).all()
    await conn.exec_driver_sql(f"SELECT set_config('search_path', '{path.replace(chr(39), chr(39) * 2)}', true)")
    return [tuple(_agtype(v) for v in row) for row in rows]


def _agtype(value: Any) -> Any:
    if value is None:
        return None
    raw = str(value)
    for suffix in ("::vertex", "::edge", "::path"):
        raw = raw.removesuffix(suffix)
    try:
        return json.loads(raw)
    except ValueError:
        return raw


async def _ensure_graph(db: AsyncSession, template_id: str) -> None:
    name = graph_name(template_id)
    conn = await db.connection()
    exists = (await conn.exec_driver_sql(f"SELECT count(*) FROM ag_catalog.ag_graph WHERE name = '{name}'")).scalar()
    if not exists:
        await conn.exec_driver_sql(f"SELECT ag_catalog.create_graph('{name}')")


async def _edge_counts(db: AsyncSession, template_id: str, pairs: Set[Tuple[str, str]]) -> Dict[Tuple[str, str], Tuple[int, str, str, str, str]]:
    """(a, b) -> (count, a type, a label, b type, b label), for pairs stored a < b."""
    if not pairs:
        return {}
    keys = {k for p in pairs for k in p}
    rows = await db.execute(
        select(EntitySighting.subject_key, EntitySighting.context_key, func.count(),
               func.max(EntitySighting.subject_type), func.max(EntitySighting.subject_label),
               func.max(EntitySighting.context_type), func.max(EntitySighting.context_label))
        .where(EntitySighting.template_id == template_id, EntitySighting.subject_key.in_(keys),
               EntitySighting.subject_key < EntitySighting.context_key)
        .group_by(EntitySighting.subject_key, EntitySighting.context_key)
    )
    return {(a, b): (n, at, al, bt, bl) for a, b, n, at, al, bt, bl in rows.all() if (a, b) in pairs}


async def _write_edges(db: AsyncSession, template_id: str, edges: Dict[Tuple[str, str], Tuple[int, str, str, str, str]],
                       removed: Iterable[Tuple[str, str]] = ()) -> None:
    for start in range(0, len(edges), 200):
        chunk = list(edges.items())[start:start + 200]
        items = ", ".join(
            f"{{a: {_lit(a)}, at: {_lit(at)}, al: {_lit(al)}, b: {_lit(b)}, bt: {_lit(bt)}, bl: {_lit(bl)}, n: {int(n)}}}"
            for (a, b), (n, at, al, bt, bl) in chunk
        )
        await _cypher(db, template_id, (
            f"UNWIND [{items}] AS p "
            "MERGE (x:Thing {key: p.a}) SET x.type = p.at, x.label = p.al "
            "MERGE (y:Thing {key: p.b}) SET y.type = p.bt, y.label = p.bl "
            f"MERGE (x)-[r:{EDGE}]->(y) SET r.times = p.n RETURN count(r)"
        ), ["n"])
    for a, b in removed:
        await _cypher(db, template_id, f"MATCH (:Thing {{key: {_lit(a)}}})-[r:{EDGE}]->(:Thing {{key: {_lit(b)}}}) DELETE r RETURN 1", ["n"])


async def _mirror(db: AsyncSession, template_id: str, pairs: Set[Tuple[str, str]]) -> None:
    """Brings the AGE edges for `pairs` in line with the sightings. A failure here never fails the
    commit: the savepoint rolls back only the mirror, and the graph job rebuilds it later."""
    if not pairs or not await has_age(db) or not await graph_ready(db, template_id):
        return
    try:
        async with db.begin_nested():
            counts = await _edge_counts(db, template_id, pairs)
            await _write_edges(db, template_id, counts, removed=[p for p in pairs if p not in counts])
    except Exception as exc:
        logger.warning("Mirroring the graph of workspace %s failed; it will be rebuilt: %s", template_id, exc)
        await _mark_graph_stale(db, template_id)


async def rebuild_graph(db: AsyncSession, template_id: str) -> int:
    """Rebuilds the workspace's AGE graph from all of its sightings."""
    conn = await db.connection()
    name = graph_name(template_id)
    if (await conn.exec_driver_sql(f"SELECT count(*) FROM ag_catalog.ag_graph WHERE name = '{name}'")).scalar():
        await conn.exec_driver_sql(f"SELECT ag_catalog.drop_graph('{name}', true)")
    await _ensure_graph(db, template_id)
    pairs = {(s, c) for s, c in (await db.execute(
        select(EntitySighting.subject_key, EntitySighting.context_key).distinct()
        .where(EntitySighting.template_id == template_id, EntitySighting.subject_key < EntitySighting.context_key)
    )).all()}
    counts = await _edge_counts(db, template_id, pairs)
    await _write_edges(db, template_id, counts)
    return len(counts)


async def graph_ready(db: AsyncSession, template_id: str) -> bool:
    job = await db.get(WorkspaceJob, (template_id, "graph"))
    return bool(job and (job.state or {}).get("age_synced"))


async def _mark_graph_stale(db: AsyncSession, template_id: str) -> None:
    job = await db.get(WorkspaceJob, (template_id, "graph"))
    if job:
        job.state = {**(job.state or {}), "age_synced": False}


# ---------------------------------------------------------------------------
# Graph questions (Cypher when the graph is built, SQL otherwise)
# ---------------------------------------------------------------------------

async def _use_age(db: AsyncSession, template_id: str) -> bool:
    return await has_age(db) and await graph_ready(db, template_id)


async def appears_with(db: AsyncSession, template_id: str, key: str, of_type: Optional[str] = None,
                       limit: int = 50) -> List[Neighbour]:
    """What `key` has appeared with, most often first (optionally only things of one type)."""
    if await _use_age(db, template_id):
        where = f" WHERE b.type = {_lit(of_type)}" if of_type else ""
        rows = await _cypher(db, template_id, (
            f"MATCH (a:Thing {{key: {_lit(key)}}})-[r:{EDGE}]-(b:Thing){where} "
            f"RETURN b.key, b.type, b.label, r.times ORDER BY r.times DESC LIMIT {int(limit)}"
        ), ["key", "type", "label", "count"])
        return [Neighbour(k, t, l or "", int(n or 0)) for k, t, l, n in rows]
    stmt = (
        select(EntitySighting.context_key, func.max(EntitySighting.context_type),
               func.max(EntitySighting.context_label), func.count())
        .where(EntitySighting.template_id == template_id, EntitySighting.subject_key == key)
        .group_by(EntitySighting.context_key).order_by(func.count().desc()).limit(limit)
    )
    if of_type:
        stmt = stmt.where(EntitySighting.context_type == of_type)
    return [Neighbour(k, t, l or "", n) for k, t, l, n in (await db.execute(stmt)).all()]


async def peers(db: AsyncSession, template_id: str, key: str, limit: int = 10) -> List[Neighbour]:
    """Things of the same kind that share contexts with `key` (count = contexts shared)."""
    obj_type = key.split(":", 1)[0]
    if await _use_age(db, template_id):
        rows = await _cypher(db, template_id, (
            f"MATCH (a:Thing {{key: {_lit(key)}}})-[:{EDGE}]-(c:Thing)-[:{EDGE}]-(b:Thing) "
            f"WHERE b.type = {_lit(obj_type)} AND b.key <> a.key "
            "RETURN b.key, b.label, c.key"
        ), ["key", "label", "context"])
        # Counted here: AGE can't order by an aggregate's alias.
        shared: Dict[str, Set[str]] = defaultdict(set)
        labels: Dict[str, str] = {}
        for k, l, c in rows:
            shared[k].add(c)
            labels[k] = l or ""
        ranked = sorted(shared, key=lambda k: (-len(shared[k]), k))[:limit]
        return [Neighbour(k, obj_type, labels[k], len(shared[k])) for k in ranked]
    mine = select(EntitySighting.context_key).where(
        EntitySighting.template_id == template_id, EntitySighting.subject_key == key).distinct().subquery()
    stmt = (
        select(EntitySighting.subject_key, func.max(EntitySighting.subject_label),
               func.count(func.distinct(EntitySighting.context_key)).label("shared"))
        .where(EntitySighting.template_id == template_id, EntitySighting.subject_type == obj_type,
               EntitySighting.subject_key != key, EntitySighting.context_key.in_(select(mine.c.context_key)))
        .group_by(EntitySighting.subject_key).order_by(text("shared DESC")).limit(limit)
    )
    return [Neighbour(k, obj_type, l or "", n) for k, l, n in (await db.execute(stmt)).all()]


async def backend(db: AsyncSession, template_id: str) -> str:
    return "age" if await _use_age(db, template_id) else "sql"


# ---------------------------------------------------------------------------
# Identity signals
# ---------------------------------------------------------------------------

def _close_ids(a: str, b: str) -> bool:
    """One character apart (substituted, missing or extra): how a digit gets misread."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    short, long_ = sorted((a, b), key=len)
    return any(long_[:i] + long_[i + 1:] == short for i in range(len(long_)))


def _close_names(a: str, b: str) -> bool:
    a, b = normalize_label(a), normalize_label(b)
    return bool(a and b and a != b and difflib.SequenceMatcher(None, a, b).ratio() >= LOOKALIKE_NAME)


def signal_id(code: str, path: str, other: str) -> str:
    return "sig_" + hashlib.sha1(f"{code}|{path}|{other}".encode()).hexdigest()[:10]


async def identity_signals(db: AsyncSession, *, template_id: str, schema: Dict[str, Any], data: Dict[str, Any],
                           document_id: str) -> List[Dict[str, Any]]:
    """
    Graph checks for an incoming record. Each signal: {id, code, path, message, blocking, other}.
    The document's own earlier sightings (a record being committed again) are not counted.
    """
    objects = normalize_schema(schema)
    own = await _document_pairs(db, document_id)
    signals: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    cache: Dict[Tuple[str, str], List[Neighbour]] = {}

    async def regulars(context: Thing, of_type: str) -> List[Neighbour]:
        if (context.key, of_type) not in cache:
            found = await appears_with(db, template_id, context.key, of_type=of_type, limit=200)
            for n in found:  # discount this document's own earlier sightings
                if (min(n.key, context.key), max(n.key, context.key)) in own:
                    n.count -= 1
            cache[(context.key, of_type)] = [n for n in found if n.count > 0]
        return cache[(context.key, of_type)]

    pairs = pairs_in_record(schema, data)
    with_context: Dict[str, Set[Thing]] = defaultdict(set)
    for a, b in pairs:
        with_context[a.key].add(b)
        with_context[b.key].add(a)

    for obj_type, spec in objects.items():
        for i, row in enumerate(rows_of(data, obj_type)):
            for thing, field in things_in_row(objects, data, obj_type, row):
                path = f"{obj_type}[{i}].{field}"
                for context in sorted(with_context.get(thing.key, ()), key=lambda t: t.key):
                    there = await regulars(context, thing.type)
                    if any(n.key == thing.key for n in there):
                        continue  # it has been here before
                    read_id = thing.key.split(":", 1)[1]
                    for n in there:
                        if n.count < REGULAR:
                            continue
                        n_id = n.key.split(":", 1)[1]
                        by_id, by_name = _close_ids(read_id, n_id), _close_names(thing.label, n.label)
                        if not (by_id or by_name):
                            continue
                        sid = signal_id("identity_lookalike", path, n.key)
                        if sid in seen:
                            continue
                        seen.add(sid)
                        what = f"{thing.type} {read_id}" + (f" ({thing.label})" if thing.label else "")
                        regular = f"{n_id}" + (f" ({n.label})" if n.label else "")
                        close = "ID is one character" if by_id else "name is very close"
                        signals.append({
                            "id": sid, "code": "identity_lookalike", "path": path, "blocking": True, "other": n.key,
                            "message": (f"{what} has never appeared with {context.type} {context.key.split(':', 1)[1]}, "
                                        f"but {regular} has, {n.count} times, and its {close} away from what was read. "
                                        "Look at the page again: if it really is what you read, acknowledge this signal "
                                        "with your reason; otherwise correct it or send the document to review."),
                        })
                    regular_count = sum(1 for n in there if n.count >= REGULAR)
                    if regular_count >= REGULAR and not any(s["path"] == path and s["blocking"] for s in signals):
                        elsewhere = await appears_with(db, template_id, thing.key, of_type=context.type, limit=3)
                        elsewhere = [e for e in elsewhere if e.count >= REGULAR]
                        sid = signal_id("identity_stranger", path, context.key)
                        if elsewhere and sid not in seen:
                            seen.add(sid)
                            places = ", ".join(e.key.split(":", 1)[1] for e in elsewhere)
                            signals.append({
                                "id": sid, "code": "identity_stranger", "path": path, "blocking": False, "other": context.key,
                                "message": (f"{thing.type} {read_id} usually appears with {context.type} {places}; this is its "
                                            f"first time with {context.key.split(':', 1)[1]}, which has {regular_count} regulars."),
                            })
    return signals
