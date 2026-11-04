"""
Template rules: checks written in the rule language that run on every record after a document is read (see
services/rule_lang.py for the language and services/rule_service.py for their life). Anyone who can see documents can
see them; writing, changing and accepting them takes `template.publish`, like the template itself.
"""
from typing import Any, Dict, Optional
from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_db_session
from app.core.permissions import Access, require
from app.core.scope import TemplateScope, get_template_scope
from app.services import rule_service
from app.services.rule_engine import schema_rules
from app.services.rule_lang import MAX_LENGTH, catalogue, language_help

router = APIRouter(prefix="/rules", tags=["Rules"], dependencies=[Depends(require("documents.view"))])


class CheckIn(BaseModel):
    scope: str = Field("document", max_length=100)
    check: str = Field("", max_length=MAX_LENGTH)
    when: str = Field("", max_length=MAX_LENGTH)
    severity: str = "error"
    name: Optional[str] = Field(None, max_length=120)


@router.get("")
async def list_rules(db: AsyncSession = Depends(get_db_session), scope: TemplateScope = Depends(get_template_scope)):
    """The workspace's rules, and the checks that come with the template version (read-only here)."""
    rules = await rule_service.list_rules(db, scope.template_id)
    return {"rules": [rule_service.detail(r) for r in rules], "template_checks": schema_rules(scope.template.schema_definition)}


@router.get("/language")
async def language(scope: TemplateScope = Depends(get_template_scope)):
    """What a rule can be written with: the template's objects and fields, functions, aggregates and operators."""
    return {**language_help(), **catalogue(scope.template.schema_definition)}


@router.post("/check")
async def check_rule(body: CheckIn, db: AsyncSession = Depends(get_db_session), scope: TemplateScope = Depends(get_template_scope)):
    """Compiles a rule without saving it and tries it on the saved records (the editor's live check)."""
    return await rule_service.check(db, scope.template, body.model_dump())


@router.post("", status_code=201)
async def create_rule(body: rule_service.RuleIn, db: AsyncSession = Depends(get_db_session),
                      scope: TemplateScope = Depends(get_template_scope), _access: Access = Depends(require("template.publish"))):
    r = await rule_service.create(db, scope.template, body, actor=scope.user_id, actor_kind="person")
    await db.commit()
    await rule_service.announce(r, "created")
    return rule_service.detail(r)


@router.get("/{rule_id}")
async def get_rule(rule_id: str, db: AsyncSession = Depends(get_db_session), scope: TemplateScope = Depends(get_template_scope)):
    return rule_service.detail(await rule_service.get(db, scope.template_id, rule_id))


@router.patch("/{rule_id}")
async def update_rule(rule_id: str, body: rule_service.RulePatch, db: AsyncSession = Depends(get_db_session),
                      scope: TemplateScope = Depends(get_template_scope), _access: Access = Depends(require("template.publish"))):
    r = await rule_service.update(db, scope.template, await rule_service.get(db, scope.template_id, rule_id), body, actor=scope.user_id)
    await db.commit()
    await rule_service.announce(r, "updated")
    return rule_service.detail(r)


@router.delete("/{rule_id}", status_code=204)
async def delete_rule(rule_id: str, db: AsyncSession = Depends(get_db_session), scope: TemplateScope = Depends(get_template_scope),
                      _access: Access = Depends(require("template.publish"))):
    r = await rule_service.get(db, scope.template_id, rule_id)
    await rule_service.announce(r, "deleted")
    await rule_service.remove(db, r, actor=scope.user_id)
    await db.commit()
    return Response(status_code=204)


@router.post("/{rule_id}/accept")
async def accept_rule(rule_id: str, db: AsyncSession = Depends(get_db_session), scope: TemplateScope = Depends(get_template_scope),
                      _access: Access = Depends(require("template.publish"))) -> Dict[str, Any]:
    """Adds a rule Syruss suggested: every record is checked with it from now on."""
    r = await rule_service.accept(db, scope.template, await rule_service.get(db, scope.template_id, rule_id), actor=scope.user_id)
    await db.commit()
    await rule_service.announce(r, "accepted")
    return rule_service.detail(r)


@router.post("/{rule_id}/dismiss", status_code=204)
async def dismiss_rule(rule_id: str, db: AsyncSession = Depends(get_db_session), scope: TemplateScope = Depends(get_template_scope),
                       _access: Access = Depends(require("template.publish"))):
    """Turns down a suggestion; it is removed."""
    r = await rule_service.get(db, scope.template_id, rule_id)
    await rule_service.announce(r, "dismissed")
    await rule_service.remove(db, r, actor=scope.user_id, event="RULE_DISMISSED")
    await db.commit()
    return Response(status_code=204)
