"""The workspace and its documents: what is there, saved records, and (within the budget) the pages."""
import base64
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
from agentic_framework.agent.tools.base import NoArgs, RunContext, ToolOutcome, dump, tool
from agentic_framework.core_api import ToolRefused


@tool("get_workspace", "The workspace's active template, document counts by status, known entity types, "
      "open questions you asked, and pending proposals.", NoArgs)
async def get_workspace(ctx: RunContext, _: NoArgs) -> ToolOutcome:
    return ToolOutcome(dump(await ctx.api.get("/workspace")))


class ListDocumentsArgs(BaseModel):
    status: str = Field("", description="PENDING, QUEUED, PROCESSING, AWAITING_INPUT, COMPLETED, NEEDS_REVIEW, FAILED; empty for all")
    role: str = Field("DOCUMENT", description="DOCUMENT or SAMPLE")
    limit: int = Field(20, description="Max results (<=100)")


@tool("list_documents", "List documents (or template samples) in this workspace.", ListDocumentsArgs)
async def list_documents(ctx: RunContext, a: ListDocumentsArgs) -> ToolOutcome:
    return ToolOutcome(dump(await ctx.api.get("/documents", status=a.status or None, role=a.role, limit=a.limit)))


class DocumentArgs(BaseModel):
    document_id: str


@tool("get_document", "A document's metadata, its record as already read and checked (if any) and its review state. "
      "For a document that was already read, this is the answer: use it instead of looking at the page again.", DocumentArgs)
async def get_document(ctx: RunContext, a: DocumentArgs) -> ToolOutcome:
    return ToolOutcome(dump(await ctx.api.get(f"/documents/{a.document_id}")))


class ViewArgs(BaseModel):
    document_id: str
    region: List[float] = Field(default_factory=list, description="Optional zoom box [ymin, xmin, ymax, xmax] on a 0-1000 scale; empty for the full page")
    purpose: str = Field("", description="What you are looking for (shown to users)")


@tool("view_document", "Look at a page, or zoom into a region of it; the image is shown to you right after this call. "
      "Costly, and capped by the platform: use it for the document you are reading now. A document that was already read "
      "has its record in the database (get_document, search_past_records); look at its page again only when the record "
      "cannot answer and the page itself must be checked. Zooming into a page you already opened in this run is free.", ViewArgs)
async def view_document(ctx: RunContext, a: ViewArgs) -> ToolOutcome:
    region = None
    if a.region:
        if len(a.region) != 4:
            raise ValueError("region must have 4 numbers: [ymin, xmin, ymax, xmax]")
        region = ",".join(str(round(v, 1)) for v in a.region)
    image, budget = await ctx.api.get_image(a.document_id, region)
    caption = f"document {a.document_id}" + (f", region {a.region}" if region else ", full page")
    return ToolOutcome(
        f"Image of {caption} follows. {budget_note(budget)}".strip(),
        images=[{"caption": caption, "b64": base64.b64encode(image).decode(), "mime": "image/jpeg"}],
    )


def budget_note(budget: Optional[Dict[str, Any]]) -> str:
    """The page budget in words, for the agent: what is left, and why to keep it."""
    if not budget:
        return ""
    if budget.get("kind") == "chat":
        message, today = budget["this_message"], budget["person_today"]
        return (f"Page budget: {message['left']} of {message['limit']} pages left for this message, {today['left']} of "
                f"{today['limit']} left today for this person. They may ask you more today, and every page you open now "
                "is one fewer for later: open a page only when the records can't answer.")
    past = budget["already_read_this_run"]
    return (f"Page budget: {past['left']} of {past['limit']} already-read pages left in this run (the document you are "
            "working on, and documents not read yet, are free). Compare with past documents through their records.")


class FailArgs(BaseModel):
    document_id: str
    reason: str


@tool("mark_document_failed", "Mark a document as impossible to process (wrong document type, blank, unreadable as a whole).", FailArgs)
async def mark_document_failed(ctx: RunContext, a: FailArgs) -> ToolOutcome:
    result = await ctx.api.post(f"/documents/{a.document_id}/status", {"status": "FAILED", "message": a.reason})
    ctx.resolved_documents.add(a.document_id)
    return ToolOutcome(dump(result), touched_document_ids=[a.document_id])


# --- work for the person you're chatting with ----------------------------------

class ProcessArgs(BaseModel):
    document_ids: List[str] = Field(..., description="Documents to read (or read again)")
    reason: str = Field("", description="What they asked for, in a few words")


@tool("process_documents", "Start reading documents because the person you're chatting with asked for it. "
      "It runs as their request and its progress shows in their chat.", ProcessArgs)
async def process_documents(ctx: RunContext, a: ProcessArgs) -> ToolOutcome:
    results = []
    for doc_id in a.document_ids[:20]:
        try:
            results.append(await ctx.api.post(f"/documents/{doc_id}/process", {"reason": a.reason}))
        except ToolRefused as exc:
            results.append({"document_id": doc_id, "refused": str(exc)})
    return ToolOutcome(dump(results))
