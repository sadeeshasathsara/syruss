"""
One wake-up of the workspace agent: reason -> act -> observe, until it decides it is done
or a budget runs out. The loop has no knowledge of the business; it only runs the model,
executes the tools it chooses, shows it the images it asked for, and records a trace.
"""
import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from agentic_framework.agent.briefing import build_briefing, build_chat_briefing
from agentic_framework.agent import guides
from agentic_framework.agent.charter import ANALYST_CHARTER, ASSISTANT_CHARTER, CHARTER
from agentic_framework.agent.tools import ANALYST_TOOLS, CHAT_TOOLS, EVAL_TOOLS, RunContext, execute, tool_schemas, work_tools
from agentic_framework.config import settings
from agentic_framework.telemetry import Telemetry

logger = logging.getLogger("agent.loop")

STOP_POLL_SECONDS = 2.0   # how often a chat reply checks, while the model thinks, whether it was stopped


class RunStopped(Exception):
    """A person stopped this run."""


async def stopped(ctx: RunContext) -> bool:
    """Whether a person stopped the run. Once stopped, always stopped; a failed check means not stopped."""
    if not ctx.stopped:
        try:
            ctx.stopped = bool((await ctx.api.get("/run/cancelled")).get("cancelled"))
        except Exception as exc:  # the check must never break a run
            logger.debug("Stop check failed: %s", exc)
    return ctx.stopped


@dataclass
class RunResult:
    status: str                       # COMPLETED | BUDGET_EXCEEDED | CANCELLED (stopped by a person) | FAILED
    final_thought: str
    steps: List[Dict[str, Any]] = field(default_factory=list)
    usage: Dict[str, int] = field(default_factory=dict)
    model_calls: int = 0
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    messages: List[BaseMessage] = field(default_factory=list)   # the whole run, for checkpoints


def message_text(msg: BaseMessage) -> str:
    content = msg.content
    if isinstance(content, str):
        return content.strip()
    parts = []
    for part in content or []:
        if isinstance(part, str):
            parts.append(part)
        elif isinstance(part, dict) and part.get("type") == "text":
            parts.append(part.get("text", ""))
    return "".join(parts).strip()


TRIMMED = " …[trimmed to save space; call the tool again if you need the rest]"


def _trim_tool_results(messages: List[BaseMessage], keep: int, limit: int) -> None:
    """The whole history is resent with every model call. The newest `keep` tool results stay whole;
    older long ones are cut to their start (the agent already acted on them)."""
    seen = 0
    for msg in reversed(messages):
        if not isinstance(msg, ToolMessage) or not isinstance(msg.content, str):
            continue
        seen += 1
        if seen > keep and len(msg.content) > limit and not msg.content.endswith(TRIMMED):
            msg.content = msg.content[:limit] + TRIMMED


def _prune_images(messages: List[BaseMessage], keep: int) -> None:
    """Keeps only the most recent `keep` images in the prompt; older ones become a text note."""
    seen = 0
    for msg in reversed(messages):
        if not isinstance(msg, HumanMessage) or isinstance(msg.content, str):
            continue
        new_content, changed = [], False
        for part in reversed(msg.content):
            if isinstance(part, dict) and part.get("type") == "image_url":
                seen += 1
                if seen > keep:
                    new_content.append({"type": "text", "text": "[an earlier image was removed to save space; view it again if needed]"})
                    changed = True
                    continue
            new_content.append(part)
        if changed:
            msg.content = list(reversed(new_content))


def _rate_limit_delay(exc: Exception) -> float | None:
    """Seconds to wait if the provider rate-limited us, else None."""
    text = f"{type(exc).__name__} {exc}"
    if not re.search(r"RateLimit|RESOURCE_EXHAUSTED|\b429\b|rate limit", text, re.IGNORECASE):
        return None
    hint = re.search(r"retry in ([\d.]+)s", text) or re.search(r"retryDelay'?:\s*'?(\d+)s", text)
    return min(90.0, float(hint.group(1)) + 1) if hint else 30.0


