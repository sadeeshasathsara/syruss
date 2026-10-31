"""
The agent service runtime.

Consumes `agent.events` from Kafka. Events of one workspace share a partition (keyed by
template_id) and are handled strictly in order; different workspaces run in parallel.
Offsets are committed only after an event is fully handled, so a crash re-delivers it;
handled event ids are remembered, so a re-delivery is skipped. An event that keeps failing
is retried with backoff, then sent to the dead-letter topic and its work is marked failed.
"""
import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer, TopicPartition
from agentic_framework.agent import checkpoint, memory_upkeep
from agentic_framework.agent.loop import RunResult, WorkspaceAgent, stopped
from agentic_framework.agent.tools import RunContext
from agentic_framework.config import settings
from agentic_framework.core_api import CoreAPI, CoreUnavailable, ToolRefused
from agentic_framework.events import AgentEvent
from agentic_framework.llm import get_chat_model, model_name, run_cost
from agentic_framework.memory import MemoryStore
from agentic_framework import token_check
from agentic_framework.telemetry import Telemetry

logger = logging.getLogger("agent.worker")


ANALYTICS_WORDS = re.compile(r"\b(analytics?|screens?|dashboards?|reports?|pivot|breakdown)\b", re.I)


def wants_analyst(event: AgentEvent) -> bool:
    """A chat about analytics screens goes to the analyst (its guide, its tools, its warmer model): when the screens page asked
    (mode "screens"), when they wrote it from an Analytics page, or when their message is plainly about analytics or screens."""
    if event.type != "human.message" or event.payload.get("conversation_kind") != "chat":
        return False
    context = event.payload.get("context") or {}
    return (context.get("mode") == "screens" or "/analytics" in (event.payload.get("page") or "")
            or bool(ANALYTICS_WORDS.search(event.payload.get("text") or "")))


