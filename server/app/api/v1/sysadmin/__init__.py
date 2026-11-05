"""
The system admin API (/api/v1/sysadmin): the platform seen from the people who run it, across every organization.
It answers only the system admin app's tokens, and only platform staff (see access.py).
"""
from fastapi import APIRouter
from app.api.v1.sysadmin import audit, overview, staff

router = APIRouter(prefix="/sysadmin", tags=["System administration"])
router.include_router(overview.router)
router.include_router(staff.router)
router.include_router(audit.router)
