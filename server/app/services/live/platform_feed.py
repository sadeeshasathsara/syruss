"""
The platform's live view, for the system admin app: what every agent run and every document is doing, in every
organization, as it happens.

Two levels, so staff see what is going on without reading customers' data:

  ops              facts only, from every organization: a run started, its step and tool, how it ended and what it
                   cost; a document arrived, moved to a stage, was saved. No thoughts, tool inputs or results, record
                   values, chat text or names that come from the data.
  run:{run_id}     every event of one run as it happens, its thoughts and tool results included
  document:{id}    every event about one document

Content listeners (run, document) are opened only with an access grant to the organization (sysadmin/support_access).

Every server process sees every event (kafka_ws_bridge.deliver), so each one serves the listeners connected to it.
A listener's queue is bounded: a slow one loses its oldest events and is told how many, it never holds the platform up.
"""
import asyncio
import itertools
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Set

logger = logging.getLogger("core.platform_feed")

OPS = "platform:ops"
QUEUE_MAX = 500

# Events the server sends on the workspace channel and, for some, again on another one: their fact is taken once,
# from the workspace channel.
WORKSPACE_EVENTS = {"extraction_log", "intake_received", "delivery_changed", "agent_question", "agent_conversation", "agent_proposal",
                    "rule_changed", "rule_ideas", "screen_changed"}

_seq = itertools.count(1)


class Listener:
    def __init__(self, key: str, tenants: Optional[Set[str]] = None, workspaces: Optional[Set[str]] = None,
                 kinds: Optional[Set[str]] = None, only: Optional[Dict[str, str]] = None):
        """`only`: fields an event must carry with these values, e.g. {"run_id": ...} (one run's facts)."""
        self.key = key
        self.tenants, self.workspaces, self.kinds = tenants or set(), workspaces or set(), kinds or set()
        self.only = only or {}
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
        self.missed = 0

    def wants(self, event: Dict[str, Any]) -> bool:
        if self.tenants and event.get("tenant_id") not in self.tenants:
            return False
        if self.workspaces and event.get("template_id") not in self.workspaces:
            return False
        if any(event.get(k) != v for k, v in self.only.items()):
            return False
        kind = event.get("kind") or ""
        return not self.kinds or any(kind == k or kind.startswith(f"{k}.") for k in self.kinds)

    def offer(self, event: Dict[str, Any]) -> None:
        if self.queue.full():
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            self.missed += 1
        self.queue.put_nowait(event)

    def take_missed(self) -> int:
        missed, self.missed = self.missed, 0
        return missed


_listeners: Dict[str, Set[Listener]] = {}


def listen(key: str, **filters) -> Listener:
    """`key`: "ops", "run:{run_id}" or "document:{id}"."""
    listener = Listener(key, **filters)
    _listeners.setdefault(key, set()).add(listener)
    return listener


def stop(listener: Listener) -> None:
    group = _listeners.get(listener.key)
    if group is not None:
        group.discard(listener)
        if not group:
            del _listeners[listener.key]


def listening() -> bool:
    return bool(_listeners)


def _send(key: str, event: Dict[str, Any]) -> None:
    for listener in list(_listeners.get(key, ())):
        if listener.wants(event):
            listener.offer(event)


def _fact(kind: str, payload: Dict[str, Any], **fields) -> Dict[str, Any]:
    return {"seq": next(_seq), "kind": kind, "at": payload.get("timestamp") or datetime.now(timezone.utc).isoformat(),
            "tenant_id": payload.get("tenant_id"), "template_id": payload.get("template_id"),
            **{k: v for k, v in fields.items() if v is not None}}


