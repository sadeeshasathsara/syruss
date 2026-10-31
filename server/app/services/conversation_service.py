"""
Chat with the workspace agent.

A user message is stored and turned into a `human.message` agent event. The agent
replies through the Agent Tool API, which delivers the reply live on the conversation's
channel. The synchronous and streaming endpoints simply wait for that reply.

Two kinds of conversation:
  chat   your private chats with Syruss;
  agent  conversations Syruss opened about an issue, shared with everyone whose role can settle
         it (its audience). Replying wakes Syruss with its saved working state for that issue.
"""
import json
import uuid
from datetime import datetime, timezone
from typing import AsyncIterator, List, Optional
from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.core.scope import TemplateScope
from app.models.conversation import ChatMessage, Conversation
from app.models.outbox import AgentEventOutbox
from app.models.trace import AgentExecutionTrace
from app.schemas.conversation import ChatMessageCreate, ConversationCreate, ConversationUpdate
from app.services.agent_events import enqueue_agent_event, agent_event_relay
from app.services import budget_service
from app.services.ui_events import listen_channel, publish_ui_event

REPLY_TIMEOUT_SECONDS = 180


DEFAULT_TITLE = "New chat"
# Who an agent conversation is for, by the permission their role must have.
AUDIENCE_PERMISSION = {"admin": "template.publish", "reviewer": "documents.review"}
# Threads Syruss starts: conversations about issues, and the threads of its flags.
SYRUSS_KINDS = ("agent", "flag")


def _audiences(scope: TemplateScope) -> List[str]:
    """The agent conversations the caller's role can see and reply to."""
    return [a for a, permission in AUDIENCE_PERMISSION.items() if permission in scope.permissions]


