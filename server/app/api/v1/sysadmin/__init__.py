"""
The system admin API (/api/v1/sysadmin): the platform seen from the people who run it, across every organization.
It answers only the system admin app's tokens, and only platform staff (see access.py). Customers' content opens only
through an access grant (support_access.py).
"""
from fastapi import APIRouter
from app.api.v1.sysadmin import actions, audit, live, overview, processes, queues, runs, staff, support_access

router = APIRouter(prefix="/sysadmin", tags=["System administration"])
router.include_router(overview.router)
router.include_router(staff.router)
router.include_router(audit.router)
router.include_router(support_access.router)
router.include_router(runs.router)
router.include_router(processes.router)
router.include_router(queues.router)
router.include_router(live.router)
router.include_router(actions.router)
