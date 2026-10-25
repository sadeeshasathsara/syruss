"""The run's context, what a tool returns, and the registry every tool joins with @tool."""
import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Type
from pydantic import BaseModel
from agentic_framework.core_api import CoreAPI
from agentic_framework.events import AgentEvent
from agentic_framework.memory import MemoryStore


@dataclass
class ToolOutcome:
    text: str
    images: List[Dict[str, Any]] = field(default_factory=list)  # [{"caption", "b64", "mime"}]
    touched_document_ids: List[str] = field(default_factory=list)


@dataclass
class RunContext:
    event: AgentEvent
    api: CoreAPI
    memory: MemoryStore
    touched_documents: set = field(default_factory=set)
    parked_documents: set = field(default_factory=set)
    resolved_documents: set = field(default_factory=set)
    replied: bool = False
    opened_conversations: List[str] = field(default_factory=list)
    stopped: bool = False   # a person stopped this run
    guides_read: set = field(default_factory=set)
    # People and things a committed record named that aren't registered, and whether the agent may
    # register them itself ("you_may_register") or a person decides ("a_person_decides")
    unregistered: List[Dict[str, str]] = field(default_factory=list)
    registration: str = ""
    registration_reminded: bool = False
    resolved_conversations: set = field(default_factory=set)
    # Screen definitions this run has previewed (as canonical JSON): one is saved only after it was looked at
    previewed_screens: set = field(default_factory=set)
    # The workspace's screens as list_screens showed them (None until it has been called this run)
    screens_seen: Optional[List[Dict[str, Any]]] = None

    @property
    def tenant_id(self) -> str:
        return self.event.tenant_id

    @property
    def template_id(self) -> str:
        return self.event.template_id

    @property
    def evaluating(self) -> bool:
        """A shadow evaluation: the platform scores the record instead of storing it."""
        return self.event.type == "eval.item"


def json_arg(value: str, name: str, default: Any) -> Any:
    if value is None or str(value).strip() == "":
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{name} is not valid JSON: {exc}")


def dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------

Handler = Callable[[RunContext, Any], Awaitable[ToolOutcome]]


@dataclass
class Tool:
    name: str
    description: str
    args: Type[BaseModel]
    handler: Handler

    def schema(self) -> Dict[str, Any]:
        params = self.args.model_json_schema()
        params.pop("title", None)
        for prop in params.get("properties", {}).values():
            prop.pop("title", None)
        return {"type": "function", "function": {"name": self.name, "description": self.description, "parameters": params}}


TOOLS: Dict[str, Tool] = {}


def tool(name: str, description: str, args: Type[BaseModel]):
    def register(fn: Handler) -> Handler:
        TOOLS[name] = Tool(name, description, args, fn)
        return fn
    return register


class NoArgs(BaseModel):
    pass
