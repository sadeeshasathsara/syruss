"""
Object and thing profiles in a workspace (app.services.object_profiles): every object of the template has a page, and
so does every thing it has met (an identified instance of an object with an identity).
"""
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db_session
from app.core.permissions import require
from app.core.scope import TemplateScope, get_template_scope
from app.services import object_profiles

router = APIRouter(prefix="/objects", tags=["Object profiles"], dependencies=[Depends(require("documents.view"))])


@router.get("")
async def list_objects(db: AsyncSession = Depends(get_db_session), scope: TemplateScope = Depends(get_template_scope)) -> List[Dict[str, Any]]:
    return await object_profiles.overview(db, scope.template)


@router.get("/{obj_type}")
async def object_profile(obj_type: str, page: int = Query(0, ge=0, le=500), db: AsyncSession = Depends(get_db_session),
                         scope: TemplateScope = Depends(get_template_scope)) -> Dict[str, Any]:
    return await object_profiles.object_profile(db, scope.template, obj_type, page=page)


@router.get("/{obj_type}/things")
async def object_things(obj_type: str, q: str = Query("", max_length=120), page: int = Query(0, ge=0, le=500),
                        db: AsyncSession = Depends(get_db_session), scope: TemplateScope = Depends(get_template_scope)) -> Dict[str, Any]:
    return await object_profiles.things(db, scope.template, obj_type, q=q, page=page)


def _filters(f: List[str]) -> Dict[str, str]:
    """`field:value` pairs from the address; a value may itself hold colons."""
    out: Dict[str, str] = {}
    for item in f[:20]:
        name, sep, value = item.partition(":")
        if sep and name:
            out[name] = value
    return out


@router.get("/{obj_type}/records")
async def object_records(obj_type: str, q: str = Query("", max_length=120), f: List[str] = Query([]), since_days: Optional[int] = Query(None),
                         sort: str = Query("seen", max_length=120), direction: str = Query("desc", pattern="^(asc|desc)$"),
                         page: int = Query(0, ge=0, le=2000), db: AsyncSession = Depends(get_db_session),
                         scope: TemplateScope = Depends(get_template_scope)) -> Dict[str, Any]:
    """The rows of an object with an identity, across documents: searchable, filterable by date and attribute values, sortable."""
    return await object_profiles.records(db, scope.template, obj_type, q=q, filters=_filters(f), since_days=since_days, sort=sort,
                                         direction=direction, page=page)


@router.get("/{obj_type}/things/{thing_id}/records")
async def thing_records(obj_type: str, thing_id: str, q: str = Query("", max_length=120), f: List[str] = Query([]),
                        since_days: Optional[int] = Query(None), sort: str = Query("seen", max_length=120),
                        direction: str = Query("desc", pattern="^(asc|desc)$"), page: int = Query(0, ge=0, le=2000),
                        db: AsyncSession = Depends(get_db_session), scope: TemplateScope = Depends(get_template_scope)) -> Dict[str, Any]:
    """The same, for one thing: its row in each document that has one."""
    return await object_profiles.records(db, scope.template, obj_type, thing=thing_id, q=q, filters=_filters(f), since_days=since_days, sort=sort,
                                         direction=direction, page=page)


@router.get("/{obj_type}/things/{thing_id}")
async def thing_profile(obj_type: str, thing_id: str, page: int = Query(0, ge=0, le=500), db: AsyncSession = Depends(get_db_session),
                        scope: TemplateScope = Depends(get_template_scope)) -> Dict[str, Any]:
    return await object_profiles.thing_profile(db, scope.template, obj_type, thing_id, page=page)
