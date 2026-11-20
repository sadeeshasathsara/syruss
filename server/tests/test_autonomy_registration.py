import copy
import json
import os
import sys
from pathlib import Path

import httpx
import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
os.environ.setdefault("AGENT_MEMORY_DB", "agent_memory_test")
os.environ["EMBEDDINGS_ENABLED"] = "false"

import agentic_framework.worker as worker_module
from agentic_framework.agent.loop import WorkspaceAgent
from agentic_framework.core_api import CoreAPI
from agentic_framework.events import AgentEvent
from agentic_framework.memory import MemoryStore
from agentic_framework.tests.conftest import ScriptedModel, call
from app.core.database import AsyncSessionLocal
from app.main import app
from app.models.knowledge import Entity, EntityAlias
from app.services.agent_events import build_envelope
from tests.conftest import SCHEMA, VALID_RECORD, agent, create_template, outbox_events, upload, ws

API = "/api/v1"
IDENTIFIED = copy.deepcopy(SCHEMA)
IDENTIFIED["objects"][1]["identity"] = {"id_field": "employee_id", "label_field": "name"}


@pytest.fixture
async def memory():
    store = MemoryStore()
    await store.init()
    yield store
    await store.close()


async def run_agent(memory, model, row) -> str:
    def core(token: str) -> CoreAPI:
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test/internal/agent/v1",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        return CoreAPI(token, client=client)

    original = worker_module.CoreAPI
    worker_module.CoreAPI = core
    try:
        return await worker_module.AgentWorker(WorkspaceAgent(model), memory, producer=None).handle(
            AgentEvent(**build_envelope(row))
        )
    finally:
        worker_module.CoreAPI = original


def record(employee_id: str, name: str, day: str) -> dict:
    data = copy.deepcopy(VALID_RECORD)
    data["Roster"][0]["date"] = day
    data["Guard"][0].update(employee_id=employee_id, name=name)
    return data


async def test_may_register_entities_on_commits_and_auto_saves_entities(client, memory):
    """
    When 'Register new people and things' (may_register_entities) is True (default):
    The agent commits records naming new entities directly without asking, and the system
    automatically registers the new entities in the Entity table.
    """
    tpl = await create_template(client, schema=IDENTIFIED)
    doc = await upload(client, tpl)
    await client.post(f"{API}/documents/{doc}/process", headers=ws(tpl))
    [event] = await outbox_events(tpl, "document.submitted")

    commit = call("submit_record", {
        "document_id": doc,
        "data_json": json.dumps(record("99001", "Amila Bandara", "2026-07-21")),
        "decision": "COMMIT",
        "confidence": 0.95,
        "unrecorded_content": "none",
        "evidence_json": '[{"object": "Guard", "index": 0, "field": "name", "value": "Amila Bandara", "raw_text": "Amila Bandara"}]',
    })
    model = ScriptedModel(script=[
        AIMessage(content="", tool_calls=[commit]),
        AIMessage(content="Committed and registered Amila Bandara."),
    ])

    assert await run_agent(memory, model, event) == "COMPLETED"
    doc_res = await client.get(f"{API}/documents/{doc}", headers=ws(tpl))
    assert doc_res.json()["status"] == "COMPLETED"

    # Verify the entity was automatically registered in DB
    async with AsyncSessionLocal() as s:
        entity = (await s.execute(
            select(Entity).where(Entity.template_id == tpl, Entity.identifier_value == "99001")
        )).scalar_one_or_none()
        assert entity is not None
        assert entity.name == "Amila Bandara"
        assert entity.entity_type == "Guard"
        assert entity.created_by == "agent"
        assert entity.source_document_id == doc


async def test_shared_surname_new_employee_does_not_block_commit_when_may_register(client, memory):
    """
    When may_register_entities is True, a new employee sharing a surname with an existing
    employee (different employee ID) is not blocked as an identity collision.
    """
    tpl = await create_template(client, schema=IDENTIFIED)

    # First document registers employee 10179 named "D.M. Dissanayaka"
    doc1 = await upload(client, tpl)
    res1 = await client.post(f"/internal/agent/v1/documents/{doc1}/record", headers=agent(tpl), json={
        "data": record("10179", "D.M. Dissanayaka", "2026-07-20"),
        "decision": "COMMIT",
        "confidence": 0.95,
        "evidence": [{"object": "Guard", "index": 0, "field": "name", "value": "D.M. Dissanayaka", "raw_text": "D.M. Dissanayaka"}],
    })
    assert res1.status_code == 200
    assert res1.json()["status"] == "COMPLETED"

    # Second document has new employee 76150 also named "K.G. Dissanayaka" (sharing surname Dissanayaka)
    doc2 = await upload(client, tpl)
    res2 = await client.post(f"/internal/agent/v1/documents/{doc2}/record", headers=agent(tpl), json={
        "data": record("76150", "K.G. Dissanayaka", "2026-07-21"),
        "decision": "COMMIT",
        "confidence": 0.95,
        "evidence": [{"object": "Guard", "index": 0, "field": "name", "value": "K.G. Dissanayaka", "raw_text": "K.G. Dissanayaka"}],
    })
    assert res2.status_code == 200
    assert res2.json()["status"] == "COMPLETED"

    # Verify both entities exist
    async with AsyncSessionLocal() as s:
        entities = (await s.execute(
            select(Entity).where(Entity.template_id == tpl).order_by(Entity.identifier_value)
        )).scalars().all()
        ids = {e.identifier_value for e in entities}
        assert "10179" in ids
        assert "76150" in ids


async def test_may_learn_aliases_auto_registers_alias_on_commit(client, memory):
    """
    When may_learn_aliases is True, committing a record with a new name variation for an
    existing entity automatically registers the variation as an EntityAlias.
    """
    tpl = await create_template(client, schema=IDENTIFIED)

    # First document registers employee 55555 named "K.G. Silva"
    doc1 = await upload(client, tpl)
    res1 = await client.post(f"/internal/agent/v1/documents/{doc1}/record", headers=agent(tpl), json={
        "data": record("55555", "K.G. Silva", "2026-07-20"),
        "decision": "COMMIT",
        "confidence": 0.95,
        "evidence": [{"object": "Guard", "index": 0, "field": "name", "value": "K.G. Silva", "raw_text": "K.G. Silva"}],
    })
    assert res1.status_code == 200

    # Second document has same employee 55555 written as "Silva K.G."
    doc2 = await upload(client, tpl)
    res2 = await client.post(f"/internal/agent/v1/documents/{doc2}/record", headers=agent(tpl), json={
        "data": record("55555", "Silva K.G.", "2026-07-21"),
        "decision": "COMMIT",
        "confidence": 0.95,
        "evidence": [{"object": "Guard", "index": 0, "field": "name", "value": "Silva K.G.", "raw_text": "Silva K.G."}],
    })
    assert res2.status_code == 200

    async with AsyncSessionLocal() as s:
        entity = (await s.execute(
            select(Entity).where(Entity.template_id == tpl, Entity.identifier_value == "55555")
        )).scalar_one_or_none()
        assert entity is not None
        aliases = [a.alias for a in entity.aliases]
        assert "Silva K.G." in aliases
