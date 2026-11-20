"""
Phase 4 — Single Store Unification

Tests verify:
  1. NOTE_KINDS no longer includes lesson or observation.
  2. MemoryStore.add() rejects lesson (redirected to core server).
  3. MemoryStore.add() rejects observation (redirected to core server).
  4. MemoryStore.list("lesson") returns [] (Pillar 4 graceful degradation).
  5. MemoryStore.list("observation") returns [] (Pillar 4 graceful degradation).
  6. MemoryStore.search() returns [] for lesson/observation kind queries.
  7. _move_older_notes() is a no-op when list() returns empty (new workspaces).
  8. MemoryStore.add("work_item") still works.
  9. MemoryStore.list("work_item") still returns real rows (integration, needs DB).
 10. work_items_for_question() still works (integration, needs DB).
 11. work_items_for_document() still works (integration, needs DB).
"""
import pytest
from unittest.mock import AsyncMock, MagicMock


# ---------------------------------------------------------------------------
# 1. NOTE_KINDS constant
# ---------------------------------------------------------------------------

from agentic_framework.memory import NOTE_KINDS, _LEGACY_NOTE_KINDS


class TestNoteKindsConstant:
    def test_work_item_in_note_kinds(self):
        assert "work_item" in NOTE_KINDS

    def test_lesson_not_in_note_kinds(self):
        """lesson now lives in core /memories store."""
        assert "lesson" not in NOTE_KINDS

    def test_observation_not_in_note_kinds(self):
        """observation now lives in core /memories store."""
        assert "observation" not in NOTE_KINDS

    def test_legacy_symbol_still_exported(self):
        """_LEGACY_NOTE_KINDS is kept for external code that imports it."""
        assert "lesson" in _LEGACY_NOTE_KINDS
        assert "observation" in _LEGACY_NOTE_KINDS
        assert "work_item" in _LEGACY_NOTE_KINDS


# ---------------------------------------------------------------------------
# 2-6. MemoryStore method contract — tested without a real DB via mocks
# ---------------------------------------------------------------------------

class TestMemoryStoreContract:
    """
    These tests exercise the MemoryStore's public contract without a real Postgres DB.
    The add() and list() pure-logic paths (ValueError, early return) don't touch the DB.
    """

    def _make_store(self):
        """MemoryStore with its DB internals stubbed out."""
        from agentic_framework.memory import MemoryStore
        store = MemoryStore.__new__(MemoryStore)
        # _session is a callable that returns a context manager
        store._session = MagicMock()
        return store

    @pytest.mark.asyncio
    async def test_add_lesson_rejected(self):
        """lesson must raise ValueError with a helpful message."""
        store = self._make_store()
        with pytest.raises(ValueError, match="core /memories"):
            await store.add("t1", "tpl1", "lesson", "Ranks are SG, LSO, JSO")

    @pytest.mark.asyncio
    async def test_add_observation_rejected(self):
        """observation must raise ValueError with a helpful message."""
        store = self._make_store()
        with pytest.raises(ValueError, match="core /memories"):
            await store.add("t1", "tpl1", "observation", "Some pages have a code column")

    @pytest.mark.asyncio
    async def test_list_lesson_returns_empty_without_db(self):
        """list('lesson') must return [] immediately — no DB session created."""
        store = self._make_store()
        result = await store.list("t1", "tpl1", "lesson")
        assert result == []
        # No DB call should have been made
        store._session.assert_not_called()

    @pytest.mark.asyncio
    async def test_list_observation_returns_empty_without_db(self):
        """list('observation') must return [] immediately."""
        store = self._make_store()
        result = await store.list("t1", "tpl1", "observation")
        assert result == []
        store._session.assert_not_called()

    @pytest.mark.asyncio
    async def test_search_lesson_kind_returns_empty_without_db(self):
        """search(kind='lesson') must return [] immediately."""
        store = self._make_store()
        result = await store.search("t1", "tpl1", "some query", kind="lesson")
        assert result == []
        store._session.assert_not_called()

    @pytest.mark.asyncio
    async def test_search_observation_kind_returns_empty_without_db(self):
        """search(kind='observation') must return [] immediately."""
        store = self._make_store()
        result = await store.search("t1", "tpl1", "some query", kind="observation")
        assert result == []
        store._session.assert_not_called()


# ---------------------------------------------------------------------------
# 7. _move_older_notes is a no-op for new workspaces
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_move_older_notes_is_noop_for_new_workspaces():
    """
    After Phase 4, MemoryStore.list(lesson/observation) returns [].
    _move_older_notes must therefore never call ctx.api.post(/memories/import).
    """
    from agentic_framework.agent.memory_upkeep import _move_older_notes

    ctx = MagicMock()
    ctx.tenant_id = "t1"
    ctx.template_id = "tpl1"
    ctx.api = AsyncMock()
    # list() always returns [] (new workspace — lesson/observation redirect to core)
    ctx.memory.list = AsyncMock(return_value=[])

    await _move_older_notes(ctx)

    # import must NOT have been called
    ctx.api.post.assert_not_awaited()


# ---------------------------------------------------------------------------
# 8-11. Integration tests with the real MemoryStore (uses conftest.memory fixture)
#       These run against the test Postgres DB and verify work_item still works.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_add_work_item_accepted(memory):
    """work_item is the only local kind — it must still be accepted."""
    result = await memory.add("t1", "tpl_p4", "work_item", "Park: check vendor code",
                               data={"question_id": "q_p4a", "document_ids": ["doc_p4a"]})
    assert result["kind"] == "work_item"
    assert result["id"].startswith("n_")


@pytest.mark.asyncio
async def test_list_work_item_returns_rows(memory):
    """work_item listing must still return real rows from the local DB."""
    await memory.add("t1", "tpl_p4b", "work_item", "Park: waiting for OT definition",
                     data={"question_id": "q_p4b", "document_ids": ["doc_p4b"]})
    items = await memory.list("t1", "tpl_p4b", "work_item")
    assert any(i["data"].get("question_id") == "q_p4b" for i in items)


@pytest.mark.asyncio
async def test_work_items_for_question_still_works(memory):
    """work_items_for_question must still resolve correctly through the local store."""
    await memory.add("t1", "tpl_p4c", "work_item", "Park: OT definition pending",
                     data={"question_id": "q_p4c", "document_ids": ["d_p4c"]})
    found = await memory.work_items_for_question("t1", "tpl_p4c", "q_p4c")
    assert len(found) >= 1
    assert found[0]["data"]["question_id"] == "q_p4c"


@pytest.mark.asyncio
async def test_work_items_for_document_still_works(memory):
    """work_items_for_document must still resolve correctly."""
    await memory.add("t1", "tpl_p4d", "work_item", "Park: doc x",
                     data={"question_id": "q_p4d", "document_ids": ["doc_p4d"]})
    found = await memory.work_items_for_document("t1", "tpl_p4d", "doc_p4d")
    assert any(i["data"].get("question_id") == "q_p4d" for i in found)
