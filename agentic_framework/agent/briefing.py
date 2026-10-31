"""
The wake-up briefing: what happened, and what the agent already knows.

It states facts (the event, the workspace, memory). It does not tell the agent what to do.
"""
import json
from typing import Any, Dict, List
from agentic_framework.agent import guides
from agentic_framework.agent.tools import RunContext, budget_note
from agentic_framework.embeddings import embed_query

CHAT_HISTORY = 20  # earlier messages a chat run sees


def _j(value: Any) -> str:
    """Compact JSON: the briefing is resent with every model call, so spaces cost on every step."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _diff(before: Any, after: Any, path: str = "") -> List[str]:
    if isinstance(before, dict) and isinstance(after, dict):
        out: List[str] = []
        for k in sorted(set(before) | set(after)):
            out += _diff(before.get(k), after.get(k), f"{path}.{k}" if path else k)
        return out
    if isinstance(before, list) and isinstance(after, list):
        out = []
        for i in range(max(len(before), len(after))):
            out += _diff(before[i] if i < len(before) else None, after[i] if i < len(after) else None, f"{path}[{i}]")
        return out
    return [] if before == after else [f"{path}: {before!r} -> {after!r}"]


async def _reflection(ctx: RunContext, p: Dict[str, Any]) -> str:
    """What a reflection looks back over: activity, people's corrections, quality, and the upkeep done."""
    quality = await ctx.api.get("/quality", days=30)
    corrections = await ctx.api.get("/reviews/history", limit=20)
    since = p.get("since") or ""
    recent = [r for r in corrections if (r.get("decided_at") or "") >= since]
    changes = [{"document_id": r["document_id"], "status": r["status"], "notes": r.get("notes"),
                "changed": _diff(r.get("original_data") or {}, r.get("corrected_data") or {}) if r.get("corrected_data") else []}
               for r in recent]
    consolidated = p.get("consolidated") or {}
    return (f"Scheduled reflection over the work since {since or 'the start'}.\n"
            f"What happened (events by type): {_j(p.get('activity') or {})}\n"
            f"Review decisions since then ({len(changes)}): {_j(changes) if changes else 'none'}\n"
            f"Quality over the last 30 days: {_j({k: quality.get(k) for k in ('agent', 'reviews', 'fields', 'calibration', 'last_evaluation')})}\n"
            f"Memory upkeep the platform just did: retired {consolidated.get('retired', 0)} unused low-importance "
            f"observations and merged {consolidated.get('merged', 0)} duplicates "
            f"{_j(consolidated.get('merged_items') or [])}")


