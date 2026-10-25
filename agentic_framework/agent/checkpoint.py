"""
Checkpoints for conversations the agent opens.

When Syruss opens a conversation about an issue, the run's message history (what it was doing,
what it found, why it asked) is saved on the conversation in Postgres, through the core API. Each
reply in that conversation resumes from the checkpoint and saves a new one, until the
conversation is resolved and the core server drops it. This plays the role of a LangGraph
checkpointer for our loop.

What is kept: everything but the system prompt (which is always the current charter). Page images
are replaced by a note (they are large, and the record or a fresh look is better), long tool
results are cut, and only the most recent part of a long history is kept, starting at a model
turn so every tool call keeps its result.
"""
from typing import Any, Dict, List
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage, messages_from_dict, messages_to_dict

MAX_MESSAGES = 60
MAX_TOOL_TEXT = 2000
IMAGE_NOTE = "[a page image was here; look at the page again if you still need it]"


def _slim(msg: BaseMessage) -> BaseMessage:
    if isinstance(msg, HumanMessage) and isinstance(msg.content, list):
        parts = [{"type": "text", "text": IMAGE_NOTE} if isinstance(p, dict) and p.get("type") == "image_url" else p
                 for p in msg.content]
        return HumanMessage(content=parts)
    if isinstance(msg, ToolMessage) and isinstance(msg.content, str) and len(msg.content) > MAX_TOOL_TEXT:
        return ToolMessage(content=msg.content[:MAX_TOOL_TEXT] + " …[cut]", tool_call_id=msg.tool_call_id, name=msg.name)
    return msg


def dump(messages: List[BaseMessage]) -> List[Dict[str, Any]]:
    history = [_slim(m) for m in messages if not isinstance(m, SystemMessage)]
    if len(history) > MAX_MESSAGES:
        # Keep the original briefing, then the most recent turns from a model message onwards.
        tail = history[-(MAX_MESSAGES - 1):]
        while tail and not isinstance(tail[0], AIMessage):
            tail = tail[1:]
        first = history[0]
        note = "\n\n[Some of your earlier steps were left out to save space.]"
        if isinstance(first, HumanMessage) and isinstance(first.content, str):
            first = HumanMessage(content=first.content + note)
        history = [first] + tail
    return messages_to_dict(history)


def load(saved: List[Dict[str, Any]]) -> List[BaseMessage]:
    """The saved history, ending on a model turn so the next person's message follows it."""
    history = messages_from_dict(saved or [])
    if history and not isinstance(history[-1], AIMessage):
        history.append(AIMessage(content="(I stopped there.)"))
    return history
