"""Involving people: flags (quick decisions), conversations about issues, and chat replies."""
from typing import List, Optional
from pydantic import BaseModel, Field
from agentic_framework.agent.tools.base import RunContext, ToolOutcome, dump, tool


class DataTableArgs(BaseModel):
    title: Optional[str] = Field(None, description="What the table shows, e.g. 'Rows on this page'")
    columns: List[str] = Field(..., description="Column names")
    rows: List[List[str]] = Field(..., description="One list of cell values per row, in column order")


class FlagArgs(BaseModel):
    question: str = Field(..., description="One precise, short question, for the person who answers it")
    context: str = Field(..., description="For the person who answers, in a sentence or two: what you saw and what you think, "
                         "naming documents by their name. Not ids, file names, or the policies you follow; your own plan goes "
                         "in resume_note")
    answer_type: str = Field("single", description="single (pick one option), multiple (pick any) or text (a written answer)")
    options: List[str] = Field(default_factory=list, description="The choices, for single or multiple; 'Other' (their own "
                               "answer) is always added for them")
    evidence_document_ids: List[str] = Field(default_factory=list)
    data_table: Optional[DataTableArgs] = Field(None, description="The data you read that the question is about, as a table "
                                                "(for example the rows of a table on the page): a few short columns and one list "
                                                "of cells per row. Always include it when the question is about what "
                                                "you extracted, so the person sees rows, not a paragraph")
    target_role: str = Field("admin", description="admin (business meaning, template) or reviewer (reading/identity)")
    priority: str = Field("normal", description="low, normal, or high (work is blocked)")
    waiting_document_ids: List[str] = Field(default_factory=list, description="Documents to park until the answer arrives")
    resume_note: str = Field("", description="What you will do with the answer (for when you wake up again)")


@tool("flag", "Flag a quick decision for a person: one question settled in one answer (choose the answer type). "
      "Optionally park documents until they answer; you are woken with the answer. For an issue that needs "
      "discussion, use open_conversation instead.", FlagArgs)
async def flag(ctx: RunContext, a: FlagArgs) -> ToolOutcome:
    body = {
        "question": a.question, "context": a.context, "options": a.options, "answer_type": a.answer_type,
        "evidence_document_ids": a.evidence_document_ids, "data_table": a.data_table.model_dump() if a.data_table else None,
        "target_role": a.target_role, "priority": a.priority,
        "related_document_id": a.waiting_document_ids[0] if a.waiting_document_ids else None,
        "channel_id": ctx.event.payload.get("channel_id"),
    }
    q = await ctx.api.post("/questions", body)
    if q.get("conversation_id") and not q.get("duplicate"):  # its thread keeps this run's state for the answer
        ctx.opened_conversations.append(q["conversation_id"])
    if a.waiting_document_ids:
        for doc_id in a.waiting_document_ids:
            await ctx.api.post(f"/documents/{doc_id}/status", {"status": "AWAITING_INPUT", "message": a.question})
            ctx.parked_documents.add(doc_id)
        await ctx.memory.add(
            ctx.tenant_id, ctx.template_id, "work_item",
            f"Waiting for answer to: {a.question}. Then: {a.resume_note or 'finish the parked documents.'}",
            data={"question_id": q["id"], "document_ids": a.waiting_document_ids},
        )
    return ToolOutcome(dump(q), touched_document_ids=a.waiting_document_ids)


class OpenConversationArgs(BaseModel):
    title: str = Field(..., description="The issue in a few words, e.g. 'Unlabelled column on some pages'")
    message: str = Field(..., description="Your opening message, written for the people who will read it: what you found "
                         "(documents by their names, never ids), why it matters, what you need from them. Markdown allowed")
    audience: str = Field("admin", description="admin (business meaning, template) or reviewer (reading, identities)")
    document_ids: List[str] = Field(default_factory=list, description="Documents it is about")
    priority: str = Field("normal", description="low, normal, or high (work is blocked)")
    waiting_document_ids: List[str] = Field(default_factory=list, description="Documents to park until it is settled")
    resume_note: str = Field("", description="What you will do once it is settled")


@tool("open_conversation", "Open a conversation about an issue that needs discussion with the people who can settle it. "
      "It appears in their agent inbox; you are woken with each reply and continue where you left off. For one quick "
      "decision, flag instead.", OpenConversationArgs)
async def open_conversation(ctx: RunContext, a: OpenConversationArgs) -> ToolOutcome:
    conv = await ctx.api.post("/conversations", {
        "title": a.title, "message": a.message, "audience": a.audience, "priority": a.priority,
        "document_ids": list(dict.fromkeys(a.document_ids + a.waiting_document_ids)),
    })
    if not conv.get("already_open"):
        ctx.opened_conversations.append(conv["id"])
    for doc_id in a.waiting_document_ids:
        await ctx.api.post(f"/documents/{doc_id}/status", {"status": "AWAITING_INPUT", "message": a.title})
        ctx.parked_documents.add(doc_id)
    if a.waiting_document_ids:
        await ctx.memory.add(
            ctx.tenant_id, ctx.template_id, "work_item",
            f"Waiting on conversation '{a.title}'. Then: {a.resume_note or 'finish the parked documents.'}",
            data={"conversation_id": conv["id"], "document_ids": a.waiting_document_ids},
        )
    return ToolOutcome(dump(conv), touched_document_ids=a.waiting_document_ids)


class ResolveConversationArgs(BaseModel):
    conversation_id: str
    resolution: str = Field(..., description="What was decided or done, in one or two sentences")


@tool("resolve_conversation", "Close a conversation you opened once the issue is settled and you have acted on it. "
      "When someone wrote to you in it, reply_to_user first: the platform refuses to close it before your answer.",
      ResolveConversationArgs)
async def resolve_conversation(ctx: RunContext, a: ResolveConversationArgs) -> ToolOutcome:
    result = await ctx.api.post(f"/conversations/{a.conversation_id}/resolve", {"resolution": a.resolution})
    ctx.resolved_conversations.add(a.conversation_id)
    return ToolOutcome(dump(result))


class ReplyArgs(BaseModel):
    content: str = Field(..., description="Your reply, written for the person (markdown allowed)")


@tool("reply_to_user", "Reply to the person who wrote to you, in the conversation they wrote in.", ReplyArgs)
async def reply_to_user(ctx: RunContext, a: ReplyArgs) -> ToolOutcome:
    conv_id = ctx.event.payload.get("conversation_id")
    if ctx.event.type not in ("human.message", "human.answered") or not conv_id:
        raise ValueError("Nobody wrote to you in this run, so there is no one to reply to.")
    body = {"content": a.content, "reply_to": ctx.event.payload.get("message_id"),
            "channel_id": ctx.event.payload.get("channel_id"), "actions": ctx.actions}
    result = await ctx.api.post(f"/conversations/{conv_id}/messages", body)
    ctx.replied = True
    return ToolOutcome(dump(result))
