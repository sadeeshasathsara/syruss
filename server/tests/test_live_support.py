"""
Live support in a person's chat: the agent asks for someone from the team, the first staff member to accept takes the
chat, the agent stays silent while they talk, typing and messages reach only the right side, and when it ends the
agent answers again with the whole exchange in its history.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from sqlalchemy import select, update
from app.core.agent_token import mint_agent_token
from app.models.conversation import Conversation
from app.models.outbox import AgentEventOutbox
from app.models.support import SupportTicket
from app.models.support_live import SupportPresence
from app.services.live import platform_feed
from app.services.live.broadcaster import broadcaster
from app.services.support import live
from tests.conftest import TENANT, AsyncSessionLocal, create_template, make_staff, person, ws
from tests.test_workspace_authorization import chat_token

API = "/api/v1"
LIVE = f"{API}/sysadmin/support/live"
AGENT = "/internal/agent/v1"
ASK = {"reason": "Wants to talk about a payment", "summary": "Their card payment failed twice today; I checked the bill and it is still open."}


def drain(queue) -> list:
    out = []
    while not queue.empty():
        out.append(queue.get_nowait())
    return out


async def staff(client, user_id: str, name: str, role: str = "support") -> dict:
    headers = {**person(user_id, name), "X-Client-ID": "syruss-admin"}
    await make_staff(client, headers, role)
    return headers


async def agent_wakeups(conversation_id: str) -> int:
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(AgentEventOutbox).where(AgentEventOutbox.event_type == "human.message"))).scalars().all()
    return sum(1 for e in rows if e.payload.get("conversation_id") == conversation_id)


async def test_a_person_from_the_team_takes_over_the_chat_and_hands_it_back(client, mailbox):
    tpl = await create_template(client, "Live help")
    chat, token = await chat_token(client, tpl, {})
    cid = chat["id"]
    ana = await staff(client, "usr_live_ana", "Ana Agent")
    ben = await staff(client, "usr_live_ben", "Ben Backup")

    # Nobody online: the agent is told to offer a ticket
    assert (await client.post(f"{AGENT}/support/live", headers=token, json=ASK)).json()["status"] == "unavailable"
    for who in (ana, ben):
        assert (await client.put(f"{LIVE}/presence", headers=who, json={"status": "online"})).status_code == 200
    asked = await client.post(f"{AGENT}/support/live", headers=token, json=ASK)
    assert asked.status_code == 200 and asked.json()["status"] == "waiting" and asked.json()["position"] == 1
    got = (await client.get(f"{API}/conversations/{cid}", headers=ws(tpl))).json()
    assert got["live_support"]["state"] == "waiting" and "summary" not in got["live_support"]

    # While waiting, the person's messages don't wake the agent
    before = await agent_wakeups(cid)
    await client.post(f"{API}/conversations/{cid}/messages?wait=false", headers=ws(tpl), json={"content": "Still there?"})
    assert await agent_wakeups(cid) == before

    # Staff without support.respond can't see the queue; the queue shows the handover, not the chat
    viewer = await staff(client, "usr_live_viewer", "Val Viewer", "viewer")
    assert (await client.get(f"{LIVE}/queue", headers=viewer)).status_code == 403
    queue = (await client.get(f"{LIVE}/queue", headers=ana)).json()
    item = next(i for i in queue["waiting"] if i["conversation_id"] == cid)
    assert item["summary"] == ASK["summary"] and item["workspace"] == "Live help" and "messages" not in item
    assert (await client.get(f"{LIVE}/chats/{cid}", headers=ana)).status_code == 403   # not before accepting

    # The first to accept takes it
    accepted = await client.post(f"{LIVE}/chats/{cid}/accept", headers=ana)
    assert accepted.status_code == 200, accepted.text
    assert [m["content"] for m in accepted.json()["messages"] if m["role"] == "user"][-1] == "Still there?"
    assert (await client.post(f"{LIVE}/chats/{cid}/accept", headers=ben)).status_code == 409
    assert (await client.get(f"{LIVE}/chats/{cid}", headers=ben)).status_code == 403
    assert (await client.post(f"{LIVE}/chats/{cid}/messages", headers=ben, json={"body": "Hi"})).status_code == 403

    # Messages and typing reach only the other side; staff events never reach the workspace's own channel
    person_side = broadcaster.subscribe(cid, replay=False)
    workspace_side = broadcaster.subscribe(f"template:{tpl}", replay=False)
    staff_side = platform_feed.listen(live.chat_channel(cid))
    try:
        said = await client.post(f"{LIVE}/chats/{cid}/messages", headers=ana, json={"body": "Hi, I'm looking at your payment now."})
        assert said.status_code == 201
        assert (await client.post(f"{LIVE}/chats/{cid}/typing", headers=ana)).status_code == 204
        await client.post(f"{API}/conversations/{cid}/support/typing", headers=ws(tpl))
        await client.post(f"{API}/conversations/{cid}/messages?wait=false", headers=ws(tpl), json={"content": "Thanks!"})
        await asyncio.sleep(0)
        to_person = [e["event_type"] for e in drain(person_side)]
        to_staff = [e["kind"] for e in drain(staff_side.queue)]
        to_workspace = drain(workspace_side)
    finally:
        broadcaster.unsubscribe(cid, person_side)
        broadcaster.unsubscribe(f"template:{tpl}", workspace_side)
        platform_feed.stop(staff_side)
    assert "support_message" in to_person and "support_typing" in to_person
    assert to_staff.count("support.message") == 2 and "support.typing" in to_staff   # her own message, and theirs
    assert not any(str(e.get("channel_id", "")).startswith("support:") or e.get("event_type", "").startswith("support") for e in to_workspace)
    assert await agent_wakeups(cid) == before

    # A reply the agent was about to send is refused while the team member is there
    late = await client.post(f"{AGENT}/conversations/{cid}/messages", headers=token, json={"content": "I can help with that!"})
    assert late.status_code == 409
    history = (await client.get(f"{AGENT}/conversations/{cid}/messages", headers=token)).json()
    assert any(m["role"] == "staff" and m["name"] == "Ana Agent" for m in history)

    # Handing over, then a ticket for follow-up
    assert (await client.post(f"{LIVE}/chats/{cid}/transfer", headers=ana, json={"to": "usr_live_viewer"})).status_code == 422
    assert (await client.post(f"{LIVE}/chats/{cid}/transfer", headers=ana, json={"to": "usr_live_ben"})).status_code == 200
    assert (await client.post(f"{LIVE}/chats/{cid}/messages", headers=ana, json={"body": "x"})).status_code == 403
    made = await client.post(f"{LIVE}/chats/{cid}/ticket", headers=ben)
    assert made.status_code == 201
    async with AsyncSessionLocal() as db:
        ticket = (await db.execute(select(SupportTicket).where(SupportTicket.number == made.json()["number"]))).scalar_one()
    assert ticket.assignee_name == "Ben Backup" and ticket.related == {"conversation_id": cid}
    assert (await client.post(f"{LIVE}/chats/{cid}/ticket", headers=ben)).status_code == 409

    # The person ends it: the agent answers again, and they can rate it
    ended = await client.post(f"{API}/conversations/{cid}/support/end", headers=ws(tpl))
    assert ended.json()["support"]["state"] == "ended" and ended.json()["support"]["ticket_number"] == made.json()["number"]
    await client.post(f"{API}/conversations/{cid}/messages?wait=false", headers=ws(tpl), json={"content": "One more question"})
    assert await agent_wakeups(cid) == before + 1
    rated = await client.post(f"{API}/conversations/{cid}/support/rating", headers=ws(tpl), json={"rating": "up", "note": "Quick"})
    assert rated.json()["support"]["rating"] == "up"
    # Someone else's chat isn't theirs to end
    other = person("usr_live_other", "Olga Other")
    assert (await client.post(f"{API}/conversations/{cid}/support/end", headers={**ws(tpl), **other})).status_code in (403, 404)


async def test_the_agent_asks_only_in_a_persons_chat_and_not_again_right_away(client):
    tpl = await create_template(client, "Live rules")
    chat, token = await chat_token(client, tpl, {})
    ana = await staff(client, "usr_live_ana2", "Ana Two")
    await client.put(f"{LIVE}/presence", headers=ana, json={"status": "online"})
    work = {"Authorization": f"Bearer {mint_agent_token(TENANT, tpl, 'evt_live_work')}"}
    assert (await client.post(f"{AGENT}/support/live", headers=work, json=ASK)).status_code == 403
    assert (await client.post(f"{AGENT}/support/live", headers=token, json=ASK)).json()["status"] == "waiting"
    await client.post(f"{LIVE}/chats/{chat['id']}/accept", headers=ana)
    await client.post(f"{LIVE}/chats/{chat['id']}/end", headers=ana)
    assert (await client.post(f"{AGENT}/support/live", headers=token, json=ASK)).json()["status"] == "too_soon"


async def test_unanswered_chats_go_back_to_the_agent_and_abandoned_ones_back_to_the_queue(client):
    tpl = await create_template(client, "Live timing")
    ana = await staff(client, "usr_live_ana3", "Ana Three")
    await client.put(f"{LIVE}/presence", headers=ana, json={"status": "online"})
    waiting, token = await chat_token(client, tpl, {})
    await client.post(f"{AGENT}/support/live", headers=token, json=ASK)
    taken, token2 = await chat_token(client, tpl, {})
    await client.post(f"{AGENT}/support/live", headers=token2, json=ASK)
    await client.post(f"{LIVE}/chats/{taken['id']}/accept", headers=ana)
    async with AsyncSessionLocal() as db:
        conv = await db.get(Conversation, waiting["id"])
        conv.support = {**conv.support, "requested_at": (datetime.now(timezone.utc) - timedelta(minutes=live.WAIT_MINUTES + 1)).isoformat()}
        await db.execute(update(SupportPresence).where(SupportPresence.user_id == "usr_live_ana3")
                         .values(seen_at=datetime.now(timezone.utc) - timedelta(minutes=live.STAFF_GONE_MINUTES + 1)))
        await db.commit()
        await live.expire(db)
    async with AsyncSessionLocal() as db:
        gave_up, requeued = await db.get(Conversation, waiting["id"]), await db.get(Conversation, taken["id"])
        assert gave_up.support_state == "ended" and gave_up.support["end_reason"] == "no_staff"
        assert requeued.support_state == "waiting" and requeued.support["staff_id"] is None
        # Someone whose heartbeat stopped doesn't count as online
        assert not any(p.user_id == "usr_live_ana3" for p in await live.online(db))