def open_obligations(ctx: RunContext) -> List[str]:
    """What the run still owes, based on what woke the agent. Not a procedure: just the outcome."""
    e = ctx.event
    owed = []
    if e.type in ("document.submitted", "eval.item") and e.document_id not in (ctx.resolved_documents | ctx.parked_documents):
        owed.append(f"document {e.document_id} has no outcome yet (commit, send to review, park it with a "
                    "question, or mark it failed)")
    if e.type == "human.message" and not ctx.replied:
        owed.append("the user's message has not been answered yet (reply_to_user)")
    if e.type == "human.answered" and e.payload.get("conversation_id") and not e.payload.get("dismissed") and not ctx.replied:
        owed.append("you haven't told them in the flag's conversation what you did with their answer (reply_to_user); "
                    "then ask what you still need, or resolve_conversation if nothing is left")
    # Registration allowed: one reminder about who the saved record named that isn't registered.
    # (Where it isn't allowed, the platform refuses the commit and says to ask a person.)
    if ctx.unregistered and ctx.registration == "you_may_register" and not ctx.registration_reminded:
        ctx.registration_reminded = True
        listed = "; ".join(f"{t['type']} {t['id']}" + (f" ({t['name']})" if t.get("name") else "") for t in ctx.unregistered)
        owed.append(f"the record you saved names people or things that aren't registered ({listed}): register those "
                    "you are sure of with create_entity, or say in a sentence why not")
    return owed


