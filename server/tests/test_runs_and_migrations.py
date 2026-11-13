"""
The run log (origins, cost, who may see what) and the database migrations.
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone
import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app.core.database import Base, engine
from app.core.migrations import alembic_config, include_object
from tests.conftest import agent, create_template, join, outbox_events, person, ws

API = "/api/v1"
AGENT = "/internal/agent/v1"
OTHER = person("usr_runs_other", "Kasun")


async def report(client, tpl: str, origin: dict, **extra) -> None:
    start = datetime.now(timezone.utc)
    res = await client.post(f"{AGENT}/traces", headers=agent(tpl), json={
        "trigger": extra.pop("trigger", "human.message"), "model": "test-model", "status": "COMPLETED",
        "started_at": start.isoformat(), "completed_at": (start + timedelta(seconds=2)).isoformat(),
        "steps": [{"thought": "x"}], "usage": {"input_tokens": 1200, "output_tokens": 300},
        "run_id": f"run_{uuid.uuid4().hex[:10]}", "origin": origin, "model_calls": 2, "cost_usd": 0.0081, **extra,
    })
    assert res.status_code == 201, res.text


async def test_events_carry_their_origin_and_runs_are_logged(client):
    tpl = await create_template(client)
    thread = (await client.post(f"{API}/conversations", headers=ws(tpl), json={"title": "Hello"})).json()
    await client.post(f"{API}/conversations/{thread['id']}/messages?wait=false", headers=ws(tpl), json={"content": "hi"})
    [event] = await outbox_events(tpl, "human.message")
    origin = event.actor
    assert (origin["origin"], origin["source"], origin["conversation_id"]) == ("user", "chat", thread["id"])
    assert event.payload["sender"]["role"] == "Owner"

    await report(client, tpl, origin)
    await report(client, tpl, {"type": "system", "id": "system", "origin": "system", "source": "system"}, trigger="schedule.reflect")
    runs = (await client.get(f"{API}/runs", headers=ws(tpl))).json()
    assert runs["scope"] == "all" and [r["origin"] for r in runs["items"]] == ["system", "user"]
    chat_run = runs["items"][1]
    assert (chat_run["source"], chat_run["input_tokens"], chat_run["model_calls"], chat_run["cost_usd"]) == ("chat", 1200, 2, 0.0081)
    assert (await client.get(f"{API}/runs?origin=system", headers=ws(tpl))).json()["items"][0]["trigger"] == "schedule.reflect"
    detail = (await client.get(f"{API}/runs/{chat_run['id']}", headers=ws(tpl))).json()
    assert detail["steps"] == [{"thought": "x"}]

    usage = (await client.get(f"{API}/settings/billing", headers=ws(tpl))).json()["usage"]
    assert usage["agent_input_tokens_this_month"] == 2400 and round(usage["agent_cost_usd_this_month"], 4) == 0.0162


async def test_members_see_only_their_own_runs(client):
    tpl = await create_template(client)
    await client.get(f"{API}/settings", headers=ws(tpl))  # creator becomes owner
    await join(client, tpl, OTHER, "reviewer")

    await report(client, tpl, {"type": "user", "id": "usr_runs_other", "origin": "user", "source": "chat"})
    await report(client, tpl, {"type": "system", "id": "system", "origin": "system", "source": "system"})
    theirs = (await client.get(f"{API}/runs", headers={**ws(tpl), **OTHER})).json()
    assert theirs["scope"] == "mine" and len(theirs["items"]) == 1
    hidden = (await client.get(f"{API}/runs", headers=ws(tpl))).json()["items"][0]
    assert hidden["origin"] == "system"
    assert (await client.get(f"{API}/runs/{hidden['id']}", headers={**ws(tpl), **OTHER})).status_code == 404
    assert (await client.get(f"{API}/organization/runs", headers=OTHER)).status_code == 403
    assert len((await client.get(f"{API}/organization/runs", params={"workspace_id": tpl})).json()["items"]) == 2


async def test_migrations_build_the_same_schema_as_the_models():
    """A fresh database upgraded with the migrations matches the models exactly."""
    name = f"{engine.url.database}_migrations"
    admin = create_async_engine(engine.url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = engine.url.set(database=name).render_as_string(hide_password=False)
    try:
        await asyncio.to_thread(command.upgrade, alembic_config(url), "head")
        fresh = create_async_engine(url)
        async with fresh.connect() as conn:
            diff = await conn.run_sync(lambda c: compare_metadata(
                MigrationContext.configure(c, opts={"include_object": include_object}), Base.metadata))
            # The platform audit log only grows: the database itself refuses to change or remove an entry.
            await conn.execute(text("INSERT INTO platform_audit_log (id, actor_id, actor_name, action, target_type, target_id, details, created_at) "
                                    "VALUES ('a1', 'u', 'U', 'staff.added', 'user', 'x', '{}', now())"))
            for change in ("UPDATE platform_audit_log SET reason = 'edited'", "DELETE FROM platform_audit_log"):
                with pytest.raises(Exception, match="append-only"):
                    async with conn.begin_nested():
                        await conn.execute(text(change))
            # So are the pages an organization was charged.
            await conn.execute(text("INSERT INTO usage_events (id, tenant_id, metric, quantity, from_credits, source_type, source_id, "
                                    "idempotency_key, period_start, occurred_at) VALUES ('u1', 't', 'pages', 1, 0, 'document', 'd', 'k', now(), now())"))
            await conn.execute(text("INSERT INTO credit_ledger (id, tenant_id, delta, kind, created_by, created_at) "
                                    "VALUES ('c1', 't', 100, 'grant', 'tests', now())"))
            for change in ("UPDATE usage_events SET quantity = 0", "DELETE FROM usage_events",
                           "UPDATE credit_ledger SET delta = 1000", "DELETE FROM credit_ledger"):
                with pytest.raises(Exception, match="append-only"):
                    async with conn.begin_nested():
                        await conn.execute(text(change))
            # A draft invoice can change; once issued, what it says is fixed, and payments against it only grow.
            await conn.execute(text("INSERT INTO invoices (id, tenant_id, kind, status, currency, subtotal_cents, tax_cents, total_cents, "
                                    "amount_paid_cents, amount_credited_cents, amount_refunded_cents, reminders, seller, buyer, bank, tax_lines, "
                                    "created_by, created_at, updated_at) VALUES ('i1', 't', 'purchase', 'draft', 'USD', 100, 0, 100, 0, 0, 0, '[]', "
                                    "'{}', '{}', '[]', '[]', 'tests', now(), now())"))
            line = ("INSERT INTO invoice_lines (id, invoice_id, position, kind, description, quantity, unit_cents, amount_cents, tax_rate_bp) "
                    "VALUES ('{}', 'i1', 0, 'custom', 'Setup', 1, 200, 200, 0)")
            await conn.execute(text(line.format("l1")))
            await conn.execute(text("UPDATE invoices SET subtotal_cents = 200, total_cents = 200 WHERE id = 'i1'"))
            await conn.execute(text("UPDATE invoices SET status = 'open', number = 'T-1', issued_at = now() WHERE id = 'i1'"))
            await conn.execute(text("INSERT INTO billing_payments (id, tenant_id, invoice_id, method, amount_cents, currency, quantity, "
                                    "recorded_by, recorded_by_name, created_at) VALUES ('p1', 't', 'i1', 'staff_recorded', 50, 'USD', 1, 'u', 'U', now())"))
            await conn.execute(text("UPDATE invoices SET amount_paid_cents = 50 WHERE id = 'i1'"))  # what has been paid is kept up to date
            for change in ("UPDATE invoices SET total_cents = 1 WHERE id = 'i1'", "UPDATE invoices SET buyer = '{\"legal_name\": \"Other\"}' WHERE id = 'i1'",
                           "UPDATE invoices SET status = 'draft' WHERE id = 'i1'", "DELETE FROM invoices WHERE id = 'i1'",
                           "UPDATE invoice_lines SET amount_cents = 1", "DELETE FROM invoice_lines", line.format("l2")):
                with pytest.raises(Exception, match="fixed"):
                    async with conn.begin_nested():
                        await conn.execute(text(change))
            for change in ("UPDATE billing_payments SET amount_cents = 1", "DELETE FROM billing_payments"):
                with pytest.raises(Exception, match="append-only"):
                    async with conn.begin_nested():
                        await conn.execute(text(change))
            # Proof of payment is kept as it was sent: only its storage key is filled in, once.
            await conn.execute(text("INSERT INTO payment_files (id, tenant_id, payment_id, object_key, filename, content_type, size, sha256, "
                                    "uploaded_by, created_at) VALUES ('f1', 't', 'p1', '', 'slip.pdf', 'application/pdf', 10, 'abc', 'u', now())"))
            await conn.execute(text("UPDATE payment_files SET object_key = 'billing/t/proofs/f1.pdf' WHERE id = 'f1'"))
            for change in ("UPDATE payment_files SET filename = 'other.pdf'", "DELETE FROM payment_files"):
                with pytest.raises(Exception, match="append-only"):
                    async with conn.begin_nested():
                        await conn.execute(text(change))
            # So are credit notes (their PDF is filled in once) and refunds.
            await conn.execute(text("INSERT INTO credit_notes (id, number, invoice_id, tenant_id, currency, amount_cents, reason, created_by, "
                                    "created_by_name, created_at) VALUES ('n1', 'T-CN-1', 'i1', 't', 'USD', 20, 'Too much', 'u', 'U', now())"))
            await conn.execute(text("UPDATE credit_notes SET pdf_key = 'billing/t/credit-notes/n1.pdf' WHERE id = 'n1'"))
            await conn.execute(text("INSERT INTO billing_refunds (id, payment_id, credit_note_id, tenant_id, amount_cents, currency, method, "
                                    "created_by, created_by_name, created_at) VALUES ('r1', 'p1', 'n1', 't', 20, 'USD', 'staff_recorded', 'u', 'U', now())"))
            for change in ("UPDATE credit_notes SET amount_cents = 1", "UPDATE credit_notes SET pdf_key = 'other.pdf'", "DELETE FROM credit_notes",
                           "UPDATE billing_refunds SET amount_cents = 1", "DELETE FROM billing_refunds"):
                with pytest.raises(Exception, match="append-only"):
                    async with conn.begin_nested():
                        await conn.execute(text(change))
        await fresh.dispose()
        assert diff == []
    finally:
        async with admin.connect() as conn:  # (a connection the test closed may not have gone yet)
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        await admin.dispose()


async def test_the_agent_starts_work_only_for_the_person_it_is_chatting_with(client):
    from app.core.agent_token import mint_agent_token
    from tests.conftest import TENANT, upload

    tpl = await create_template(client)
    chat = (await client.post(f"{API}/conversations", headers=ws(tpl), json={"title": "Work"})).json()
    await client.post(f"{API}/conversations/{chat['id']}/messages?wait=false", headers=ws(tpl), json={"content": "Read roster.png"})
    [message] = await outbox_events(tpl, "human.message")
    doc = await upload(client, tpl)

    as_chat = {"Authorization": f"Bearer {mint_agent_token(TENANT, tpl, message.id)}"}
    res = await client.post(f"{AGENT}/documents/{doc}/process", headers=as_chat, json={"reason": "they asked"})
    assert res.status_code == 200, res.text
    [work] = await outbox_events(tpl, "document.submitted")
    assert (work.actor["source"], work.actor["conversation_id"], work.actor["origin"]) == ("chat", chat["id"], "user")

    # Any other run (here, one with no chat behind it) can't start work on someone's behalf.
    assert (await client.post(f"{AGENT}/documents/{doc}/process", headers=agent(tpl), json={})).status_code == 403
