"""
Who is calling. Every request carries an agent token for one workspace and the event (run) that woke the agent;
during an evaluation, or in a person's private chat, what the run may do is narrowed here.
"""
import re
from typing import Optional
from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.agent_token import AgentScope, verify_agent_token
from app.core.config import settings
from app.core.database import get_db_session
from app.core.permissions import permission_sentence
from app.core.scope import membership
from app.models.outbox import AgentEventOutbox
from app.models.template import DocumentTemplate
from app.models.evaluation import EvalRunItem
from app.services import eval_service


class Workspace:
    def __init__(self, scope: AgentScope, template: DocumentTemplate, db: AsyncSession,
                 eval_item: Optional[EvalRunItem] = None, hidden: Optional[set] = None):
        self.tenant_id = scope.tenant_id
        self.template_id = scope.template_id
        self.event_id = scope.event_id
        self.template = template
        self.db = db
        self.eval_item = eval_item          # set while the agent works on an evaluation run
        self.hidden = hidden or set()       # documents whose records are this evaluation's answers


# What the agent may still call while evaluating (everything else that writes is refused).
EVAL_WRITES = re.compile(r"/(documents/[^/]+/record|documents/[^/]+/status|documents/[^/]+/pages|records/validate|records/query|memories/search|traces(/[^/]+)?|evals/outcome)$")


# In a person's private chat the agent acts as that person: what they may do bounds every call it makes
# (checked now, so removing them or changing their role stops the run's work at once).
CHAT_POLICY = [
    (re.compile(r"^/(documents(/|$)|records/|reviews/|quality|questions$)"), {"GET"}, "documents.view"),
    (re.compile(r"^/records/(validate|query)$"), {"POST"}, "documents.view"),
    (re.compile(r"^/documents/[^/]+/record$"), {"POST"}, "documents.review"),
    (re.compile(r"^/documents/[^/]+/(status|pages)$"), {"POST"}, "documents.upload"),
    (re.compile(r"^/screens(/|$)"), {"GET"}, "documents.view"),
    (re.compile(r"^/rules(/check)?$"), {"GET", "POST"}, "documents.view"),
    (re.compile(r"^/screens/(preview|[^/]+/run)$"), {"POST"}, "documents.view"),
    (re.compile(r"^/screens(?:/(?!preview$)[^/]+)?$"), {"POST", "PATCH", "DELETE"}, "screens.manage"),
    (re.compile(r"^/(entities/search|graph/|sources/[^/]+$|proposals$)"), {"GET"}, "knowledge.view"),
    (re.compile(r"^/(entities|sources/[^/]+/mapping)"), {"POST", "PATCH", "PUT"}, "knowledge.manage"),
]


async def _private_chat_guard(request: Request, db: AsyncSession, event: AgentEventOutbox) -> None:
    actor = event.actor or {}
    if event.event_type != "human.message" or event.payload.get("conversation_kind") != "chat" or actor.get("type") != "user":
        return
    found = await membership(db, event.template_id, actor.get("id") or "")
    if found is None:
        raise HTTPException(status_code=403, detail="The person you are chatting with no longer has access to this workspace.")
    path = request.url.path.removeprefix(settings.AGENT_API_PREFIX)
    thread = re.match(r"^/conversations/([^/]+)", path)
    if thread and thread.group(1) != event.payload.get("conversation_id"):
        raise HTTPException(status_code=403, detail="In a private chat you can only use that chat's own conversation.")
    for pattern, methods, permission in CHAT_POLICY:
        if pattern.match(path) and request.method in methods and permission not in (found[1].permissions or []):
            raise HTTPException(status_code=403, detail=(
                f"{actor.get('name') or 'This person'}'s role ({found[1].name}) can't {permission_sentence(permission)}, "
                "so you can't do it for them."))


async def get_workspace(
    request: Request,
    authorization: str = Header(..., description="Bearer <agent token>"),
    db: AsyncSession = Depends(get_db_session),
) -> Workspace:
    token = authorization.removeprefix("Bearer ").strip()
    try:
        scope = verify_agent_token(token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid agent token: {exc}")
    template = await db.get(DocumentTemplate, scope.template_id)
    if not template or template.tenant_id != scope.tenant_id:
        raise HTTPException(status_code=404, detail="Workspace no longer exists.")
    event = await db.get(AgentEventOutbox, scope.event_id)
    if event is not None:
        if (event.tenant_id, event.template_id) != (scope.tenant_id, scope.template_id):
            raise HTTPException(status_code=401, detail="Invalid agent token: it does not match its event.")
        await _private_chat_guard(request, db, event)
    item = await eval_service.item_for_event(db, scope.event_id)
    if item is None:
        return Workspace(scope, template, db)
    if request.method != "GET" and not EVAL_WRITES.search(request.url.path):
        raise HTTPException(status_code=403, detail=(
            "Not during an evaluation: this run only measures how you read the document, so it can't change "
            "the workspace (no entities, aliases, questions, memories, proposals or messages). Submit your record."))
    return Workspace(scope, template, db, eval_item=item, hidden=await eval_service.run_documents(db, item.run_id))