class WorkspaceAgent:
    """
    mode "work": documents, template design, data sources, reflection; the full charter and tools.
    mode "chat": talking with one person; a short charter and the chat tools.
    mode "eval": re-reading a golden document for a shadow evaluation; the work charter, read-only tools.
    mode "analyst": custom screens and analytics, in a chat or on a schedule; the analyst charter (with the screens guide), the
        screen tools, and a warmer model so it thinks of new things to look at.
    """

    def __init__(self, model: BaseChatModel, mode: str = "work"):
        self.base_model = model
        self.mode = mode
        self.tools = CHAT_TOOLS if mode == "chat" else EVAL_TOOLS if mode == "eval" else ANALYST_TOOLS if mode == "analyst" else None
        self.charter = ASSISTANT_CHARTER if mode == "chat" else CHARTER
        if mode == "analyst":
            self.charter = f"{ANALYST_CHARTER}\n\n# The screens guide\n{guides.read('screens')}"
        # Steps are not what costs: page images are, and those are capped by the platform.
        self.max_steps = settings.MAX_STEPS
        self._bound: Dict[tuple, BaseChatModel] = {}

    def _model_for(self, tools: Sequence[str]) -> BaseChatModel:
        """The model with these tools bound (cached per toolset; runs of several workspaces share this agent)."""
        key = tuple(tools)
        if key not in self._bound:
            self._bound[key] = self.base_model.bind_tools(tool_schemas(key))
        return self._bound[key]

    async def run(self, ctx: RunContext, telemetry: Telemetry, history: Optional[List[BaseMessage]] = None) -> RunResult:
        """`history`: a saved checkpoint to continue from (a conversation the agent opened)."""
        result = RunResult(status="COMPLETED", final_thought="")
        tools = self.tools if self.tools is not None else work_tools(ctx.event)
        model = self._model_for(tools)
        deadline = time.monotonic() + settings.MAX_RUN_SECONDS
        reminders = 0
        last_thought = ""
        chatting = ctx.event.type == "human.message" and self.mode in ("chat", "analyst")
        briefing = await (build_chat_briefing(ctx) if chatting else build_briefing(ctx))
        messages: List[BaseMessage] = [SystemMessage(content=self.charter), *(history or []), HumanMessage(content=briefing)]
        result.messages = messages
        await telemetry.emit("processing_started", {"trigger": ctx.event.type, "status": "RUNNING", "origin": ctx.event.actor})

        max_steps = self.max_steps
        for step in range(1, max_steps + 1):
            if time.monotonic() > deadline:
                result.status = "BUDGET_EXCEEDED"
                result.final_thought = f"Stopped: run exceeded {settings.MAX_RUN_SECONDS}s."
                break
            if await stopped(ctx):
                result.status, result.final_thought = "CANCELLED", "Stopped by a person."
                break
            _prune_images(messages, settings.MAX_IMAGES_IN_CONTEXT)
            _trim_tool_results(messages, settings.FULL_TOOL_RESULTS, settings.OLD_TOOL_RESULT_CHARS)
            step_started = datetime.now(timezone.utc)
            try:
                response: AIMessage = await self._invoke(model, messages, deadline, ctx)
            except RunStopped:
                result.status, result.final_thought = "CANCELLED", "Stopped by a person."
                break
            messages.append(response)
            result.model_calls += 1
            for key, value in (getattr(response, "usage_metadata", None) or {}).items():
                if isinstance(value, int):
                    result.usage[key] = result.usage.get(key, 0) + value

            thought = message_text(response)
            if thought:
                last_thought = thought
                await telemetry.thought(step, thought)
            record = {"step_number": step, "step_started_at": step_started.isoformat(), "thought": thought, "tool_calls": []}
            result.steps.append(record)

            if not response.tool_calls:
                record["step_completed_at"] = datetime.now(timezone.utc).isoformat()
                if not thought:
                    messages.pop()  # an empty turn adds nothing to the conversation
                owed = open_obligations(ctx)
                if owed and reminders < settings.MAX_OUTCOME_REMINDERS:
                    reminders += 1
                    messages.append(HumanMessage(content="You stopped, but " + "; ".join(owed) + ". Continue."))
                    continue
                result.final_thought = thought or last_thought
                break

            images = []
            for call in response.tool_calls:
                await telemetry.tool_called(step, call["name"], call.get("args") or {})
                t0 = time.perf_counter()
                outcome = await execute(ctx, call["name"], call.get("args"), tools)
                duration_ms = round((time.perf_counter() - t0) * 1000, 1)
                messages.append(ToolMessage(content=outcome.text, tool_call_id=call["id"], name=call["name"]))
                images.extend(outcome.images)
                status = "error" if outcome.text.startswith(("ERROR", "REFUSED")) else "success"
                record["tool_calls"].append({
                    "tool_call_id": call["id"], "tool_name": call["name"], "arguments": call.get("args") or {},
                    "observation": outcome.text[:4000], "duration_ms": duration_ms, "status": status,
                })
                await telemetry.tool_result(step, call["name"], outcome.text)
            if images:
                content: List[Dict[str, Any]] = [{"type": "text", "text": "Requested image(s):"}]
                for img in images:
                    content.append({"type": "text", "text": img["caption"]})
                    content.append({"type": "image_url", "image_url": {"url": f"data:{img['mime']};base64,{img['b64']}"}})
                messages.append(HumanMessage(content=content))
            record["step_completed_at"] = datetime.now(timezone.utc).isoformat()
            await asyncio.sleep(0)
        else:
            result.status = "BUDGET_EXCEEDED"
            result.final_thought = f"Stopped: reached the limit of {max_steps} steps."

        result.completed_at = datetime.now(timezone.utc)
        await telemetry.emit("agent_run_finished", {"status": result.status, "summary": result.final_thought})
        return result

    async def _call(self, model: BaseChatModel, messages: List[BaseMessage], timeout: float, ctx: Optional[RunContext]) -> AIMessage:
        """One model call. While answering a chat message it also watches for a stop, so the
        person doesn't wait for the model to finish thinking."""
        task = asyncio.create_task(model.ainvoke(messages))
        watch = ctx is not None and ctx.event.type == "human.message"
        end = time.monotonic() + timeout
        try:
            while True:
                left = end - time.monotonic()
                if left <= 0:
                    raise asyncio.TimeoutError
                done, _ = await asyncio.wait({task}, timeout=min(left, STOP_POLL_SECONDS) if watch else left)
                if done:
                    return task.result()
                if watch and await stopped(ctx):
                    raise RunStopped()
        finally:
            if not task.done():
                task.cancel()

    async def _invoke(self, model: BaseChatModel, messages: List[BaseMessage], deadline: float,
                      ctx: Optional[RunContext] = None) -> AIMessage:
        """
        Calls the model with a per-call timeout. Rate limits and hung calls are waited out
        and retried within the same run, as long as the run's deadline allows.
        """
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 5:
                raise TimeoutError("Run deadline reached while waiting for the model.")
            try:
                return await self._call(model, messages, min(settings.MODEL_CALL_TIMEOUT_SECONDS, remaining), ctx)
            except asyncio.TimeoutError:
                logger.warning("Model call timed out; retrying")
                continue
            except Exception as exc:
                delay = _rate_limit_delay(exc)
                if delay is None or time.monotonic() + delay > deadline:
                    raise
                logger.warning("Model rate-limited; waiting %.0fs and continuing the run", delay)
                await asyncio.sleep(delay)
