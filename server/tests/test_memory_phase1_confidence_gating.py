"""
Phase 1 Verification: Confidence Gating & Hallucination Guard (Lesson Truth Verification).
Tests that:
1. Low-confidence or vetoed memories cannot poison workspace lessons (downgraded to unverified observations).
2. High-confidence (>= 0.90, zero vetoes) lessons are verified.
3. Briefing search with kinds=["lesson"] excludes unverified memories.
4. Human review decisions (APPROVE / CORRECT) promote unverified observations to verified lessons with boosted importance.
5. Answering a related agent question promotes unverified observations.
"""
import pytest
from app.models.memory import Memory
from app.models.review import ReviewTask, ReviewStatus
from app.schemas.review import ReviewDecisionRequest
from app.services.review_service import ReviewService
from app.core.scope import TemplateScope
from app.models.template import DocumentTemplate
from tests.conftest import TENANT, agent, create_template, ws, upload, AsyncSessionLocal

API = "/api/v1"
AGENT = "/internal/agent/v1"


@pytest.mark.asyncio
async def test_confidence_gating_downgrades_unverified_lessons(client):
    tpl = await create_template(client)

    # 1. Low confidence lesson (< 0.90) -> downgraded to observation
    res_low = await client.post(
        f"{AGENT}/memories",
        headers=agent(tpl),
        json={
            "scope": "workspace",
            "kind": "lesson",
            "content": "Low confidence invoice rule: discount applies on weekends",
            "data": {"source_run_confidence": 0.65, "status": "unverified"},
        },
    )
    assert res_low.status_code == 201, res_low.text
    mem_low = res_low.json()
    assert mem_low["kind"] == "observation"
    assert mem_low["data"]["status"] == "unverified"
    assert mem_low["data"]["downgraded_from"] == "lesson"

    # 2. Vetoed lesson -> downgraded to observation
    res_veto = await client.post(
        f"{AGENT}/memories",
        headers=agent(tpl),
        json={
            "scope": "workspace",
            "kind": "lesson",
            "content": "Vetoed invoice rule: totals do not match items",
            "data": {"source_run_confidence": 0.95, "veto_reasons": ["MATH_DISCREPANCY"]},
        },
    )
    assert res_veto.status_code == 201
    mem_veto = res_veto.json()
    assert mem_veto["kind"] == "observation"
    assert mem_veto["data"]["status"] == "unverified"

    # 3. High confidence lesson (>= 0.90, no vetoes) -> accepted as verified lesson
    res_high = await client.post(
        f"{AGENT}/memories",
        headers=agent(tpl),
        json={
            "scope": "workspace",
            "kind": "lesson",
            "content": "Authoritative rule: Tax ID starts with DE followed by 9 digits",
            "data": {"source_run_confidence": 0.96},
        },
    )
    assert res_high.status_code == 201
    mem_high = res_high.json()
    assert mem_high["kind"] == "lesson"
    assert mem_high["data"]["status"] == "verified"

    # 4. Briefing search isolation: searching for lessons returns ONLY the verified lesson
    search_res = await client.post(
        f"{AGENT}/memories/search",
        headers=agent(tpl),
        json={"kinds": ["lesson"], "limit": 10},
    )
    assert search_res.status_code == 200
    found = search_res.json()
    found_ids = [m["id"] for m in found]
    assert mem_high["id"] in found_ids
    assert mem_low["id"] not in found_ids
    assert mem_veto["id"] not in found_ids


@pytest.mark.asyncio
async def test_human_review_promotes_unverified_lesson(client):
    tpl = await create_template(client)
    doc_id = await upload(client, tpl, "invoice.png")

    # Agent saves an unverified lesson linked to doc_id
    res = await client.post(
        f"{AGENT}/memories",
        headers=agent(tpl),
        json={
            "scope": "workspace",
            "kind": "lesson",
            "content": "Vendor Apex Corp always uses Net 30 payment terms",
            "importance": 0.5,
            "document_ids": [doc_id],
            "data": {"source_run_confidence": 0.60, "status": "unverified", "document_ids": [doc_id]},
        },
    )
    assert res.status_code == 201
    mem = res.json()
    assert mem["kind"] == "observation"
    assert mem["data"]["status"] == "unverified"

    # Simulate human review task creation and approval
    async with AsyncSessionLocal() as session:
        tmpl = await session.get(DocumentTemplate, tpl)
        scope = TemplateScope(tenant_id=TENANT, template_id=tpl, user_id="usr_reviewer", username="Alice Reviewer", template=tmpl)
        task = ReviewTask(
            tenant_id=TENANT,
            template_id=tpl,
            document_id=doc_id,
            reason="Uncertain payment terms",
            original_data={},
            status=ReviewStatus.PENDING,
        )
        session.add(task)
        await session.commit()
        task_id = task.id

        # Human approves review
        decision = ReviewDecisionRequest(action="APPROVE", notes="Verified terms on document")
        await ReviewService.decide(session, scope, task_id, decision)
        await session.commit()

        # Check that memory was promoted to lesson with status verified and boosted importance
        promoted_mem = await session.get(Memory, mem["id"])
        assert promoted_mem.kind == "lesson"
        assert promoted_mem.data["status"] == "verified"
        assert promoted_mem.data["promoted_by"] == "Alice Reviewer"
        assert promoted_mem.importance >= 0.70  # boosted by +0.20


@pytest.mark.asyncio
async def test_human_question_answer_promotes_unverified_lesson(client):
    tpl = await create_template(client)
    doc_id = await upload(client, tpl, "receipt.png")

    # Agent saves unverified lesson tied to doc_id
    res = await client.post(
        f"{AGENT}/memories",
        headers=agent(tpl),
        json={
            "scope": "workspace",
            "kind": "lesson",
            "content": "Meals above 50 EUR require attendee list",
            "importance": 0.4,
            "document_ids": [doc_id],
            "data": {"source_run_confidence": 0.55, "status": "unverified"},
        },
    )
    assert res.status_code == 201
    mem_id = res.json()["id"]

    # Ask a question related to doc_id via agent endpoint
    q_res = await client.post(
        f"{AGENT}/questions",
        headers=agent(tpl),
        json={
            "question": "Is attendee list required for this meal?",
            "context": "Receipt total is 65 EUR",
            "options": ["Yes", "No"],
            "answer_type": "single",
            "related_document_id": doc_id,
        },
    )
    assert q_res.status_code == 201, q_res.text
    q_id = q_res.json()["id"]

    # Human answers the question via public API
    ans_res = await client.post(
        f"{API}/questions/{q_id}/answer",
        headers=ws(tpl),
        json={"answer": "Yes", "choices": ["Yes"]},
    )
    assert ans_res.status_code == 200

    # Verify memory was promoted
    async with AsyncSessionLocal() as session:
        promoted_mem = await session.get(Memory, mem_id)
        assert promoted_mem.kind == "lesson"
        assert promoted_mem.data["status"] == "verified"
        assert promoted_mem.importance >= 0.60