async def _what_happened(ctx: RunContext, workspace: Dict[str, Any]) -> str:
    e = ctx.event
    p = e.payload
    t = e.type
    if t == "document.submitted":
        doc = await ctx.api.get(f"/documents/{p['document_id']}")
        parked = await ctx.memory.work_items_for_document(e.tenant_id, e.template_id, p["document_id"])
        text = (f"Document {doc['id']} ('{doc['filename']}', image {doc['image_size']}) was submitted for processing. "
                "This run is for this document; other submitted documents wake you separately."
                f"\nCurrent record: {_j(doc['record']) if doc['record'] else 'none yet'}")
        if parked:
            text += f"\nYou had parked this document before: {_j(parked)}"
        return text
    if t == "template.samples_added":
        has_template = bool(workspace["template"].get("active_version"))
        return (
            f"The admin uploaded {len(p['sample_document_ids'])} sample document(s) to "
            f"{'refine this template' if has_template else 'design the template for this new workspace (it has no template yet)'}: "
            f"{p['sample_document_ids']}.\nAdmin's notes: {p.get('notes') or '(none)'}\n"
            "The admin is watching the template designer live and will review your proposal there."
        )
    if t == "knowledge.source_added":
        why = {
            "added": "added a data source",
            "changed": "changed a data source, and its data no longer fits the approved mapping",
            "remap": "asked you to map a data source again",
        }.get(p.get("reason"), "added a data source")
        return (f"The admin {why}: '{p.get('name')}' ({'an API' if p.get('kind') == 'API' else 'a file'}), "
                f"source {p['source_id']}.\nAdmin's notes: {p.get('notes') or '(none)'}")
    if t == "human.message":
        sender = p.get("sender") or {}
        who = sender.get("name") or "A member of the workspace"
        if sender.get("role"):
            who += f" ({sender['role']})"
        history = await ctx.api.get(f"/conversations/{p['conversation_id']}/messages", limit=CHAT_HISTORY)
        earlier = [m for m in history if m["id"] != p["message_id"]]
        transcript = "\n".join(
            f"{'You' if m['role'] == 'assistant' else 'Them'}: {m['content'][:600]}" for m in earlier
        ) or "(this is the start of the conversation)"
        if p.get("conversation_kind") in ("agent", "flag"):
            return (f"{who} replied in the conversation you opened, \"{p.get('conversation_title')}\" "
                    f"(conversation {p['conversation_id']}, message {p['message_id']}). Your earlier work on it is above.\n"
                    f"The conversation so far, oldest first:\n{transcript}\n\nTheir new message:\n\"{p['text']}\"\n"
                    "Reply with reply_to_user. When the issue is settled and you have acted on it, ask whether there is "
                    "anything else; once they say no (or ask you to close it), resolve_conversation.")
        return (f"{who} is chatting with you (conversation {p['conversation_id']}, message {p['message_id']}).\n"
                f"Conversation so far, oldest first:\n{transcript}\n\n"
                f"Their new message:\n\"{p['text']}\"\n"
                f"They were on this page when they wrote it (a hint, not a request): {p.get('page') or 'unknown'}"
                + (f"\nThey wrote it from the analytics screen '{(p.get('context') or {}).get('screen')}': changes they ask for are to that "
                   "screen (list_screens has its definition)." if (p.get("context") or {}).get("screen") else ""))
    if t == "human.answered":
        waiting = await ctx.memory.work_items_for_question(e.tenant_id, e.template_id, p["question_id"])
        verb = "dismissed" if p.get("dismissed") else "answered"
        return (f"A person {verb} your question {p['question_id']}.\nQuestion: {p['question']}\n"
                f"Your context then: {p.get('context')}\n{'Reason' if p.get('dismissed') else 'Answer'}: {p['answer']}\n"
                f"Work waiting on this answer: {_j(waiting) if waiting else 'none recorded'}"
                + (f"\nThe flag has its own conversation ({p['conversation_id']}), where their answer now is. When you've "
                   "acted on it, reply there (reply_to_user): say what you did, ask what you still need if anything "
                   "is unclear, and resolve_conversation when nothing is left." if p.get("conversation_id") and not p.get("dismissed") else ""))
    if t == "review.completed":
        changes = _diff(p.get("original_data") or {}, p.get("corrected_data") or {}) if p.get("corrected_data") else []
        return (f"A reviewer finished review {p['review_id']} of document {p['document_id']}: {p['action']}.\n"
                f"You had sent it to review because: {p.get('reason')}\n"
                f"Fields they changed: {_j(changes) if changes else 'none'}\nReviewer notes: {p.get('notes') or '(none)'}")
    if t == "proposal.decided":
        return (f"Your proposal {p['proposal_id']} ({p['proposal_type']}) was {p['decision']}."
                f"\nNotes: {p.get('notes') or '(none)'}\nDetails: {_j({k: v for k, v in p.items() if k not in ('proposal_id', 'proposal_type', 'decision', 'notes')})}")
    if t == "schedule.reflect":
        return await _reflection(ctx, p)
    if t == "schedule.insights":
        return ("Scheduled look at the records, nobody asked a question. Study what has been saved (query_records, preview_screen) and the "
                "screens that already exist (list_screens), and suggest up to three NEW analytics screens worth having with save_screen, each "
                "with a rationale that says what you noticed and why it matters. If nothing new is worth suggesting, suggest nothing and say "
                f"so in your last thought. Records saved since the last look: {_j(p.get('activity') or {})}")
    if t == "eval.item":
        doc = await ctx.api.get(f"/documents/{p['document_id']}")
        return (f"Evaluation run: read document {doc['id']} ('{doc['filename']}', image {doc['image_size']}) as if it had "
                "just been submitted, and submit its record. People verified this document; your record is scored "
                "against theirs and nothing in the workspace changes. This run is for this document only. "
                "You can't ask people, register anything or write memory now: where you would normally ask, "
                "send it to review with your reason.")
    return f"Event {t}: {_j(p)}"


async def _system_lessons(ctx: RunContext) -> str:
    """Syruss's own lessons about working in the platform (every workspace)."""
    lessons = await ctx.api.get("/system-lessons")
    if not lessons:
        return ""
    return "# How to work in this platform (your lessons)\n" + "\n".join(f"- {l['content']}" for l in lessons) + "\n\n"


