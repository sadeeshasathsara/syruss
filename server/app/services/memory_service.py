"""
What Syruss remembers: storing, finding and forgetting memories, by scope.

Ranking blends meaning, importance and recency:
    score = 0.65 * relevance + 0.25 * importance + 0.10 * recency      (pinned always first)
relevance is cosine similarity on embeddings where the database has pgvector and the memory has
an embedding, otherwise full-text rank; recency halves every 60 days since last use.

Privacy is enforced here, not by callers being careful: personal memories are only ever returned
for their own person, and conversation memories only for their own chat.
"""
import math
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple
from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.models.memory import Memory

SCOPES = ("workspace", "personal", "conversation")
KINDS = ("lesson", "observation", "preference", "fact", "summary")
HALF_LIFE_DAYS = 60
_vector_column: Optional[bool] = None


async def has_vectors(db: AsyncSession) -> bool:
    """Whether this database stores embeddings (pgvector installed when migration 0003 ran)."""
    global _vector_column
    if _vector_column is None:
        _vector_column = bool((await db.execute(text(
            "SELECT 1 FROM information_schema.columns WHERE table_name = 'memories' AND column_name = 'embedding'"
        ))).scalar())
    return _vector_column


def _vector_literal(embedding: Sequence[float]) -> str:
    return "[" + ",".join(f"{float(v):.6f}" for v in embedding) + "]"


def _normalize(content: str) -> str:
    return re.sub(r"\s+", " ", content.strip().lower())


def memory_dict(m: Memory, score: Optional[float] = None) -> Dict[str, Any]:
    out = {
        "id": m.id, "scope": m.scope, "kind": m.kind, "content": m.content, "data": m.data, "importance": m.importance,
        "pinned": m.pinned, "source": m.source, "created_by": m.created_by, "conversation_id": m.conversation_id,
        "use_count": m.use_count, "last_used_at": m.last_used_at, "created_at": m.created_at, "updated_at": m.updated_at,
    }
    if score is not None:
        out["score"] = round(score, 4)
    return out


async def set_embedding(db: AsyncSession, memory_id: str, embedding: Optional[Sequence[float]]) -> bool:
    """Stores an embedding if this database can; returns whether it did."""
    if not embedding or not await has_vectors(db) or len(embedding) != settings.EMBEDDING_DIM:
        return False
    await db.execute(text("UPDATE memories SET embedding = CAST(:v AS vector), needs_embedding = false WHERE id = :id"),
                     {"v": _vector_literal(embedding), "id": memory_id})
    return True


async def add(
    db: AsyncSession,
    *,
    tenant_id: str,
    template_id: str,
    scope: str,
    kind: str,
    content: str,
    created_by: str,
    user_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    importance: float = 0.5,
    data: Optional[Dict[str, Any]] = None,
    source: Optional[Dict[str, Any]] = None,
    embedding: Optional[Sequence[float]] = None,
) -> Tuple[Memory, bool]:
    """Remembers something; the same thing said again strengthens the existing memory. Returns (memory, created)."""
    if scope not in SCOPES or kind not in KINDS:
        raise ValueError(f"scope must be one of {SCOPES} and kind one of {KINDS}")
    existing = [m for m in (await db.execute(select(Memory).where(
        Memory.template_id == template_id, Memory.scope == scope, Memory.kind == kind,
        Memory.user_id.is_(None) if user_id is None else Memory.user_id == user_id, Memory.archived.is_(False),
    ))).scalars().all() if _normalize(m.content) == _normalize(content)]
    if existing:
        m = existing[0]
        m.importance = min(1.0, max(m.importance, importance) + 0.05)
        m.updated_at = datetime.now(timezone.utc)
        return m, False
    m = Memory(
        tenant_id=tenant_id, template_id=template_id, scope=scope, kind=kind, content=content.strip(),
        user_id=user_id, conversation_id=conversation_id, importance=max(0.0, min(1.0, importance)),
        data=data or {}, source=source or {}, created_by=created_by,
    )
    db.add(m)
    await db.flush()
    await set_embedding(db, m.id, embedding)
    return m, True


