"""
Object and thing profiles for staff (app.services.object_profiles), in any workspace, read-only. Each answer says
which organization and workspace it belongs to.
"""
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.sysadmin.access import require_staff
from app.core.database import get_db_session
from app.models.template import DocumentTemplate
from app.models.tenant import Tenant
from app.models.workspace import Workspace
from app.services import object_profiles

router = APIRouter(prefix="/objects", dependencies=[Depends(require_staff())])


async def _where(db: AsyncSession, template_id: str):
    template = await db.get(DocumentTemplate, template_id)
    if template is None:
        raise HTTPException(status_code=404, detail="No such workspace.")
    workspace = await db.get(Workspace, template_id)
    tenant = await db.get(Tenant, template.tenant_id)
    tags = {"organization": {"id": template.tenant_id, "name": tenant.name if tenant else template.tenant_id},
            "workspace": {"id": template_id, "name": workspace.name if workspace else template.name}}
    return template, tags


@router.get("/{template_id}")
async def list_objects(template_id: str, db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    template, tags = await _where(db, template_id)
    return {**tags, "objects": await object_profiles.overview(db, template)}


@router.get("/{template_id}/{obj_type}")
async def object_profile(template_id: str, obj_type: str, page: int = Query(0, ge=0, le=500), db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    template, tags = await _where(db, template_id)
    return {**tags, **await object_profiles.object_profile(db, template, obj_type, page=page)}


@router.get("/{template_id}/{obj_type}/things")
async def object_things(template_id: str, obj_type: str, q: str = Query("", max_length=120), page: int = Query(0, ge=0, le=500),
                        db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    template, _tags = await _where(db, template_id)
    return await object_profiles.things(db, template, obj_type, q=q, page=page)


@router.get("/{template_id}/{obj_type}/things/{thing_id}")
async def thing_profile(template_id: str, obj_type: str, thing_id: str, page: int = Query(0, ge=0, le=500),
                        db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    template, tags = await _where(db, template_id)
    return {**tags, **await object_profiles.thing_profile(db, template, obj_type, thing_id, page=page)}
