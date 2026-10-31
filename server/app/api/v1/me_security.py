"""
Your browsers and your sign-in history. The first group of routes is what a new browser uses to confirm itself; the
rest need a confirmed browser, like everything else.
"""
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.auth import Identity, get_identity
from app.core.database import get_db_session
from app.services import device_service

router = APIRouter(tags=["Account security"])


class CheckIn(BaseModel):
    token: str = Field(..., max_length=120)
    user_agent: str = Field("", max_length=400)
    ip: str = Field("", max_length=64)


@router.post("/me/device/check")
async def check_device(body: CheckIn, identity: Identity = Depends(get_identity), db: AsyncSession = Depends(get_db_session)):
    """The web app asks right after sign-in: is this browser known? A new one is emailed a code."""
    return await device_service.check(db, identity, token=body.token, user_agent=body.user_agent, ip=body.ip)


@router.get("/me/device")
async def device_status(x_device_token: Optional[str] = Header(None), identity: Identity = Depends(get_identity), db: AsyncSession = Depends(get_db_session)):
    return await device_service.status(db, identity, x_device_token)


@router.post("/me/device/send")
async def send_device_code(x_device_token: Optional[str] = Header(None), identity: Identity = Depends(get_identity), db: AsyncSession = Depends(get_db_session)):
    return await device_service.send_code(db, identity, x_device_token)


class ConfirmIn(BaseModel):
    code: str = Field(..., min_length=1, max_length=32)
    remember: bool = True


@router.post("/me/device/confirm")
async def confirm_device(body: ConfirmIn, x_device_token: Optional[str] = Header(None), identity: Identity = Depends(get_identity), db: AsyncSession = Depends(get_db_session)):
    return await device_service.confirm(db, identity, x_device_token, body.code, body.remember)


@router.get("/me/devices")
async def my_devices(x_device_token: Optional[str] = Header(None), identity: Identity = Depends(get_identity), db: AsyncSession = Depends(get_db_session)):
    return await device_service.list_devices(db, identity, x_device_token)


@router.post("/me/devices/revoke-others")
async def revoke_other_devices(x_device_token: Optional[str] = Header(None), identity: Identity = Depends(get_identity), db: AsyncSession = Depends(get_db_session)):
    return {"removed": await device_service.revoke_others(db, identity, x_device_token)}


@router.delete("/me/devices/{device_id}", status_code=204)
async def remove_device(device_id: str, identity: Identity = Depends(get_identity), db: AsyncSession = Depends(get_db_session)):
    await device_service.revoke(db, identity, device_id)


@router.get("/me/security-events")
async def security_events(
    limit: int = Query(30, ge=1, le=100), before: Optional[datetime] = None,
    identity: Identity = Depends(get_identity), db: AsyncSession = Depends(get_db_session),
):
    return await device_service.events(db, identity, limit=limit, before=before)
