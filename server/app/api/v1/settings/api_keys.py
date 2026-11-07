"""API keys: keys other systems use to call this workspace's API, shown once and revocable."""
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Literal, Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.auth import require_recent_login
from app.core.database import get_db_session
from app.core.permissions import Access, require
from app.core.secrets import token_hash
from app.models.workspace_admin import ApiKey
from app.services import settings_service

router = APIRouter()


class ApiKeyIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    scope: Literal["read", "write"] = "read"
    expires_in_days: Optional[int] = Field(None, ge=1, le=730)


def _key_dict(k: ApiKey) -> Dict[str, Any]:
    return {"id": k.id, "name": k.name, "prefix": k.prefix, "scope": k.scope, "created_by": k.created_by,
            "created_at": k.created_at, "expires_at": k.expires_at, "last_used_at": k.last_used_at, "revoked_at": k.revoked_at}


@router.get("/api-keys")
async def list_api_keys(access: Access = Depends(require("integrations.manage")), db: AsyncSession = Depends(get_db_session)):
    rows = (await db.execute(select(ApiKey).where(ApiKey.template_id == access.scope.template_id, ApiKey.scope != "intake")
                             .order_by(ApiKey.created_at.desc()))).scalars().all()
    return [_key_dict(k) for k in rows]


@router.post("/api-keys", status_code=201, dependencies=[Depends(require_recent_login)])
async def create_api_key(body: ApiKeyIn, access: Access = Depends(require("integrations.manage")), db: AsyncSession = Depends(get_db_session)):
    """The key is returned once. Send it as `Authorization: Bearer <key>` with `X-Template-ID`."""
    await settings_service.check_limit(db, access.scope.template_id, "api_keys")
    plain = f"sk_{secrets.token_urlsafe(32)}"
    key = ApiKey(
        tenant_id=access.scope.tenant_id, template_id=access.scope.template_id, name=body.name.strip(),
        prefix=plain[:10], key_hash=token_hash(plain), scope=body.scope, created_by=access.auth.username or access.auth.user_id,
        expires_at=datetime.now(timezone.utc) + timedelta(days=body.expires_in_days) if body.expires_in_days else None,
    )
    db.add(key)
    await db.commit()
    return {"api_key": _key_dict(key), "key": plain}


@router.delete("/api-keys/{key_id}", status_code=204, dependencies=[Depends(require_recent_login)])
async def revoke_api_key(key_id: str, access: Access = Depends(require("integrations.manage")), db: AsyncSession = Depends(get_db_session)):
    key = await db.get(ApiKey, key_id)
    if not key or key.template_id != access.scope.template_id or key.scope == "intake":
        raise HTTPException(status_code=404, detail="API key not found.")
    key.revoked_at = key.revoked_at or datetime.now(timezone.utc)
    await db.commit()