def fact_of(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The ops fact of an ordinary live event, or None when it says nothing the platform view needs."""
    kind, data = payload.get("event_type"), payload.get("data") or {}
    if kind in WORKSPACE_EVENTS and payload.get("channel_id") != f"template:{payload.get('template_id')}":
        return None
    run = payload.get("run_id")
    if kind == "agent_thought":
        return _fact("run.thought", payload, run_id=run, step=data.get("step"))
    if kind == "tool_called":
        return _fact("run.tool", payload, run_id=run, step=data.get("step"), tool=data.get("tool_name"))
    if kind == "tool_result":
        ok = not str(data.get("result") or "").startswith(("ERROR", "REFUSED"))
        return _fact("run.tool_done", payload, run_id=run, step=data.get("step"), tool=data.get("tool_name"), ok=ok)
    if kind == "extraction_log":
        return _fact("document.stage", payload, document_id=data.get("document_id"), stage=data.get("stage"), level=data.get("level"),
                     log_seq=data.get("seq"), by_agent=data.get("actor") == "Agent")
    if kind == "intake_received":
        return _fact("document.received" if data.get("outcome") == "accepted" else "intake.rejected", payload,
                     document_id=data.get("document_id"), channel=data.get("channel_id"))
    if kind == "document_status":
        return _fact("document.status", payload, document_id=payload.get("channel_id"), status=data.get("status"))
    if kind == "agent_completed":
        return _fact("extraction.saved", payload, document_id=payload.get("channel_id"), status=data.get("status"),
                     confidence=data.get("confidence"))
    if kind == "agent_question":
        return _fact("question.opened", payload, question_id=data.get("id"), document_id=data.get("related_document_id"),
                     priority=data.get("priority"))
    if kind == "agent_conversation":
        return _fact("conversation.opened", payload, conversation_id=data.get("id") or data.get("conversation_id"))
    if kind == "agent_message":
        return _fact("message.sent", payload, conversation_id=payload.get("channel_id"), private=bool(payload.get("private_to")),
                     by=data.get("role") or data.get("sender"))
    if kind == "agent_stopped":
        return _fact("run.stop_asked", payload, conversation_id=payload.get("channel_id"))
    if kind == "delivery_changed":
        return _fact("delivery.changed", payload, delivery_id=data.get("delivery_id"), status=data.get("status"))
    if kind in ("agent_proposal", "rule_changed", "rule_ideas", "screen_changed"):
        return _fact("workspace.changed", payload, what=kind)
    return None


def deliver(payload: Dict[str, Any]) -> None:
    """Every live event on this process passes here (it never raises: the platform view must not break delivery)."""
    if not _listeners:
        return
    try:
        if payload.get("channel_id") == OPS:  # a fact the platform sent itself (a run started, stepped or ended)
            _send("ops", {"seq": next(_seq), **(payload.get("data") or {})})
            run = (payload.get("data") or {}).get("run_id")
            if run:
                _send(f"run:{run}", {"kind": payload.get("event_type"), **(payload.get("data") or {})})
            return
        fact = fact_of(payload)
        if fact:
            _send("ops", fact)
        content = {"kind": payload.get("event_type"), "content": True, **payload}
        if payload.get("run_id"):
            _send(f"run:{payload['run_id']}", content)
        document_id = (payload.get("data") or {}).get("document_id") or (fact or {}).get("document_id")
        if document_id:
            _send(f"document:{document_id}", content)
    except Exception:
        logger.exception("Couldn't pass an event to the platform view")


async def publish_fact(kind: str, *, tenant_id: str, template_id: Optional[str], **fields) -> None:
    """A fact from the platform itself, sent to every server process like any live event."""
    from app.services.live.ui_events import publish_ui_event  # ui_events sends through the bridge, which calls deliver()
    data = {"kind": kind, "at": datetime.now(timezone.utc).isoformat(), "tenant_id": tenant_id, "template_id": template_id,
            **{k: v for k, v in fields.items() if v is not None}}
    try:
        await publish_ui_event(OPS, kind, data, tenant_id=tenant_id, template_id=template_id)
    except Exception:
        logger.exception("Couldn't publish the platform fact %s", kind)