def _visible(template_id: str, scopes: Sequence[str], user_id: Optional[str], conversation_id: Optional[str]):
    """Which memories a request may see: personal only its person's, conversation only its chat's."""
    allowed = []
    if "workspace" in scopes:
        allowed.append(Memory.scope == "workspace")
    if "personal" in scopes and user_id:
        allowed.append((Memory.scope == "personal") & (Memory.user_id == user_id))
    if "conversation" in scopes and conversation_id:
        allowed.append((Memory.scope == "conversation") & (Memory.conversation_id == conversation_id))
    return [Memory.template_id == template_id, Memory.archived.is_(False), or_(*allowed) if allowed else text("false")]


def _recency(m: Memory, now: datetime) -> float:
    last = m.last_used_at or m.updated_at or m.created_at
    days = max(0.0, (now - last).total_seconds() / 86400) if last else 0.0
    return math.pow(0.5, days / HALF_LIFE_DAYS)


async def search(
    db: AsyncSession,
    *,
    template_id: str,
    query: str = "",
    embedding: Optional[Sequence[float]] = None,
    scopes: Sequence[str] = ("workspace",),
    user_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    kinds: Optional[Sequence[str]] = None,
    limit: int = 8,
    touch: bool = True,
) -> List[Tuple[Memory, float]]:
    """The most useful memories for a query (or, without one, the most important), best first."""
    where = _visible(template_id, scopes, user_id, conversation_id)
    if kinds:
        where.append(Memory.kind.in_(kinds))
    relevance: Dict[str, float] = {}
    pool = max(limit * 4, 20)

    if embedding and await has_vectors(db) and len(embedding) == settings.EMBEDDING_DIM:
        rows = (await db.execute(
            select(Memory.id, text("1 - (embedding <=> CAST(:q AS vector)) AS similarity"))
            .where(*where, text("embedding IS NOT NULL"))
            .order_by(text("embedding <=> CAST(:q AS vector)")).limit(pool),
            {"q": _vector_literal(embedding)},
        )).all()
        relevance.update({r[0]: max(0.0, float(r[1])) for r in rows})
    # Any of the query's words (an AND of every word would rarely match a chat message).
    words = list(dict.fromkeys(re.findall(r"[^\W_]{3,}", query.lower())))[:12]
    if words:
        rows = (await db.execute(
            select(Memory.id, text("ts_rank(to_tsvector('simple', content), to_tsquery('simple', :t)) AS rank"))
            .where(*where, text("to_tsvector('simple', content) @@ to_tsquery('simple', :t)"))
            .order_by(text("rank DESC")).limit(pool),
            {"t": " | ".join(words)},
        )).all()
        for mid, rank in rows:
            relevance[mid] = max(relevance.get(mid, 0.0), min(1.0, float(rank) * 10))

    stmt = select(Memory).where(*where)
    if query.strip() or embedding:
        if not relevance:
            stmt = stmt.where(Memory.pinned.is_(True))
        else:
            stmt = stmt.where(or_(Memory.id.in_(list(relevance)), Memory.pinned.is_(True)))
    candidates = (await db.execute(stmt.order_by(Memory.importance.desc(), Memory.updated_at.desc()).limit(max(pool, 60)))).scalars().all()

    now = datetime.now(timezone.utc)
    has_query = bool(query.strip() or embedding)
    scored = []
    for m in candidates:
        rel = relevance.get(m.id, 0.0) if has_query else 0.5
        score = (2.0 if m.pinned else 0.0) + 0.65 * rel + 0.25 * m.importance + 0.10 * _recency(m, now)
        scored.append((m, score))
    scored.sort(key=lambda pair: pair[1], reverse=True)
    best = scored[:limit]
    if touch:
        for m, _ in best:
            m.use_count += 1
            m.last_used_at = now
    return best


async def needing_embeddings(db: AsyncSession, template_id: str, limit: int = 20) -> List[Memory]:
    if not await has_vectors(db):
        return []
    return list((await db.execute(select(Memory).where(
        Memory.template_id == template_id, Memory.needs_embedding.is_(True), Memory.archived.is_(False),
    ).limit(limit))).scalars().all())


async def conversation_summary(db: AsyncSession, template_id: str, conversation_id: str) -> Optional[Memory]:
    return (await db.execute(select(Memory).where(
        Memory.template_id == template_id, Memory.scope == "conversation", Memory.kind == "summary",
        Memory.conversation_id == conversation_id,
    ))).scalar_one_or_none()


# ---------------------------------------------------------------------------
# Consolidation (run by the reflection job, before the agent reflects)
# ---------------------------------------------------------------------------

