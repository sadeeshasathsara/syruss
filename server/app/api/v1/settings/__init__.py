"""
Workspace settings: general preferences, plan and usage, members and roles, invitations,
your own notification preferences, API keys and webhooks. Every route checks the caller's
role in the workspace (app/core/permissions.py).
"""
from fastapi import APIRouter
from app.api.v1.settings import api_keys, general, invitations, members, roles, webhooks

# The prefix goes on each include: FastAPI refuses to include a router whose routes have an empty path ("") without one.
router = APIRouter(tags=["Workspace settings"])
router.include_router(general.router, prefix="/settings")
router.include_router(roles.router, prefix="/settings")
router.include_router(members.router, prefix="/settings")
router.include_router(invitations.router, prefix="/settings")
router.include_router(api_keys.router, prefix="/settings")
router.include_router(webhooks.router, prefix="/settings")
