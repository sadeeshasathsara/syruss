"""
Agent service tests: a scripted model and a fake core server (httpx MockTransport),
so they need neither an LLM nor the core server. Memory uses a separate test database.
"""
import base64
import io
import json
import os
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

os.environ["AGENT_MEMORY_DB"] = "agent_memory_test"

import httpx
import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from sqlalchemy import delete
from agentic_framework.config import settings
from agentic_framework.core_api import CoreAPI
from agentic_framework.events import AgentEvent
from agentic_framework.memory import MemoryStore, Note, ProcessedEvent

settings.EMBEDDINGS_ENABLED = False  # tests never call a real embedding model

JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/"
    "wAALCAABAAEBAREA/8QAFAABAAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q=="
)


class ScriptedModel(BaseChatModel):
    """Returns pre-written AIMessages in order and records what it was shown."""
    script: List[AIMessage]
    seen: List[Any] = []

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.seen.append(list(messages))
        msg = self.script.pop(0) if self.script else AIMessage(content="Done.")
        return ChatResult(generations=[ChatGeneration(message=msg)])


def call(name: str, args: Dict[str, Any], cid: Optional[str] = None) -> Dict[str, Any]:
    return {"name": name, "args": args, "id": cid or f"call_{name}", "type": "tool_call"}


class FakeCore:
    """Records requests; routes are (method, path regex) -> handler(request, match) -> (status, json|bytes)."""

    def __init__(self):
        self.requests: List[Tuple[str, str, Any]] = []
        self.routes: List[Tuple[str, re.Pattern, Callable]] = []
        self.doc_status: Dict[str, str] = {}
        self.add("GET", r"/workspace", lambda r, m: (200, {
            "template": {"id": "tpl1", "name": "Roster", "schema_definition": {"objects": []}, "active_version": {"id": "v1", "version": "1.0.0"}},
            "documents_by_status": {}, "entity_types": [], "open_questions": [], "pending_proposals": [],
        }))
        self.add("GET", r"/conversations/(?P<id>[^/]+)/messages", lambda r, m: (200, []))
        self.add("GET", r"/conversations/(?P<id>[^/]+)/summary", lambda r, m: (200, {"content": None, "covers_until": None}))
        self.memories: List[Dict[str, Any]] = []
        self.add("POST", r"/memories/search", self._search_memories)
        self.add("POST", r"/memories/import", lambda r, m: (200, {"imported": 0}))
        self.add("GET", r"/memories/embedding-queue", lambda r, m: (200, []))
        self.add("POST", r"/memories", self._remember)
        self.add("GET", r"/documents/(?P<id>[^/]+)/image", lambda r, m: (200, JPEG))
        self.add("GET", r"/documents/(?P<id>[^/]+)", lambda r, m: (200, {
            "id": m["id"], "filename": "roster.jpg", "status": self.doc_status.get(m["id"], "QUEUED"),
            "role": "DOCUMENT", "image_size": {"width": 1, "height": 1}, "record": None, "review": None,
        }))
        self.add("POST", r"/documents/(?P<id>[^/]+)/status", self._set_status)
        self.add("POST", r"/documents/(?P<id>[^/]+)/record", self._record)
        self.add("POST", r"/questions", lambda r, m: (201, {"id": "q1", **json.loads(r.content)}))
        self.add("POST", r"/conversations/(?P<id>[^/]+)/messages", lambda r, m: (201, {"message_id": "m2"}))
        self.add("POST", r"/traces", lambda r, m: (201, {"id": "trace1"}))
        self.add("GET", r"/quality", lambda r, m: (200, {"agent": {}, "reviews": {}, "fields": [], "calibration": []}))
        self.add("GET", r"/reviews/history", lambda r, m: (200, []))
        self.add("POST", r"/evals/outcome", lambda r, m: (200, {"status": "NO_ANSWER"}))
        # Page budget: what the platform reports in briefings and with every page image.
        self.page_budget: Dict[str, Any] = {"kind": "work", "own_documents": [], "left": 2,
                                            "already_read_this_run": {"limit": 2, "used": 0, "left": 2}}
        self.add("GET", r"/page-budget", lambda r, m: (200, self.page_budget))
        self.cancelled = False   # set True to act as if a person pressed Stop
        self.add("GET", r"/run/cancelled", lambda r, m: (200, {"cancelled": self.cancelled}))
        self.add("GET", r"/system-lessons", lambda r, m: (200, [{"id": "sys1", "content": "Never guess who someone is."}]))

    def add(self, method: str, pattern: str, handler: Callable) -> None:
        self.routes.insert(0, (method, re.compile(f"^{pattern}$"), handler))

    def _remember(self, request, m):
        body = json.loads(request.content)
        memory = {"id": f"mem{len(self.memories) + 1}", **body, "created": True}
        self.memories.append(memory)
        return 201, memory

    def _search_memories(self, request, m):
        """Any shared word, like the server's text fallback."""
        body = json.loads(request.content)
        words = {w for w in re.findall(r"\w{3,}", (body.get("query") or "").lower())}
        kinds = body.get("kinds") or None
        found = [x for x in self.memories if (not kinds or x["kind"] in kinds)
                 and (not words or words & set(re.findall(r"\w{3,}", x["content"].lower())))]
        return 200, found[: body.get("limit", 8)]

    def _set_status(self, request, m):
        self.doc_status[m["id"]] = json.loads(request.content)["status"]
        return 200, {"status": self.doc_status[m["id"]]}

    def _record(self, request, m):
        body = json.loads(request.content)
        status = "COMPLETED" if body["decision"] == "COMMIT" else "NEEDS_REVIEW"
        self.doc_status[m["id"]] = status
        return 200, {"status": status, "validation": {"valid": True, "errors": []}}

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/internal/agent/v1")
        body = json.loads(request.content) if request.content else None
        self.requests.append((request.method, path, body))
        for method, pattern, fn in self.routes:
            match = pattern.match(path)
            if method == request.method and match:
                status, payload = fn(request, match)
                if isinstance(payload, bytes):
                    return httpx.Response(status, content=payload, headers={
                        "content-type": "image/jpeg", "X-Page-Budget": json.dumps(self.page_budget)})
                return httpx.Response(status, json=payload)
        return httpx.Response(404, json={"detail": f"no route {request.method} {path}"})

    def api(self) -> CoreAPI:
        client = httpx.AsyncClient(transport=httpx.MockTransport(self.handler), base_url="http://core/internal/agent/v1")
        return CoreAPI("token", client=client)

    def paths(self, method: str) -> List[str]:
        return [p for m, p, _ in self.requests if m == method]