class ConversationService:
    @staticmethod
    async def list_conversations(db: AsyncSession, scope: TemplateScope, kind: str = "chat", limit: int = 20,
                                 offset: int = 0, status: Optional[str] = None) -> dict:
        """A page of conversations, newest first, without their messages (only what a list shows)."""
        columns = (Conversation.id, Conversation.title, Conversation.kind, Conversation.status, Conversation.audience,
                   Conversation.updated_at, Conversation.created_at)
        if kind == "agent":
            where = [Conversation.template_id == scope.template_id, Conversation.kind == "agent",
                     Conversation.audience.in_(_audiences(scope))]
            if status:
                where.append(Conversation.status == status)
        elif kind == "history":  # your chats and Syruss's conversations with your role, together
            audiences = _audiences(scope)
            where = [Conversation.template_id == scope.template_id, or_(
                (Conversation.kind == "chat") & (Conversation.user_id == scope.user_id),
                Conversation.kind.in_(SYRUSS_KINDS) & Conversation.audience.in_(audiences),
            )]
        else:
            where = [Conversation.template_id == scope.template_id, Conversation.user_id == scope.user_id,
                     Conversation.kind == "chat"]
        rows = (await db.execute(select(*columns).where(*where).order_by(Conversation.updated_at.desc())
                                 .offset(offset).limit(limit + 1))).all()
        items = [dict(zip(("id", "title", "kind", "status", "audience", "updated_at", "created_at"), r)) for r in rows[:limit]]
        result = {"items": items, "has_more": len(rows) > limit, "next_offset": offset + len(items)}
        if kind == "agent":
            result["open"] = (await db.execute(select(func.count(Conversation.id)).where(
                *where[:3], Conversation.status == "open"))).scalar() or 0
        return result

    @staticmethod
    async def create_conversation(db: AsyncSession, scope: TemplateScope, data: Optional[ConversationCreate] = None) -> Conversation:
        conv = Conversation(
            id=str(uuid.uuid4()),
            tenant_id=scope.tenant_id,
            template_id=scope.template_id,
            user_id=scope.user_id,
            title=(data.title if data and data.title else DEFAULT_TITLE).strip(),
            context_metadata=dict(data.context_metadata or {}) if data else {},
        )
        db.add(conv)
        await db.commit()
        return await ConversationService.get_conversation(db, scope, conv.id)

    @staticmethod
    async def get_or_create_for_channel(db: AsyncSession, scope: TemplateScope, channel_id: str) -> Conversation:
        """Live sessions (template wizard, help panel) use their channel id as the conversation id."""
        conv = await db.get(Conversation, channel_id)
        if conv and conv.template_id != scope.template_id:
            raise HTTPException(status_code=409, detail="This session belongs to another workspace.")
        if conv is None:
            conv = Conversation(
                id=channel_id, tenant_id=scope.tenant_id, template_id=scope.template_id,
                user_id=scope.user_id, title="Workspace session", context_metadata={"channel_id": channel_id},
            )
            db.add(conv)
            await db.flush()
        return conv

    @staticmethod
    async def get_conversation(db: AsyncSession, scope: TemplateScope, conversation_id: str, mine_only: bool = False) -> Conversation:
        """One of your own chats, or a conversation Syruss opened with people in your role. Other
        people's chats are private."""
        stmt = (
            select(Conversation)
            .where(Conversation.id == conversation_id, Conversation.template_id == scope.template_id)
            .options(selectinload(Conversation.messages))
        )
        conv = (await db.execute(stmt)).scalar_one_or_none()
        if conv and conv.kind in SYRUSS_KINDS and not mine_only and conv.audience in _audiences(scope):
            return conv
        if not conv or conv.kind != "chat" or conv.user_id != scope.user_id:
            raise HTTPException(status_code=404, detail="Conversation not found in this workspace.")
        return conv

    @staticmethod
    async def update_conversation(db: AsyncSession, scope: TemplateScope, conversation_id: str, data: ConversationUpdate) -> Conversation:
        conv = await ConversationService.get_conversation(db, scope, conversation_id, mine_only=True)
        conv.title = data.title.strip()
        await db.commit()
        return await ConversationService.get_conversation(db, scope, conversation_id)

    @staticmethod
    async def delete_conversation(db: AsyncSession, scope: TemplateScope, conversation_id: str) -> None:
        conv = await ConversationService.get_conversation(db, scope, conversation_id, mine_only=True)
        await db.delete(conv)
        await db.commit()

    @staticmethod
    async def linked_conversation(db: AsyncSession, scope: TemplateScope, conversation_id: Optional[str]) -> Optional[str]:
        """The conversation a dashboard action belongs to: one of yours in this workspace, or none."""
        if not conversation_id:
            return None
        conv = await db.get(Conversation, conversation_id)
        if conv and conv.template_id == scope.template_id and conv.user_id == scope.user_id:
            return conv.id
        return None

    @staticmethod
    async def record_action(db: AsyncSession, scope: TemplateScope, text: str, conversation_id: Optional[str]) -> Optional[ChatMessage]:
        """Notes something you did in the conversation it belongs to, without waking the agent."""
        if not await ConversationService.linked_conversation(db, scope, conversation_id):
            return None
        conv = await db.get(Conversation, conversation_id)
        msg = ChatMessage(
            id=str(uuid.uuid4()), conversation_id=conv.id, tenant_id=scope.tenant_id, role="system",
            content=text, evidence_citations=[], confidence=1.0, agent_trace={},
        )
        db.add(msg)
        conv.updated_at = datetime.now(timezone.utc)
        return msg

    @staticmethod
    async def post_user_message(
        db: AsyncSession, scope: TemplateScope, conv: Conversation, payload: ChatMessageCreate, channel_id: Optional[str] = None,
    ) -> ChatMessage:
        """Stores the message and wakes the agent (committed together)."""
        name = scope.username
        sender = {"user_id": scope.user_id, "name": name, "role": scope.role_name or None}
        context = dict(payload.context or {})
        if context:
            conv.context_metadata = {**(conv.context_metadata or {}), **context}
        msg = ChatMessage(
            id=str(uuid.uuid4()), conversation_id=conv.id, tenant_id=scope.tenant_id, role="user",
            content=payload.content.strip(), evidence_citations=[], confidence=1.0, agent_trace={},
        )
        db.add(msg)
        if conv.kind == "chat" and conv.title in (DEFAULT_TITLE, "New Conversation"):  # the first message names the chat
            conv.title = payload.content.strip()[:60]
        if conv.kind in SYRUSS_KINDS and conv.status == "resolved":  # writing again reopens it
            conv.status, conv.resolved_at = "open", None
        conv.updated_at = datetime.now(timezone.utc)
        paused = await budget_service.paused_reply(db, scope.template_id)
        if paused:  # the agent can't work this month: say so now rather than leave them waiting
            reply = ChatMessage(
                id=str(uuid.uuid4()), conversation_id=conv.id, tenant_id=scope.tenant_id, role="assistant",
                content=paused, evidence_citations=[], confidence=1.0, agent_trace={"reply_to": msg.id, "budget": "paused"},
            )
            db.add(reply)
            await db.commit()
            await publish_ui_event(channel_id or conv.id, "agent_message", {
                "message_id": reply.id, "conversation_id": conv.id, "reply_to": msg.id, "content": paused,
                "answer": paused, "evidence_citations": [], "confidence": 1.0,
            }, tenant_id=scope.tenant_id, template_id=scope.template_id, private_to=conv.user_id if conv.kind == "chat" else None)
            return msg
        enqueue_agent_event(
            db, event_type="human.message", tenant_id=scope.tenant_id, template_id=scope.template_id,
            payload={
                "conversation_id": conv.id, "message_id": msg.id, "text": msg.content, "sender": sender,
                "conversation_kind": conv.kind, "conversation_title": conv.title,
                "channel_id": channel_id or conv.id, "page": context.get("current_page"), "context": context,
            },
            actor_id=scope.user_id, actor_type="user", actor_name=sender["name"], source="chat", conversation_id=conv.id,
        )
        await db.commit()
        agent_event_relay.notify()
        return msg

    @staticmethod
    async def stop_reply(db: AsyncSession, scope: TemplateScope, conversation_id: str) -> dict:
        """
        Stops Syruss answering in this conversation: every reply still queued or under way is marked
        cancelled. A queued one never starts; a running one stops at its next check (between steps,
        and every couple of seconds while the model is thinking). Work started from the chat (reading
        documents) carries on: only the reply stops.
        """
        conv = await ConversationService.get_conversation(db, scope, conversation_id)
        finished = select(AgentExecutionTrace.event_id).where(AgentExecutionTrace.event_id.is_not(None))
        events = (await db.execute(select(AgentEventOutbox).where(
            AgentEventOutbox.template_id == scope.template_id, AgentEventOutbox.conversation_id == conv.id,
            AgentEventOutbox.event_type == "human.message", AgentEventOutbox.cancelled_at.is_(None),
            AgentEventOutbox.id.not_in(finished),
        ))).scalars().all()
        if not events:
            return {"stopped": 0}
        now = datetime.now(timezone.utc)
        for event in events:
            event.cancelled_at, event.cancelled_by = now, scope.user_id
        note = ChatMessage(
            id=str(uuid.uuid4()), conversation_id=conv.id, tenant_id=scope.tenant_id, role="system",
            content=f"{scope.username or 'You'} stopped Syruss.", evidence_citations=[], confidence=1.0,
            agent_trace={"stopped_events": [e.id for e in events]},
        )
        db.add(note)
        await db.commit()
        await publish_ui_event(conv.id, "agent_stopped", {"conversation_id": conv.id, "event_ids": [e.id for e in events]},
                               tenant_id=scope.tenant_id, template_id=scope.template_id, private_to=conv.user_id if conv.kind == "chat" else None)
        return {"stopped": len(events), "event_ids": [e.id for e in events]}

    @staticmethod
    async def send_and_wait(
        db: AsyncSession, scope: TemplateScope, conversation_id: str, payload: ChatMessageCreate,
    ) -> dict:
        """Posts the message and returns the agent's reply (or a pending notice on timeout)."""
        conv = await ConversationService.get_conversation(db, scope, conversation_id)
        async with listen_channel(conv.id) as wait:
            msg = await ConversationService.post_user_message(db, scope, conv, payload)
            reply = await wait(
                lambda e: e.get("event_type") == "agent_message" and e.get("reply_to") == msg.id,
                timeout=REPLY_TIMEOUT_SECONDS,
            )
        if reply:
            return {
                "id": reply["message_id"], "conversation_id": conv.id, "role": "assistant",
                "content": reply["content"], "evidence_citations": reply.get("evidence_citations", []),
                "confidence": reply.get("confidence", 1.0), "agent_trace": {"reply_to": msg.id},
                "created_at": datetime.now(timezone.utc),
            }
        return {
            "id": f"pending-{msg.id}", "conversation_id": conv.id, "role": "assistant",
            "content": "I'm still working on this. My reply will appear in this conversation.",
            "evidence_citations": [], "confidence": 0.0, "agent_trace": {"pending": True, "reply_to": msg.id},
            "created_at": datetime.now(timezone.utc),
        }

    @staticmethod
    async def stream_reply(
        db: AsyncSession, scope: TemplateScope, conversation_id: str, payload: ChatMessageCreate,
    ) -> AsyncIterator[str]:
        """Server-Sent Events: `thought` while the agent works, then `token` and `done`."""
        conv = await ConversationService.get_conversation(db, scope, conversation_id)

        def sse(event: str, data: dict) -> str:
            return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"

        queue: list = []

        def collect(event: dict) -> None:
            if event.get("event_type") == "agent_thought" and event.get("thought"):
                queue.append(sse("thought", {"thought": event["thought"], "step": event.get("step")}))

        async with listen_channel(conv.id) as wait:
            msg = await ConversationService.post_user_message(db, scope, conv, payload)
            yield sse("thought", {"thought": "Message delivered to the workspace agent.", "step": 0})
            reply = None
            deadline_left = REPLY_TIMEOUT_SECONDS
            while reply is None and deadline_left > 0:
                reply = await wait(
                    lambda e: e.get("event_type") == "agent_message" and e.get("reply_to") == msg.id,
                    timeout=min(2, deadline_left), on_event=collect,
                )
                deadline_left -= 2
                while queue:
                    yield queue.pop(0)
        if reply:
            yield sse("token", {"delta": reply["content"]})
            yield sse("done", {"message_id": reply["message_id"], "content": reply["content"],
                               "evidence_citations": reply.get("evidence_citations", []),
                               "confidence": reply.get("confidence", 1.0)})
        else:
            notice = "I'm still working on this. My reply will appear in this conversation."
            yield sse("token", {"delta": notice})
            yield sse("done", {"pending": True, "content": notice})


conversation_service = ConversationService()