STALE_DAYS = 30
STALE_BELOW = 0.5        # importance under which an unused observation may retire
DUPLICATE_SIMILARITY = 0.95


def _keeper(a: Memory, b: Memory) -> Tuple[Memory, Memory]:
    """Of two memories saying the same thing: keep the pinned, then the more important, the more
    used, the older one."""
    def rank(m: Memory):
        return (m.pinned, m.importance, m.use_count, -(m.created_at.timestamp() if m.created_at else 0))
    return (a, b) if rank(a) >= rank(b) else (b, a)


async def _duplicate_pairs(db: AsyncSession, template_id: str) -> List[Tuple[str, str, float]]:
    """Pairs of active memories in the same scope, kind and person that say the same thing:
    by meaning where embeddings exist, by normalized text otherwise."""
    pairs: List[Tuple[str, str, float]] = []
    if await has_vectors(db):
        rows = await db.execute(text(
            "SELECT a.id, b.id, 1 - (a.embedding <=> b.embedding) AS similarity FROM memories a "
            "JOIN memories b ON b.template_id = a.template_id AND b.scope = a.scope AND b.kind = a.kind "
            "  AND coalesce(b.user_id, '') = coalesce(a.user_id, '') AND a.id < b.id "
            "WHERE a.template_id = :t AND a.scope IN ('workspace', 'personal') AND NOT a.archived AND NOT b.archived "
            "  AND a.embedding IS NOT NULL AND b.embedding IS NOT NULL AND (a.embedding <=> b.embedding) <= :d "
            "ORDER BY similarity DESC LIMIT 500"
        ), {"t": template_id, "d": 1 - DUPLICATE_SIMILARITY})
        pairs.extend((a, b, float(s)) for a, b, s in rows.all())
    active = (await db.execute(select(Memory).where(
        Memory.template_id == template_id, Memory.scope.in_(("workspace", "personal")), Memory.archived.is_(False),
    ))).scalars().all()
    first: Dict[Tuple[str, str, str, str], str] = {}
    for m in active:
        key = (m.scope, m.kind, m.user_id or "", re.sub(r"[^\w ]+", "", _normalize(m.content)))
        if key in first:
            pairs.append((first[key], m.id, 1.0))
        else:
            first[key] = m.id
    return pairs


async def consolidate(db: AsyncSession, template_id: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    """
    Deterministic upkeep, nothing a model decides:
      * retires workspace observations that were never used, matter little and are older than
        STALE_DAYS (they stay in the archive, visible on the memory page);
      * merges memories that say the same thing: the keeper takes the higher importance and the
        combined use, the other is archived pointing at it.
    Pinned memories are never retired. Returns what changed, for the reflection run and the log.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now.timestamp() - STALE_DAYS * 86400
    retired: List[Dict[str, Any]] = []
    for m in (await db.execute(select(Memory).where(
        Memory.template_id == template_id, Memory.scope == "workspace", Memory.kind == "observation",
        Memory.archived.is_(False), Memory.pinned.is_(False), Memory.use_count == 0, Memory.importance < STALE_BELOW,
    ))).scalars().all():
        if m.created_at and m.created_at.timestamp() < cutoff:
            m.archived = True
            m.data = {**(m.data or {}), "retired": {"at": now.isoformat(), "why": "unused and of low importance"}}
            retired.append({"id": m.id, "content": m.content[:200]})

    merged: List[Dict[str, Any]] = []
    gone: set = set()
    for a_id, b_id, similarity in await _duplicate_pairs(db, template_id):
        if a_id in gone or b_id in gone:
            continue
        a, b = await db.get(Memory, a_id), await db.get(Memory, b_id)
        if not a or not b or a.archived or b.archived:
            continue
        keep, drop = _keeper(a, b)
        keep.importance = max(keep.importance, drop.importance)
        keep.use_count += drop.use_count
        keep.pinned = keep.pinned or drop.pinned
        keep.data = {**(keep.data or {}), "merged_from": [*(keep.data or {}).get("merged_from", []), drop.id]}
        drop.archived = True
        drop.data = {**(drop.data or {}), "merged_into": keep.id}
        gone.add(drop.id)
        merged.append({"kept": keep.content[:200], "merged": drop.content[:200], "similarity": round(similarity, 3)})
    await db.flush()
    return {"retired": len(retired), "merged": len(merged), "retired_items": retired[:20], "merged_items": merged[:20]}
