"""
Saving, checking and running template rules. Shared by the people's API (api/v1/rules.py) and the agent's (api/internal),
so a rule means the same thing whoever wrote it: it is compiled against the active template before it is stored, and
tried on the records already saved so the person (or Syruss) sees what it would have said about them.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from fastapi import HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.audit import AuditLog
from app.models.document import Document, DocumentRole, DocumentStatus
from app.models.extraction import ExtractionResult
from app.models.jobs import WorkspaceJob
from app.models.rule import TemplateRule
from app.models.template import DocumentTemplate
from app.services.agent_events import enqueue_agent_event
from app.services.document_key import document_label
from app.services.rule_lang import MAX_LENGTH, SEVERITIES, RuleError, compile_rule
from app.services.ui_events import publish_ui_event

MAX_RULES = 100
DRY_RUN_RECORDS = 50
STATUSES = ("suggested", "active", "disabled", "broken")


class RuleIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: str = Field("", max_length=2000)
    scope: str = Field("document", max_length=100, description='"document", or an object type to check each of its rows')
    check: str = Field(..., max_length=MAX_LENGTH, description="A yes/no expression in the rule language")
    when: str = Field("", max_length=MAX_LENGTH, description="Optional: only check where this is true")
    severity: str = Field("error", description="error (stops the commit) or warning (only reported)")
    message: str = Field("", max_length=500, description="What people are told when the rule fails")


class RulePatch(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=120)
    description: Optional[str] = Field(None, max_length=2000)
    scope: Optional[str] = Field(None, max_length=100)
    check: Optional[str] = Field(None, max_length=MAX_LENGTH)
    when: Optional[str] = Field(None, max_length=MAX_LENGTH)
    severity: Optional[str] = None
    message: Optional[str] = Field(None, max_length=500)
    status: Optional[str] = Field(None, description="active or disabled")


def as_rule(r: TemplateRule) -> Dict[str, Any]:
    """The shape the rule language and validate_record take."""
    return {"id": r.id, "name": r.name, "scope": r.scope, "check": r.check, "when": r.when, "severity": r.severity, "message": r.message}


def detail(r: TemplateRule) -> Dict[str, Any]:
    return {
        **as_rule(r), "description": r.description, "status": r.status, "created_by": r.created_by, "created_by_kind": r.created_by_kind,
        "rationale": r.rationale, "source_document_id": r.source_document_id, "source_conversation_id": r.source_conversation_id,
        "broken_reason": r.broken_reason, "last_dry_run": r.last_dry_run,
        "created_at": r.created_at.isoformat() if r.created_at else None, "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


def compile_or_refuse(rule: Dict[str, Any], schema: Optional[Dict[str, Any]]) -> List[str]:
    """Compiles the rule against the template; a 422 with the problem, where it is and how to fix it, when it doesn't.
    Returns the template paths the rule reads."""
    if rule.get("severity", "error") not in SEVERITIES:
        raise HTTPException(status_code=422, detail={"message": "severity must be 'error' or 'warning'.", "problems": []})
    try:
        return compile_rule(rule, schema).reads
    except RuleError as exc:
        raise HTTPException(status_code=422, detail={"message": f"The rule does not compile: {exc.message}", "problems": [exc.to_dict()]})


async def dry_run(db: AsyncSession, template_id: str, schema: Optional[Dict[str, Any]], rule: Dict[str, Any],
                  limit: int = DRY_RUN_RECORDS, hidden: Optional[set] = None) -> Dict[str, Any]:
    """What the rule says about the records already committed (newest first): how many pass, fail, or don't apply,
    and the first documents it fails on."""
    compiled = compile_rule(rule, schema)
    stmt = (
        select(ExtractionResult, Document)
        .join(Document, Document.id == ExtractionResult.document_id)
        .where(ExtractionResult.template_id == template_id, Document.status == DocumentStatus.COMPLETED,
               Document.role == DocumentRole.DOCUMENT)
        .order_by(ExtractionResult.updated_at.desc()).limit(limit)
    )
    passed = failed = skipped = 0
    failures: List[Dict[str, Any]] = []
    for result, doc in (await db.execute(stmt)).all():
        if hidden and doc.id in hidden:
            continue
        outcome = compiled.run(result.extracted_data or {})
        if outcome["failed"]:
            failed += 1
            if len(failures) < 5:
                failures.append({"document_id": doc.id, "name": document_label(doc),
                                 "rows": [f["path"] for f in outcome["failed"][:5] if f["path"]]})
        elif outcome["checked"]:
            passed += 1
        else:
            skipped += 1
    return {"records": passed + failed + skipped, "passed": passed, "failed": failed, "not_applicable": skipped,
            "failures": failures, "reads": compiled.reads}


async def check(db: AsyncSession, template: DocumentTemplate, rule: Dict[str, Any], hidden: Optional[set] = None) -> Dict[str, Any]:
    """Compile and dry run, for the editor and the agent: never a 422, the problems are part of the answer."""
    try:
        compile_rule(rule, template.schema_definition)
    except RuleError as exc:
        return {"ok": False, "problems": [exc.to_dict()]}
    return {"ok": True, "problems": [], "dry_run": await dry_run(db, template.id, template.schema_definition, rule, hidden=hidden)}


async def list_rules(db: AsyncSession, template_id: str, statuses: Optional[List[str]] = None) -> List[TemplateRule]:
    stmt = select(TemplateRule).where(TemplateRule.template_id == template_id)
    if statuses:
        stmt = stmt.where(TemplateRule.status.in_(statuses))
    return list((await db.execute(stmt.order_by(TemplateRule.created_at))).scalars().all())


async def active_rules(db: AsyncSession, template_id: str) -> List[Dict[str, Any]]:
    """The rules every record is checked with."""
    return [as_rule(r) for r in await list_rules(db, template_id, ["active"])]


async def get(db: AsyncSession, template_id: str, rule_id: str) -> TemplateRule:
    r = await db.get(TemplateRule, rule_id)
    if not r or r.template_id != template_id:
        raise HTTPException(status_code=404, detail="Rule not found.")
    return r


def _audit(db: AsyncSession, r: TemplateRule, event: str, actor: str, **details: Any) -> None:
    db.add(AuditLog(tenant_id=r.tenant_id, template_id=r.template_id, event_type=event, entity_type="Rule", entity_id=r.id,
                    actor=actor, details={"name": r.name, "check": r.check, **details}))


async def create(db: AsyncSession, template: DocumentTemplate, body: RuleIn, *, actor: str, actor_kind: str, status: str = "active",
                 rationale: Optional[str] = None, source_document_id: Optional[str] = None,
                 source_conversation_id: Optional[str] = None) -> TemplateRule:
    if not template.active_version_id:
        raise HTTPException(status_code=409, detail="Publish the template first: rules check records against its fields.")
    count = (await db.execute(select(func.count()).select_from(TemplateRule).where(TemplateRule.template_id == template.id))).scalar_one()
    if count >= MAX_RULES:
        raise HTTPException(status_code=409, detail=f"A workspace can have {MAX_RULES} rules. Delete one you no longer use.")
    rule = {**body.model_dump(), "scope": body.scope.strip() or "document"}
    compile_or_refuse(rule, template.schema_definition)
    r = TemplateRule(
        tenant_id=template.tenant_id, template_id=template.id, name=body.name.strip()[:120], description=body.description.strip(),
        scope=rule["scope"], check=body.check.strip(), when=body.when.strip(), severity=body.severity, message=body.message.strip(),
        status=status, created_by=actor, created_by_kind=actor_kind, rationale=rationale or None,
        source_document_id=source_document_id, source_conversation_id=source_conversation_id,
        checked_against_version_id=template.active_version_id,
    )
    r.last_dry_run = await dry_run(db, template.id, template.schema_definition, as_rule(r))
    db.add(r)
    await db.flush()
    _audit(db, r, "RULE_SUGGESTED" if status == "suggested" else "RULE_CREATED", actor, by=actor_kind)
    return r


async def update(db: AsyncSession, template: DocumentTemplate, r: TemplateRule, body: RulePatch, *, actor: str) -> TemplateRule:
    changes = body.model_dump(exclude_none=True)
    status = changes.pop("status", None)
    if status is not None and status not in ("active", "disabled"):
        raise HTTPException(status_code=422, detail="A rule can be set to active or disabled.")
    for key, value in changes.items():
        setattr(r, key, value.strip() if isinstance(value, str) else value)
    if changes or status == "active":
        compile_or_refuse(as_rule(r), template.schema_definition)
        r.broken_reason = None
        r.checked_against_version_id = template.active_version_id
        r.last_dry_run = await dry_run(db, template.id, template.schema_definition, as_rule(r))
        if r.status == "broken":
            r.status = "active"
    if status is not None:
        r.status = status
    _audit(db, r, "RULE_UPDATED", actor, changed=sorted(changes) + (["status"] if status else []))
    return r


async def accept(db: AsyncSession, template: DocumentTemplate, r: TemplateRule, *, actor: str) -> TemplateRule:
    """A person keeps a rule Syruss suggested: from now on every record is checked with it."""
    if r.status != "suggested":
        raise HTTPException(status_code=409, detail="Only a suggested rule can be added.")
    compile_or_refuse(as_rule(r), template.schema_definition)
    r.status = "active"
    _audit(db, r, "RULE_ACCEPTED", actor)
    return r


async def remove(db: AsyncSession, r: TemplateRule, *, actor: str, event: str = "RULE_DELETED") -> None:
    _audit(db, r, event, actor)
    await db.delete(r)


async def recheck_all(db: AsyncSession, template: DocumentTemplate) -> List[TemplateRule]:
    """After the template changed: a rule that no longer fits is broken (and not run) until someone fixes it; a broken
    rule that fits again comes back. Returns the rules that broke now."""
    broke: List[TemplateRule] = []
    for r in await list_rules(db, template.id, ["active", "suggested", "broken"]):
        try:
            compile_rule(as_rule(r), template.schema_definition)
        except RuleError as exc:
            if r.status != "broken":
                r.status, r.broken_reason = "broken", exc.message
                broke.append(r)
                _audit(db, r, "RULE_BROKEN", "system", reason=exc.message)
            continue
        if r.status == "broken":
            r.status, r.broken_reason = "active", None
            _audit(db, r, "RULE_RESTORED", "system")
        r.checked_against_version_id = template.active_version_id
    if broke:
        from app.services import workspace_events
        names = ", ".join(r.name for r in broke[:3]) + (f" and {len(broke) - 3} more" if len(broke) > 3 else "")
        await workspace_events.emit(
            db, tenant_id=template.tenant_id, template_id=template.id, type="rule.broken", severity="warning",
            title=f"{len(broke)} rule{'s' if len(broke) != 1 else ''} stopped after the template changed", body=names,
            path="template/rules", data={"rules": [r.id for r in broke]})
    return broke


async def announce(r: TemplateRule, what: str) -> None:
    """Tells open workspaces so the Rules tab and chat cards refresh."""
    await publish_ui_event(f"template:{r.template_id}", "rule_changed", {"rule": r.id, "name": r.name, "what": what, "status": r.status,
                                                                       "by": r.created_by_kind}, tenant_id=r.tenant_id, template_id=r.template_id)


# --- ideas for the new-rule page ---------------------------------------------------------------
# Syruss thinks of rules worth having for this template once per template version; the page shows them as starting points.
# Asking again while it is still thinking does nothing, so loading the page never costs a model call.

IDEAS_JOB = "rule_ideas"
IDEAS_RETRY = timedelta(minutes=10)   # a request that never got an answer (agent down) is made again after this
MAX_IDEAS = 6


async def _ideas_job(db: AsyncSession, template: DocumentTemplate) -> Optional[WorkspaceJob]:
    await db.execute(insert(WorkspaceJob).values(
        template_id=template.id, job=IDEAS_JOB, tenant_id=template.tenant_id, state={}, updated_at=datetime.now(timezone.utc),
    ).on_conflict_do_nothing())
    return (await db.execute(select(WorkspaceJob).where(
        WorkspaceJob.template_id == template.id, WorkspaceJob.job == IDEAS_JOB).with_for_update(skip_locked=True))).scalar_one_or_none()


async def ideas(db: AsyncSession, template: DocumentTemplate, *, may_ask: bool, now: Optional[datetime] = None) -> Dict[str, Any]:
    """{"status": ready | thinking | none | no_template, "ideas": [{"text"}]}. Wakes Syruss when this template version has no
    ideas yet and the person may ask for them (writing rules takes template.publish)."""
    if not template.active_version_id:
        return {"status": "no_template", "ideas": []}
    now = now or datetime.now(timezone.utc)
    job = await _ideas_job(db, template)
    if job is None:  # another request is updating it right now
        return {"status": "thinking", "ideas": []}
    state = dict(job.state or {})
    current = state.get("version_id") == template.active_version_id
    if current and state.get("status") == "ready":
        return {"status": "ready", "ideas": state.get("ideas") or []}
    asked = datetime.fromisoformat(state["requested_at"]) if state.get("requested_at") else None
    if current and state.get("status") == "thinking" and asked and now - asked < IDEAS_RETRY:
        return {"status": "thinking", "ideas": []}
    if not may_ask:
        return {"status": "none", "ideas": []}
    enqueue_agent_event(db, event_type="rules.ideas", tenant_id=template.tenant_id, template_id=template.id,
                        payload={"version_id": template.active_version_id, "channel_id": f"template:{template.id}"}, source="dashboard")
    job.state = {"version_id": template.active_version_id, "status": "thinking", "requested_at": now.isoformat(), "ideas": []}
    job.last_run_at, job.last_status = now, "thinking"
    return {"status": "thinking", "ideas": []}


async def store_ideas(db: AsyncSession, template: DocumentTemplate, version_id: str, texts: List[str]) -> Dict[str, Any]:
    """What Syruss came up with, kept for the version it looked at."""
    clean: List[Dict[str, str]] = []
    for t in texts:
        text = " ".join(str(t or "").split())[:200]
        if text and text.lower() not in {i["text"].lower() for i in clean}:
            clean.append({"text": text})
    job = await _ideas_job(db, template)
    if job is None:
        raise HTTPException(status_code=409, detail="The ideas are being updated; try again.")
    job.state = {"version_id": version_id, "status": "ready", "ideas": clean[:MAX_IDEAS],
                 "requested_at": (job.state or {}).get("requested_at")}
    job.last_status = "ready"
    await publish_ui_event(f"template:{template.id}", "rule_ideas", {"count": len(clean[:MAX_IDEAS])},
                           tenant_id=template.tenant_id, template_id=template.id)
    return {"status": "ready", "ideas": clean[:MAX_IDEAS]}
