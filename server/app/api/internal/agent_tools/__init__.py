"""
Agent Tool API: the only way the agent service reads or changes business data.

Every request carries a token minted for one (tenant, template) workspace, so the agent
can only touch the workspace that woke it. Authority is enforced here, not in the prompt:

  free                read anything in the workspace, view pages, validate a draft record,
                      ask humans, reply in chat, report traces
  free, audited       commit a record that passes the template, send a document to review,
                      create an entity, add an alias, update entity attributes
  needs approval      template changes, entity merges -> only as proposals; a human approves
  not possible        anything outside the workspace, deleting data

During an evaluation run (an `eval.item` event) the agent reads a golden document as if it were
new: its record is diverted to the evaluation and scored, the expected answers are hidden from
every read, and anything else that would change the workspace is refused.

scope.py checks every request's token; each other module is one area of the API.
"""
from fastapi import APIRouter
from app.api.internal.agent_tools import (
    conversations, documents, entities, memory, proposals, records, rules, screens, sources, traces, workspace,
)

router = APIRouter(tags=["Agent Tool API (internal)"])
router.include_router(workspace.router)
router.include_router(documents.router)
router.include_router(records.router)
router.include_router(entities.router)
router.include_router(conversations.router)
router.include_router(screens.router)
router.include_router(proposals.router)
router.include_router(sources.router)
router.include_router(memory.router)
router.include_router(traces.router)
router.include_router(rules.router)
