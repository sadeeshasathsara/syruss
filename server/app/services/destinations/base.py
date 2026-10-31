"""
A destination kind is an adapter: what it needs to connect (fields the connect wizard shows), what it can be given (targets, for systems
that want specific fields), how to check the connection, and how to send one delivery.
"""
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional


@dataclass
class Field:
    """One thing to fill in when connecting. `secret` values are sealed at rest and never returned."""
    key: str
    label: str
    kind: str = "text"                    # text | url | password | number | select | textarea | checkbox
    required: bool = False
    secret: bool = False
    help: str = ""
    placeholder: str = ""
    options: Optional[List[Dict[str, str]]] = None
    default: Any = None
    show_if: Optional[Dict[str, str]] = None   # {"auth_type": "bearer"}: only shown when another field has this value


@dataclass
class Target:
    """A field the destination takes in a mapping. `line` ones belong to each line item."""
    key: str
    label: str
    required: bool = False
    line: bool = False
    kind: str = "text"                    # text | number | date


@dataclass
class SendResult:
    ok: bool
    status_code: Optional[int] = None
    error: Optional[str] = None
    retryable: bool = True
    external_ref: Optional[str] = None
    request: str = ""
    response: str = ""
    retry_after: Optional[int] = None


@dataclass
class TestResult:
    ok: bool
    message: str
    detail: Optional[str] = None


@dataclass
class SendContext:
    destination_id: str
    destination_name: str
    config: Dict[str, Any]
    secrets: Dict[str, Any]
    delivery_id: str
    idempotency_key: str
    event: str
    revision: int
    envelope: Dict[str, Any]              # the whole message: ids, document, and the record (mapped when the destination maps)
    test: bool = False
    document_key: Optional[str] = None    # where the original file is kept in Syruss's own storage (for destinations that also store it)
    # Stores changed credentials (a refreshed sign-in token) for this destination. Given by the worker; None when only checking a connection.
    save_secrets: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None
    reload_secrets: Optional[Callable[[], Awaitable[Dict[str, Any]]]] = None   # the stored credentials right now (another delivery may have refreshed them)


class Adapter:
    kind: str = ""
    label: str = ""
    blurb: str = ""
    category: str = "custom"              # custom | accounting | storage | mail
    fields: List[Field] = []
    targets: List[Target] = []
    uses_oauth: bool = False
    corrections: bool = True              # whether a corrected record can be sent on as an update

    def required_problems(self, config: Dict[str, Any], secrets: Dict[str, Any]) -> List[str]:
        """What is missing before this can be saved, in plain words."""
        problems = []
        for f in self.fields:
            if not f.required:
                continue
            if f.show_if and any(config.get(k) != v for k, v in f.show_if.items()):
                continue
            have = secrets.get(f.key) if f.secret else config.get(f.key)
            if have in (None, ""):
                problems.append(f"{f.label} is needed.")
        return problems

    def normalise(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Tidies the settings before they are saved (trims addresses, drops unknown keys)."""
        known = {f.key for f in self.fields if not f.secret}
        clean = {k: v for k, v in config.items() if k in known}
        for f in self.fields:
            if f.kind == "url" and isinstance(clean.get(f.key), str):
                clean[f.key] = clean[f.key].strip()
            elif isinstance(clean.get(f.key), str) and f.kind in ("text", "select"):
                clean[f.key] = clean[f.key].strip()
            if f.key not in clean and f.default is not None and not f.secret:
                clean[f.key] = f.default
        return clean

    async def test(self, config: Dict[str, Any], secrets: Dict[str, Any], destination_id: Optional[str] = None) -> TestResult:
        raise NotImplementedError

    async def send(self, ctx: SendContext) -> SendResult:
        raise NotImplementedError

    def describe(self) -> Dict[str, Any]:
        return {
            "kind": self.kind, "label": self.label, "blurb": self.blurb, "category": self.category, "uses_oauth": self.uses_oauth,
            "fields": [asdict(f) for f in self.fields], "targets": [asdict(t) for t in self.targets], "corrections": self.corrections,
            "maps": bool(self.targets), "mapping_required": bool(self.targets),
        }


def make_context(config: Dict[str, Any], secrets: Dict[str, Any], destination_id: Optional[str] = None) -> SendContext:
    """A context for checking a connection: the same as for a delivery, with nothing to send."""
    save = reload = None
    if destination_id:
        from app.services import delivery_service  # (imported here: the delivery service imports this package)
        save = lambda changed: delivery_service.save_secrets(destination_id, changed)  # noqa: E731
        reload = lambda: delivery_service.load_secrets(destination_id)  # noqa: E731
    return SendContext(destination_id=destination_id or "unsaved", destination_name="", config=config, secrets=secrets, delivery_id="test",
                       idempotency_key="test", event="ping", revision=0, envelope={}, test=True, save_secrets=save, reload_secrets=reload)


ADAPTERS: Dict[str, Adapter] = {}


def register(adapter: Adapter) -> Adapter:
    ADAPTERS[adapter.kind] = adapter
    return adapter
