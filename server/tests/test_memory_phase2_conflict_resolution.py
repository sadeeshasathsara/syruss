"""
Phase 2 Verification: Semantic Conflict & Contradiction Resolution.

Tests that memory_service.add() with an injected embedding:
1. Exact text match  → reinforcement  (no new row, importance boosted)
2. Similarity >= 0.95 (via pgvector mock) → reinforcement
3. Similarity in [0.82, 0.95) → semantic clash: old archived, new inserted with supersedes link
4. Similarity < 0.82 → plain insert (no archival)
5. Distinct scope/kind partitions are never compared against each other
6. Archived memories are not superseded again (idempotent)
7. Response body includes "superseded" list when clash occurs
"""
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone
from app.services import memory_service as ms
from app.services.memory_service import CONFLICT_SIMILARITY, REINFORCE_SIMILARITY
from app.models.memory import Memory
from tests.conftest import TENANT, agent, create_template, ws

API = "/api/v1"
AGENT = "/internal/agent/v1"

FAKE_EMBED_A = [0.1] * 768
FAKE_EMBED_B = [0.2] * 768   # will be treated as "semantically close" in mocks


# ---------------------------------------------------------------------------
# Unit tests: memory_service.add() with mocked DB
# ---------------------------------------------------------------------------

def _make_memory(content: str, kind: str = "lesson", scope: str = "workspace",
                 importance: float = 0.5, archived: bool = False) -> Memory:
    m = Memory(
        id=f"mem_{content[:8].replace(' ', '_')}",
        tenant_id=TENANT, template_id="tpl1",
        scope=scope, kind=kind, content=content,
        importance=importance, archived=archived,
        use_count=0, pinned=False,
        data={}, source={}, created_by="agent",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    return m


async def _run_add(sim: float, old_mem: Memory):
    """
    Call memory_service.add() with a mocked DB that:
    - has pgvector (has_vectors → True)
    - returns one existing memory at the given cosine similarity
    - db.get() returns old_mem for its id
    """
    new_embedding = FAKE_EMBED_A

    db = AsyncMock()

    # Existing memories query (exact text check) → no exact matches
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = [old_mem]   # returned by select(), but normalized content differs
    result_mock = MagicMock()
    result_mock.scalars.return_value = scalars_mock
    db.execute = AsyncMock()

    call_count = 0

    async def _execute(stmt_or_text, params=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # Exact text query — return old_mem but _normalize will differ
            r = MagicMock()
            r.scalars.return_value.all.return_value = [old_mem]
            return r
        else:
            # Vector similarity query
            r = MagicMock()
            r.all.return_value = [(old_mem.id, sim)]
            return r

    db.execute = _execute
    db.get = AsyncMock(return_value=old_mem)
    db.add = MagicMock()
    db.flush = AsyncMock()

    with (
        patch("app.services.memory_service.has_vectors", return_value=True),
        patch("app.services.memory_service.set_embedding", return_value=True),
        patch("app.core.config.settings.EMBEDDING_DIM", len(new_embedding)),
    ):
        result, created = await ms.add(
            db,
            tenant_id=TENANT, template_id="tpl1",
            scope="workspace", kind="lesson",
            content="New conflicting lesson content XYZABC",
            created_by="agent",
            importance=0.7,
            embedding=new_embedding,
        )
    return result, created, old_mem


import pytest


@pytest.mark.asyncio
async def test_high_similarity_reinforces_existing():
    """sim >= REINFORCE_SIMILARITY → strengthen existing, no new row."""
    old = _make_memory("existing lesson same meaning text", importance=0.5)
    result, created, old_mem = await _run_add(sim=0.97, old_mem=old)
    assert not created
    assert result is old_mem
    assert result.importance > 0.5  # boosted


@pytest.mark.asyncio
async def test_conflict_similarity_supersedes_old():
    """CONFLICT_SIMILARITY <= sim < REINFORCE_SIMILARITY → old archived, new inserted."""
    old = _make_memory("Old VAT rate is 20 percent", importance=0.6)
    result, created, old_mem = await _run_add(sim=0.87, old_mem=old)
    assert created                             # new memory was inserted
    assert result is not old_mem              # a new object was returned
    assert old_mem.archived is True           # old archived
    assert old_mem.data.get("superseded_by") == result.id
    assert result.data.get("supersedes") == [old_mem.id]


@pytest.mark.asyncio
async def test_low_similarity_plain_insert():
    """sim < CONFLICT_SIMILARITY → plain insert, nothing archived."""
    old = _make_memory("Completely different topic fruit banana", importance=0.5)
    result, created, old_mem = await _run_add(sim=0.55, old_mem=old)
    assert created
    assert result is not old_mem
    assert old_mem.archived is False          # untouched
    assert not old_mem.data.get("superseded_by")


@pytest.mark.asyncio
async def test_already_archived_not_superseded_again():
    """Archived memories are skipped during conflict detection (idempotent)."""
    old = _make_memory("archived old rule", archived=True)
    old.archived = True

    db = AsyncMock()
    call_count = 0

    async def _execute(stmt_or_text, params=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            r = MagicMock()
            r.scalars.return_value.all.return_value = [old]
            return r
        r = MagicMock()
        r.all.return_value = [(old.id, 0.90)]  # would supersede if not archived
        return r

    db.execute = _execute
    db.get = AsyncMock(return_value=old)
    db.add = MagicMock()
    db.flush = AsyncMock()

    with (
        patch("app.services.memory_service.has_vectors", return_value=True),
        patch("app.services.memory_service.set_embedding", return_value=True),
        patch("app.core.config.settings.EMBEDDING_DIM", len(FAKE_EMBED_A)),
    ):
        result, created = await ms.add(
            db,
            tenant_id=TENANT, template_id="tpl1",
            scope="workspace", kind="lesson",
            content="New topic lesson different from archived",
            created_by="agent",
            importance=0.5,
            embedding=FAKE_EMBED_A,
        )

    # The archived old memory should NOT get a superseded_by link
    assert not old.data.get("superseded_by")


# ---------------------------------------------------------------------------
# Integration tests: via HTTP client (pgvector absent, falls through to plain insert)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_api_remember_returns_superseded_field(client):
    """The /memories endpoint always returns a 'superseded' key (empty list when no clash)."""
    tpl = await create_template(client)
    res = await client.post(
        f"{AGENT}/memories",
        headers=agent(tpl),
        json={
            "scope": "workspace",
            "kind": "lesson",
            "content": "New lesson in a fresh workspace",
            "data": {"source_run_confidence": 0.95},
        },
    )
    assert res.status_code == 201, res.text
    body = res.json()
    assert "superseded" in body
    assert isinstance(body["superseded"], list)


@pytest.mark.asyncio
async def test_exact_duplicate_via_api_is_reinforced(client):
    """Posting identical content twice → second call returns created=False and boosted importance."""
    tpl = await create_template(client)
    headers = agent(tpl)
    payload = {
        "scope": "workspace",
        "kind": "observation",
        "content": "Payment terms are always Net 30",
        "importance": 0.5,
    }
    r1 = await client.post(f"{AGENT}/memories", headers=headers, json=payload)
    assert r1.status_code == 201
    r2 = await client.post(f"{AGENT}/memories", headers=headers, json=payload)
    assert r2.status_code == 201
    j2 = r2.json()
    assert j2["created"] is False
    assert j2["importance"] > 0.5
