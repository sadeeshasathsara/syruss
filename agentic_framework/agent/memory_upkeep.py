"""
Housekeeping around a run, never part of the agent's own reasoning:
  * notes the agent kept in its own database before memory moved to the core server are moved
    there (once, the first time it works in that workspace again);
  * memories whose text changed (for example edited by a person) get a fresh embedding;
  * a chat's older messages are folded into its rolling summary, so long chats stay coherent
    without sending their whole history every time.
"""
import logging
from datetime import datetime
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from agentic_framework.agent.briefing import CHAT_HISTORY
from agentic_framework.agent.loop import message_text
from agentic_framework.agent.tools import RunContext
from agentic_framework.config import settings
from agentic_framework.embeddings import embed_documents

logger = logging.getLogger("agent.memory_upkeep")

SUMMARY_PROMPT = """\
You keep the running summary of one chat between Syruss (an AI colleague in a document-processing \
workspace) and a member of the team. Update the summary with the new messages: keep what was asked, \
decided, promised and learned about the person or the business; drop small talk. Plain sentences, at \
most 200 words. Reply with the summary only."""


async def before_run(ctx: RunContext) -> None:
    await _move_older_notes(ctx)
    await _embed_backlog(ctx)


async def _move_older_notes(ctx: RunContext) -> None:
    notes = [
        *await ctx.memory.list(ctx.tenant_id, ctx.template_id, "lesson", limit=500),
        *await ctx.memory.list(ctx.tenant_id, ctx.template_id, "observation", limit=500),
    ]
    if not notes:
        return
    await ctx.api.post("/memories/import", {"notes": [{"kind": n["kind"], "content": n["content"], "data": n["data"]} for n in notes]})
    for n in notes:
        await ctx.memory.update(ctx.tenant_id, ctx.template_id, n["id"], status="moved")
    logger.info("Moved %d older notes of workspace %s into its memory", len(notes), ctx.template_id)


async def _embed_backlog(ctx: RunContext, limit: int = 20) -> None:
    queue = await ctx.api.get("/memories/embedding-queue", limit=limit)
    if not queue:
        return
    vectors = await embed_documents([q["content"] for q in queue])
    for item, vector in zip(queue, vectors):
        if vector:
            await ctx.api.put(f"/memories/{item['id']}/embedding", {"embedding": vector})


def _when(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


async def summarize_conversation(ctx: RunContext, model: BaseChatModel) -> None:
    """Folds the messages that have scrolled out of the chat's window into its summary."""
    conversation_id = ctx.event.payload.get("conversation_id")
    if not conversation_id:
        return
    history = await ctx.api.get(f"/conversations/{conversation_id}/messages", limit=100)
    older = history[:-CHAT_HISTORY] if len(history) > CHAT_HISTORY else []
    current = await ctx.api.get(f"/conversations/{conversation_id}/summary")
    covered = _when(current["covers_until"]) if current.get("covers_until") else None
    fresh = [m for m in older if covered is None or _when(str(m["at"])) > covered]
    if len(fresh) < settings.SUMMARY_BATCH:
        return
    transcript = "\n".join(f"{'Syruss' if m['role'] == 'assistant' else 'Them'}: {m['content'][:800]}" for m in fresh)
    response = await model.ainvoke([
        SystemMessage(content=SUMMARY_PROMPT),
        HumanMessage(content=f"Summary so far:\n{current.get('content') or '(none yet)'}\n\nNew messages:\n{transcript}"),
    ])
    summary = message_text(response)
    if not summary:
        return
    [vector] = await embed_documents([summary])
    await ctx.api.put(f"/conversations/{conversation_id}/summary",
                      {"content": summary, "covers_until": str(fresh[-1]["at"]), "embedding": vector})