class AgentWorker:
    def __init__(self, agent: WorkspaceAgent, memory: MemoryStore, producer: Optional[AIOKafkaProducer],
                 chat_agent: Optional[WorkspaceAgent] = None, analyst_agent: Optional[WorkspaceAgent] = None):
        self.agent = agent
        # Chats get the assistant mode; by default on the same model as the work.
        self.chat_agent = chat_agent or WorkspaceAgent(agent.base_model, mode="chat")
        # Evaluations read like work, with only the tools that can't change the workspace.
        self.eval_agent = WorkspaceAgent(agent.base_model, mode="eval")
        # Screens and analytics: a warmer model, so it comes up with new things to look at (its numbers still come from tools).
        self.analyst_agent = analyst_agent or WorkspaceAgent(agent.base_model, mode="analyst")
        self.memory = memory
        self.producer = producer
        self._slots = asyncio.Semaphore(settings.MAX_PARALLEL_WORKSPACES)

    # --- one event ---------------------------------------------------------

    async def handle(self, event: AgentEvent) -> str:
        if await self.memory.was_processed(event.event_id):
            logger.info("Skipping already handled event %s", event.event_id)
            return "DUPLICATE"

        try:
            token_check.verify(event)
        except token_check.BadToken as exc:
            # Never retried and never run: an event whose token doesn't match it can't be trusted.
            logger.error("Refusing event %s: %s", event.event_id, exc)
            await self._dead_letter(event, exc)
            await self.memory.mark_processed(event.event_id, event.type, event.template_id, "DEAD_LETTERED")
            return "DEAD_LETTERED"

        last_error: Optional[Exception] = None
        for attempt in range(1, settings.MAX_ATTEMPTS + 1):
            api = CoreAPI(event.agent_token)
            try:
                status = await self._run_once(event, api)
                await self.memory.mark_processed(event.event_id, event.type, event.template_id, status)
                return status
            except ToolRefused as exc:
                # The platform refused a runtime-level call (expired token, deleted workspace or
                # document): retrying cannot help.
                logger.warning("Dropping event %s: %s", event.event_id, exc)
                await self.memory.mark_processed(event.event_id, event.type, event.template_id, "DROPPED")
                return "DROPPED"
            except Exception as exc:  # model/provider errors, core unavailable, bugs
                last_error = exc
                logger.exception("Event %s attempt %d/%d failed", event.event_id, attempt, settings.MAX_ATTEMPTS)
            finally:
                await api.close()
            await asyncio.sleep(min(30, 2 ** attempt))

        await self._dead_letter(event, last_error)
        await self.memory.mark_processed(event.event_id, event.type, event.template_id, "DEAD_LETTERED")
        return "DEAD_LETTERED"

    async def _run_once(self, event: AgentEvent, api: CoreAPI) -> str:
        ctx = RunContext(event=event, api=api, memory=self.memory)
        if await stopped(ctx):  # stopped before it started: nothing to do
            logger.info("Event %s was stopped before it started", event.event_id)
            return "CANCELLED"
        run_id = f"run_{uuid.uuid4().hex[:10]}"
        private_chat = event.type == "human.message" and event.payload.get("conversation_kind") == "chat"
        telemetry = Telemetry(self.producer, tenant_id=event.tenant_id, template_id=event.template_id,
                              channel_id=event.channel_id, run_id=run_id,
                              private_to=event.actor.get("id") if private_chat else None)
        if event.type == "document.submitted":
            doc = await api.get(f"/documents/{event.document_id}")
            # QUEUED: normal. PROCESSING: a previous attempt crashed mid-run (a workspace's events run
            # strictly in order), so resume. Anything else was already handled by another run.
            if doc["status"] not in ("QUEUED", "PROCESSING"):
                logger.info("Skipping %s: document %s is already %s", event.event_id, event.document_id, doc["status"])
                return "SKIPPED"
            await api.post(f"/documents/{event.document_id}/status", {"status": "PROCESSING"})

        # A reply in a conversation the agent opened resumes the work it was doing, with full tools.
        # A reply in a thread Syruss started (a conversation, or a flag's thread, including the answer to
        # the flag itself) resumes the work it was doing there, with full tools.
        resuming = bool(event.payload.get("conversation_id")) and (
            (event.type == "human.message" and event.payload.get("conversation_kind") in ("agent", "flag"))
            or event.type == "human.answered")
        analytical = event.type == "schedule.insights" or wants_analyst(event)
        agent = (self.agent if resuming else self.analyst_agent if analytical
                 else {"human.message": self.chat_agent, "eval.item": self.eval_agent}.get(event.type, self.agent))
        if event.type != "eval.item":  # an evaluation must not change anything, memory included
            await self._upkeep("before the run", memory_upkeep.before_run(ctx))
        history = None
        if resuming:
            saved = await api.get(f"/conversations/{event.payload['conversation_id']}/state")
            history = checkpoint.load(((saved.get("state") or {}).get("messages")) or [])
        result: RunResult = await agent.run(ctx, telemetry, history=history)
        await self._enforce_outcomes(event, api, ctx, result)
        await self._save_checkpoints(event, api, ctx, result, resuming)
        if event.type == "human.message" and not resuming:
            await self._upkeep("summarizing the chat", memory_upkeep.summarize_conversation(ctx, agent.base_model))
        await api.post("/traces", {
            "trigger": event.type, "model": model_name(agent.mode), "status": result.status,
            "started_at": result.started_at.isoformat(), "completed_at": result.completed_at.isoformat(),
            "steps": result.steps, "final_thought": result.final_thought, "usage": result.usage,
            "document_id": event.document_id, "run_id": run_id, "origin": event.actor,
            "model_calls": result.model_calls, "cost_usd": run_cost(model_name(agent.mode), result.usage),
        })
        logger.info("Event %s (%s) handled: %s, %d steps", event.event_id, event.type, result.status, len(result.steps))
        return result.status

    async def _save_checkpoints(self, event: AgentEvent, api: CoreAPI, ctx: RunContext, result: RunResult, resuming: bool) -> None:
        """Conversations the agent opened keep its working state in Postgres until they are resolved."""
        ids = list(ctx.opened_conversations)
        if resuming:
            ids.append(event.payload["conversation_id"])
        saved = checkpoint.dump(result.messages)
        for conv_id in dict.fromkeys(ids):
            if conv_id not in ctx.resolved_conversations:
                await api.put(f"/conversations/{conv_id}/state", {"messages": saved})

    async def _enforce_outcomes(self, event: AgentEvent, api: CoreAPI, ctx: RunContext, result: RunResult) -> None:
        """Nothing is left hanging: a document always ends in a state, a chat message always gets a reply."""
        if event.type == "document.submitted":
            doc = await api.get(f"/documents/{event.document_id}")
            if doc["status"] == "PROCESSING":
                await api.post(f"/documents/{event.document_id}/status", {
                    "status": "FAILED",
                    "message": f"The agent ended without an outcome ({result.status}). {result.final_thought}"[:1000],
                })
        if event.type == "eval.item" and event.document_id not in ctx.resolved_documents:
            await api.post("/evals/outcome", {"note": f"Ended without a record ({result.status}). {result.final_thought}"[:2000]})
        if event.type == "human.message" and not ctx.replied and result.status != "CANCELLED":
            await api.post(f"/conversations/{event.payload['conversation_id']}/messages", {
                "content": result.final_thought or "I couldn't finish working on this. Please try again.",
                "reply_to": event.payload.get("message_id"), "channel_id": event.payload.get("channel_id"),
                "confidence": 0.5,
            })

    async def _dead_letter(self, event: AgentEvent, error: Optional[Exception]) -> None:
        logger.error("Dead-lettering event %s (%s): %s", event.event_id, event.type, error)
        await self._log_failure(event, error)
        if self.producer:
            body = {**event.model_dump(exclude={"agent_token"}), "error": str(error)}
            await self.producer.send_and_wait(settings.AGENT_DLQ_TOPIC, key=event.template_id.encode(),
                                              value=json.dumps(body, default=str).encode())
        api = CoreAPI(event.agent_token)
        try:
            if event.type == "document.submitted":
                await api.post(f"/documents/{event.document_id}/status",
                               {"status": "FAILED", "message": f"Agent could not process the document: {error}"[:1000]})
            elif event.type == "eval.item":
                await api.post("/evals/outcome", {"note": f"The agent could not read it: {error}"[:2000]})
            elif event.type == "human.message":
                await api.post(f"/conversations/{event.payload['conversation_id']}/messages", {
                    "content": "Sorry, I ran into a problem and couldn't answer. Please try again shortly.",
                    "reply_to": event.payload.get("message_id"), "channel_id": event.payload.get("channel_id"),
                    "confidence": 0.0,
                })
        except (CoreUnavailable, ToolRefused) as exc:
            logger.error("Could not record failure for event %s: %s", event.event_id, exc)
        finally:
            await api.close()

    async def _upkeep(self, what: str, job) -> None:
        """Memory housekeeping never fails a run: problems are logged and retried next time."""
        try:
            await job
        except Exception as exc:
            logger.warning("Memory upkeep failed (%s): %s", what, exc)

    async def _log_failure(self, event: AgentEvent, error: Optional[Exception]) -> None:
        """A run that never finished still belongs in the run log."""
        api = CoreAPI(event.agent_token)
        now = datetime.now(timezone.utc).isoformat()
        try:
            await api.post("/traces", {
                "trigger": event.type, "model": model_name(), "status": "DEAD_LETTERED", "started_at": now,
                "completed_at": now, "error_message": str(error)[:2000] if error else None,
                "document_id": event.document_id, "origin": event.actor,
            })
        except (CoreUnavailable, ToolRefused) as exc:
            logger.error("Could not log the failed run for event %s: %s", event.event_id, exc)
        finally:
            await api.close()

    # --- consuming -----------------------------------------------------------

    async def _handle_partition(self, messages: List) -> None:
        async with self._slots:
            for msg in messages:
                try:
                    event = AgentEvent.model_validate_json(msg.value)
                except Exception:
                    logger.exception("Discarding malformed event at offset %s", msg.offset)
                    continue
                logger.info("Handling %s for workspace %s", event.type, event.workspace_key)
                await self.handle(event)

    async def consume(self, consumer: AIOKafkaConsumer) -> None:
        while True:
            batches: Dict[TopicPartition, List] = await consumer.getmany(timeout_ms=1000, max_records=20)
            if not batches:
                continue
            await asyncio.gather(*(self._handle_partition(msgs) for msgs in batches.values()))
            await consumer.commit()


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    token_check.check_startup()  # refuse to start without a way to verify event tokens
    memory = MemoryStore()
    await memory.init()
    producer = AIOKafkaProducer(bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS)
    await producer.start()
    consumer = AIOKafkaConsumer(
        settings.AGENT_EVENTS_TOPIC,
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
        group_id=settings.CONSUMER_GROUP,
        enable_auto_commit=False,
        auto_offset_reset="earliest",
        max_poll_interval_ms=max(300_000, (settings.MAX_RUN_SECONDS * settings.MAX_ATTEMPTS + 120) * 1000),
    )
    await consumer.start()
    worker = AgentWorker(WorkspaceAgent(get_chat_model("work")), memory, producer,
                         chat_agent=WorkspaceAgent(get_chat_model("chat"), mode="chat"),
                         analyst_agent=WorkspaceAgent(get_chat_model("analytics"), mode="analyst"))
    logger.info("Workspace agent service started (work model %s, chat model %s), consuming '%s'",
                model_name("work"), model_name("chat"), settings.AGENT_EVENTS_TOPIC)
    try:
        await worker.consume(consumer)
    finally:
        await consumer.stop()
        await producer.stop()
        await memory.close()