def sign_token(tenant_id: str, template_id: str, event_id: str, actor_id: str = "system", ttl: int = 3600,
               secret: str = settings.AGENT_TOKEN_SECRET) -> str:
    """What the core server would sign (tests use the development keypair derived from the shared secret)."""
    import hashlib
    import time
    import jwt
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    key = Ed25519PrivateKey.from_private_bytes(hashlib.sha256(secret.encode()).digest())
    now = int(time.time())
    return jwt.encode({"iss": "syruss-core", "aud": "syruss-agent-tools", "sub": actor_id, "tid": tenant_id, "tpl": template_id,
                       "evt": event_id, "jti": "j1", "iat": now, "exp": now + ttl}, key, algorithm="EdDSA")


def make_event(event_type: str, payload: Dict[str, Any], event_id: str = "evt1", actor: Dict[str, Any] | None = None) -> AgentEvent:
    return AgentEvent(event_id=event_id, type=event_type, tenant_id="t1", template_id="tpl1",
                      occurred_at="2026-10-23T00:00:00Z", payload=payload, actor=actor or {},
                      agent_token=sign_token("t1", "tpl1", event_id, (actor or {}).get("id") or "system"))


@pytest.fixture(scope="session")
async def memory():
    store = MemoryStore()
    await store.init()
    yield store
    await store.close()


@pytest.fixture(autouse=True)
async def clean_memory(memory):
    async with memory._session() as s:
        await s.execute(delete(Note))
        await s.execute(delete(ProcessedEvent))
        await s.commit()


@pytest.fixture
def core():
    return FakeCore()
