"""
What Syruss remembers: storing, finding and forgetting memories, by scope.

Ranking uses Reciprocal Rank Fusion (RRF) to blend dense-vector and sparse-text retrieval lists,
then blends relevance with importance and adaptive recency:
    score = 0.65 * rrf_relevance + 0.25 * importance + 0.10 * recency  (pinned always first)
Adaptive recency: lessons and facts never decay (half-life = ∞); observations/preferences/summaries
halve every HALF_LIFE_DAYS (30) days since last use.

Privacy is enforced here, not by callers being careful: personal memories are only ever returned
for their own person, and conversation memories only for their own chat.

Pillar 2 — Semantic Conflict Resolution thresholds:
  >= REINFORCE_SIMILARITY  → same meaning, strengthen existing (no new row)
  >= CONFLICT_SIMILARITY   → contradictory update, archive old, insert new (supersede)
  <  CONFLICT_SIMILARITY   → genuinely new information, plain insert

Pillar 3 — Hybrid Search + Adaptive Decay:
  RRF_K constant controls rank-score smoothing (standard 60 from the RRF paper).
  DECAY_KINDS lists which memory kinds are subject to recency decay.
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

# Pillar 3: Adaptive decay — only these kinds age out; lessons and facts are permanent
HALF_LIFE_DAYS = 30          # was 60; observations now halve every 30 days (more aggressive)
DECAY_KINDS = frozenset(("observation", "preference", "summary"))  # lessons & facts never decay

# Pillar 3: RRF constant (from the original RRF paper; 60 is standard)
RRF_K = 60

_vector_column: Optional[bool] = None

# Pillar 2: Semantic Conflict Resolution thresholds
# >= REINFORCE_SIMILARITY  → same meaning, strengthen existing (no new row)
# >= CONFLICT_SIMILARITY   → contradictory update, archive old and insert new (supersede)
# < CONFLICT_SIMILARITY    → genuinely new information, plain insert
REINFORCE_SIMILARITY = 0.95   # same as DUPLICATE_SIMILARITY used in consolidate()
CONFLICT_SIMILARITY  = 0.82   # semantic clash threshold from the upgrade plan


# ---------------------------------------------------------------------------
# Pillar 3: RRF helpers and adaptive recency
# ---------------------------------------------------------------------------

def _rrf_score(rank: int) -> float:
    """Reciprocal Rank Fusion score for a result at zero-based rank position."""
    return 1.0 / (RRF_K + rank + 1)


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
    """
    Remembers something; returns (memory, created).

    Pillar 2 — Semantic Conflict Resolution:
      1. Exact text match  → reinforce (boost importance, no new row)
      2. Embedding similarity >= REINFORCE_SIMILARITY  → reinforce
      3. Embedding similarity >= CONFLICT_SIMILARITY   → semantic clash:
           archive the old memory (mark superseded_by=new_id), insert new
      4. Otherwise → plain insert

    Conversation summaries always replace their existing one (upsert).
    """
    if scope not in SCOPES or kind not in KINDS:
        raise ValueError(f"scope must be one of {SCOPES} and kind one of {KINDS}")

    now = datetime.now(timezone.utc)

    # ── 1. Exact text deduplication (fast path, no DB round-trip for embeddings) ──
    scope_filter = Memory.user_id.is_(None) if user_id is None else Memory.user_id == user_id
    existing_all = (await db.execute(select(Memory).where(
        Memory.template_id == template_id, Memory.scope == scope, Memory.kind == kind,
        scope_filter, Memory.archived.is_(False),
    ))).scalars().all()

    exact = [m for m in existing_all if _normalize(m.content) == _normalize(content)]
    if exact:
        m = exact[0]
        m.importance = min(1.0, max(m.importance, importance) + 0.05)
        m.updated_at = now
        return m, False

    # ── 2. Semantic similarity check (requires pgvector + embedding on new memory) ──
    superseded_ids: List[str] = []          # old memories this new one replaces
    reinforced: Optional[Memory] = None     # existing memory we strengthen instead of inserting

    if embedding and await has_vectors(db) and len(embedding) == settings.EMBEDDING_DIM:
        # Only compare within the same scope/kind/user partition
        vec_rows = await db.execute(text(
            "SELECT id, 1 - (embedding <=> CAST(:q AS vector)) AS sim "
            "FROM memories "
            "WHERE template_id = :t AND scope = :s AND kind = :k "
            "  AND COALESCE(user_id, '') = :u "
            "  AND archived = false AND embedding IS NOT NULL "
            "ORDER BY embedding <=> CAST(:q AS vector) LIMIT 20"
        ), {
            "q": _vector_literal(embedding),
            "t": template_id, "s": scope, "k": kind,
            "u": user_id or "",
        })
        sim_rows: List[Tuple[str, float]] = [(row[0], float(row[1])) for row in vec_rows.all()]

        for mem_id, sim in sim_rows:
            if sim >= REINFORCE_SIMILARITY:
                # Same meaning — strengthen the existing memory, skip insert
                existing = await db.get(Memory, mem_id)
                if existing and not existing.archived:
                    existing.importance = min(1.0, max(existing.importance, importance) + 0.05)
                    existing.updated_at = now
                    reinforced = existing
                    break  # only one reinforcement per call
            elif sim >= CONFLICT_SIMILARITY:
                # Semantic clash: this new memory supersedes the old one
                superseded_ids.append(mem_id)

        if reinforced is not None:
            return reinforced, False

    # ── 3. Insert the new memory ──
    m = Memory(
        tenant_id=tenant_id, template_id=template_id, scope=scope, kind=kind,
        content=content.strip(), user_id=user_id, conversation_id=conversation_id,
        importance=max(0.0, min(1.0, importance)),
        data=data or {}, source=source or {}, created_by=created_by,
    )
    db.add(m)
    await db.flush()   # get m.id before we reference it in superseded memories
    await set_embedding(db, m.id, embedding)

    # ── 4. Archive semantically-superseded memories ──
    for old_id in superseded_ids:
        old = await db.get(Memory, old_id)
        if old and not old.archived:
            old.archived = True
            old.updated_at = now
            old.data = {**(old.data or {}), "superseded_by": m.id,
                        "superseded_at": now.isoformat()}
    if superseded_ids:
        m.data = {**(m.data or {}), "supersedes": superseded_ids}

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
    """
    Adaptive recency decay per memory kind.

    Lessons and facts represent authoritative, long-lived knowledge — they never decay (score = 1.0).
    Observations, preferences and conversation summaries age out with HALF_LIFE_DAYS half-life.
    This prevents the agent from effectively forgetting important rules just because it hasn't
    invoked them recently, while keeping ephemeral observations lean.
    """
    if m.kind not in DECAY_KINDS:
        return 1.0   # lessons & facts are permanent — no decay
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
    """
    The most useful memories for a query (or, without one, the most important), best first.

    Pillar 3 — Hybrid Search with Reciprocal Rank Fusion (RRF):
      Dense retrieval (pgvector cosine similarity) and sparse retrieval (PostgreSQL full-text
      ts_rank) each produce an independent ranked list. RRF fuses them:

          rrf(d) = 1/(RRF_K + rank_dense) + 1/(RRF_K + rank_sparse)

      This prevents a single high-scoring retrieval channel from drowning out the other and
      is robust to result-score scale differences between cosine distance and ts_rank.

      A memory that appears in both lists gets credit from both channels.
      Pinned memories are always promoted to the top regardless of RRF score.
    """
    where = _visible(template_id, scopes, user_id, conversation_id)
    if kinds:
        where.append(Memory.kind.in_(kinds))
    pool = max(limit * 4, 20)

    # ── Dense retrieval: ranked by vector similarity ──
    # rank_dense[id] = zero-based position in the dense result list
    rank_dense: Dict[str, int] = {}
    if embedding and await has_vectors(db) and len(embedding) == settings.EMBEDDING_DIM:
        rows = (await db.execute(
            select(Memory.id, text("1 - (embedding <=> CAST(:q AS vector)) AS similarity"))
            .where(*where, text("embedding IS NOT NULL"))
            .order_by(text("embedding <=> CAST(:q AS vector)")).limit(pool),
            {"q": _vector_literal(embedding)},
        )).all()
        for pos, (mid, _sim) in enumerate(rows):
            rank_dense[mid] = pos

    # ── Sparse retrieval: ranked by full-text ts_rank ──
    # rank_sparse[id] = zero-based position in the sparse result list
    rank_sparse: Dict[str, int] = {}
    words = list(dict.fromkeys(re.findall(r"[^\W_]{3,}", query.lower())))[:12]
    if words:
        rows = (await db.execute(
            select(Memory.id, text("ts_rank(to_tsvector('simple', content), to_tsquery('simple', :t)) AS rank"))
            .where(*where, text("to_tsvector('simple', content) @@ to_tsquery('simple', :t)"))
            .order_by(text("rank DESC")).limit(pool),
            {"t": " | ".join(words)},
        )).all()
        for pos, (mid, _rank) in enumerate(rows):
            rank_sparse[mid] = pos

    # ── RRF fusion: relevance score per memory id ──
    # A memory absent from a list gets no credit from that channel (not penalised).
    all_hit_ids = set(rank_dense) | set(rank_sparse)
    relevance: Dict[str, float] = {}
    for mid in all_hit_ids:
        rrf = 0.0
        if mid in rank_dense:
            rrf += _rrf_score(rank_dense[mid])
        if mid in rank_sparse:
            rrf += _rrf_score(rank_sparse[mid])
        # Normalise roughly to [0,1]: max possible RRF (rank 0 in both) = 2/(RRF_K+1) ≈ 0.032
        # Scale so that a hit in both channels at rank 0 → ~1.0
        relevance[mid] = min(1.0, rrf * (RRF_K + 1))

    # ── Candidate fetch ──
    stmt = select(Memory).where(*where)
    has_query = bool(query.strip() or embedding)
    if has_query:
        if not relevance:
            stmt = stmt.where(Memory.pinned.is_(True))
        else:
            stmt = stmt.where(or_(Memory.id.in_(list(relevance)), Memory.pinned.is_(True)))
    candidates = (await db.execute(
        stmt.order_by(Memory.importance.desc(), Memory.updated_at.desc()).limit(max(pool, 60))
    )).scalars().all()

    # ── Score = pinned boost + RRF relevance + importance + adaptive recency ──
    now = datetime.now(timezone.utc)
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

    Pillar 3 — Adaptive Decay: only `observation` kind memories are eligible for retirement.
    Lessons and facts represent authoritative knowledge and are NEVER pruned here, regardless of
    age or use-count. The pruning query's `kind == "observation"` filter enforces this.
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


async def promote_memories_for_document(
    db: AsyncSession,
    *,
    template_id: str,
    document_id: str,
    promoted_by: str,
) -> List[Memory]:
    """
    Pillar 1: Promotes any unverified memories associated with document_id to authoritative workspace lessons
    once a human reviewer approves or corrects the document extraction or answers a related question.
    """
    all_memories = (await db.execute(
        select(Memory).where(
            Memory.template_id == template_id,
            Memory.archived.is_(False),
        )
    )).scalars().all()

    promoted: List[Memory] = []
    now = datetime.now(timezone.utc)
    for m in all_memories:
        docs = list((m.source or {}).get("document_ids") or []) + list((m.data or {}).get("document_ids") or [])
        if document_id in docs:
            is_unverified = (m.data or {}).get("status") == "unverified" or (m.data or {}).get("downgraded_from") == "lesson"
            if is_unverified:
                m.kind = "lesson"
                data = dict(m.data or {})
                data["status"] = "verified"
                data["promoted_by"] = promoted_by
                data["promoted_at"] = now.isoformat()
                m.data = data
                m.importance = min(1.0, m.importance + 0.20)
                m.updated_at = now
                promoted.append(m)
    await db.flush()
    return promoted
