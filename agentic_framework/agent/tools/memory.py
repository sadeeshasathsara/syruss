"""What the agent remembers (in the core server's memory) and its own work items."""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from agentic_framework.embeddings import embed_documents, embed_query
from agentic_framework.agent.tools.base import RunContext, ToolOutcome, dump, json_arg, tool


async def save_memory(ctx: RunContext, scope: str, kind: str, content: str, *, importance: float = 0.5,
                      document_ids: Optional[List[str]] = None, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Remembers in the core server, with an embedding when one can be made."""
    [vector] = await embed_documents([content])
    return await ctx.api.post("/memories", {
        "scope": scope, "kind": kind, "content": content, "importance": importance,
        "document_ids": document_ids or [], "data": data or {}, "embedding": vector,
    })


class RememberArgs(BaseModel):
    kind: str = Field(..., description="lesson (how this business works), observation (something noticed), "
                                       "fact (something true about the business or, with scope personal, the person), "
                                       "preference (how the person likes things; scope personal)")
    content: str = Field(..., description="Short, specific, reusable; one thing per memory")
    scope: str = Field("workspace", description="workspace (everyone here benefits) or personal (about the person you're "
                                                "chatting with; only used when talking with them)")
    importance: float = Field(0.5, description="0-1: how much this should shape future work")
    evidence_document_ids: List[str] = Field(default_factory=list)


@tool("remember", "Remember something. Workspace memories shape all your future work here; personal ones only your "
      "conversations with that person. Saying the same thing again strengthens the memory.", RememberArgs)
async def remember(ctx: RunContext, a: RememberArgs) -> ToolOutcome:
    return ToolOutcome(dump(await save_memory(ctx, a.scope, a.kind, a.content, importance=a.importance,
                                               document_ids=a.evidence_document_ids)))


class RecallArgs(BaseModel):
    query: str = Field(..., description="What you want to remember, in words")
    kinds: List[str] = Field(default_factory=list, description="lesson, observation, fact, preference, summary; empty for all")


@tool("recall", "Search what you remember, ranked by meaning, importance and recency. In a chat this includes what "
      "you remember about the person and earlier parts of the conversation.", RecallArgs)
async def recall(ctx: RunContext, a: RecallArgs) -> ToolOutcome:
    scopes = ["workspace", "personal", "conversation"] if ctx.event.type == "human.message" else ["workspace"]
    found = await ctx.api.post("/memories/search", {
        "query": a.query, "embedding": await embed_query(a.query), "scopes": scopes, "kinds": a.kinds or None, "limit": 10,
    })
    return ToolOutcome(dump(found))


class UpdateMemoryArgs(BaseModel):
    memory_id: str
    content: str = Field("", description="The corrected text")
    importance: Optional[float] = Field(None, description="0-1")
    archived: Optional[bool] = Field(None, description="true when it's no longer true or useful")


@tool("update_memory", "Correct, re-weigh or retire something you remember (it keeps its history in the audit log).", UpdateMemoryArgs)
async def update_memory(ctx: RunContext, a: UpdateMemoryArgs) -> ToolOutcome:
    body: Dict[str, Any] = {"importance": a.importance, "archived": a.archived}
    if a.content:
        body["content"] = a.content
        [body["embedding"]] = await embed_documents([a.content])
    return ToolOutcome(dump(await ctx.api.patch(f"/memories/{a.memory_id}", {k: v for k, v in body.items() if v is not None})))


class UpdateNoteArgs(BaseModel):
    note_id: str
    status: str = Field("", description="active, done, or archived")
    content: str = Field("", description="Replace the note's text")
    data_json: str = Field("", description="JSON object merged into the note's data (e.g. updated counts)")


@tool("update_note", "Update one of your work items (things you parked, waiting on an answer): close it, or note progress.", UpdateNoteArgs)
async def update_note(ctx: RunContext, a: UpdateNoteArgs) -> ToolOutcome:
    note = await ctx.memory.update(ctx.tenant_id, ctx.template_id, a.note_id, content=a.content or None,
                                   status=a.status or None, data=json_arg(a.data_json, "data_json", None))
    return ToolOutcome(dump(note or {"error": "note not found"}))


class SystemLessonArgs(BaseModel):
    content: str = Field(..., description="The lesson: how to work in this platform (its tools, refusals, commits, when to "
                         "ask). General enough for any workspace: never names, IDs, values or business facts")
    why: str = Field("", description="What happened that taught you this")


@tool("propose_system_lesson", "Propose a lesson about working in the platform itself, learned from something that went "
      "wrong or a refusal you had to work out. Business knowledge belongs in remember. It applies to every workspace once "
      "an admin approves it.", SystemLessonArgs)
async def propose_system_lesson(ctx: RunContext, a: SystemLessonArgs) -> ToolOutcome:
    return ToolOutcome(dump(await ctx.api.post("/system-lessons", {"content": a.content, "why": a.why})))
