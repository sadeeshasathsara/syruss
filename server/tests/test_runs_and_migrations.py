"""
The run log (origins, cost, who may see what) and the database migrations.
"""
import asyncio
from datetime import datetime, timedelta, timezone
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
        "run_id": "run_abc", "origin": origin, "model_calls": 2, "cost_usd": 0.0081, **extra,
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
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        await conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = engine.url.set(database=name).render_as_string(hide_password=False)
    try:
        await asyncio.to_thread(command.upgrade, alembic_config(url), "head")
        fresh = create_async_engine(url)
        async with fresh.connect() as conn:
            diff = await conn.run_sync(lambda c: compare_metadata(
                MigrationContext.configure(c, opts={"include_object": include_object}), Base.metadata))
        await fresh.dispose()
        assert diff == []
    finally:
        async with admin.connect() as conn:
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
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