async def build_chat_briefing(ctx: RunContext) -> str:
    """What a chat run needs: the conversation, who it is with, and a short picture of the workspace."""
    e = ctx.event
    workspace = await ctx.api.get("/workspace")
    message = e.payload.get("text") or ""
    vector = await embed_query(message)
    about_them = await ctx.api.post("/memories/search", {"query": message, "embedding": vector, "scopes": ["personal"], "limit": 8})
    relevant = await ctx.api.post("/memories/search", {"query": message, "embedding": vector, "scopes": ["workspace"], "limit": 8})
    summary = (await ctx.api.get(f"/conversations/{e.payload['conversation_id']}/summary")).get("content")
    template = workspace.get("template") or {}
    objects = (template.get("schema_definition") or {}).get("objects") or []
    shape = "; ".join(
        f"{o.get('type')} ({', '.join(f if isinstance(f, str) else f.get('name', '') for f in o.get('fields', []))})" for o in objects
    ) or "not designed yet"
    standing = ((workspace.get("workspace_policy") or {}).get("instructions") or "").strip()
    pages = budget_note(await ctx.api.get("/page-budget"))
    return f"""{await _system_lessons(ctx)}# The conversation
{f"Earlier in this chat (summary): {summary}" + chr(10) if summary else ""}{await _what_happened(ctx, workspace)}

# What you remember about them (only for talking with them)
{chr(10).join(f"- {m['content']}" for m in about_them) or "(nothing yet: when they tell you how they like things or about themselves, remember it with scope personal)"}

# The workspace
Name: {template.get('name')}. What it captures: {shape}.
Documents by status: {_j(workspace.get('documents_by_status') or {})}
Waiting on people: {len(workspace.get('open_questions') or [])} question(s) from you, {len(workspace.get('pending_proposals') or [])} proposal(s).
Standing instructions from the admins: {standing or "(none)"}

# What you remember about this workspace that may matter here
{chr(10).join(f"- [{m['kind']}] {m['content']}" for m in relevant) or "(nothing relevant)"}

# Looking at pages
Every document that was read is in the database: search_past_records and get_document give you what \
was read and checked. Answer from them. A page image costs far more than a lookup, and it is not \
needed to report what a document says, count, list or compare records.
{pages}
"""


async def build_briefing(ctx: RunContext) -> str:
    e = ctx.event
    workspace = await ctx.api.get("/workspace")
    lessons = await ctx.api.post("/memories/search", {"kinds": ["lesson", "fact"], "limit": 40})
    observations = await ctx.api.post("/memories/search", {"kinds": ["observation"], "limit": 15})
    work_items = await ctx.memory.list(e.tenant_id, e.template_id, "work_item", limit=15)

    def notes(items: List[Dict[str, Any]]) -> str:
        if not items:
            return "(none)"
        return "\n".join(f"- [{n['id']}] {n['content']}" + (f"  data={_j(n['data'])}" if n["data"] else "") for n in items)

    policy = workspace.get("workspace_policy") or {}
    rules = [f"Commit only records you are at least {policy.get('commit_only_when_at_least', 0):.0%} sure of; send the rest to review."]
    if policy.get("may_register_entities") is False:
        rules.append("Do not register new entities yourself; ask a person.")
    if policy.get("may_learn_aliases") is False:
        rules.append("Do not add spellings (aliases) yourself; ask a person.")
    standing = (policy.get("instructions") or "").strip()

    return f"""{await _system_lessons(ctx)}# What happened
{await _what_happened(ctx, workspace)}

# Workspace policy (set by its admins; the platform enforces it)
{chr(10).join(f"- {r}" for r in rules)}
- {budget_note(await ctx.api.get("/page-budget")) or "Look at pages only when you must."}
Standing instructions from the admins: {standing or "(none)"}

# Workspace
Template: {_j(workspace['template'])}
Documents by status: {_j(workspace['documents_by_status'])}
Known entity types: {_j(workspace['entity_types'])}
Your open questions (flags): {_j(workspace['open_questions'])}
Your open conversations: {_j(workspace.get('open_conversations') or [])}
Your pending proposals: {_j(workspace['pending_proposals'])}

# Your memory
Lessons:
{notes(lessons)}

Recent observations:
{notes(observations)}

Open work items:
{notes(work_items)}
{_task_guide(ctx)}"""


def _task_guide(ctx: RunContext) -> str:
    """The guide this kind of task always needs, so the agent doesn't spend a step reading it."""
    name = guides.FOR_TASK.get(ctx.event.type)
    if not name:
        return ""
    ctx.guides_read.add(name)
    return f"\n# Guide for this task ({name})\n{guides.read(name)}\n"
