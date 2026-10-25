from fastapi import APIRouter
from app.api.v1.account import router as account_router
from app.api.v1.admin import router as admin_router
from app.api.v1.audit import router as audit_router
from app.api.v1.channels import router as channels_router
from app.api.v1.connections import router as connections_router
from app.api.v1.conversations import router as conversations_router
from app.api.v1.deliveries import router as deliveries_router
from app.api.v1.destinations import router as destinations_router
from app.api.v1.intake import router as intake_router
from app.api.v1.documents import router as documents_router
from app.api.v1.extractions import router as extractions_router
from app.api.v1.knowledge import router as knowledge_router
from app.api.v1.knowledge_sources import router as knowledge_sources_router
from app.api.v1.memories import router as memories_router
from app.api.v1.organization import router as organization_router
from app.api.v1.quality import router as quality_router
from app.api.v1.questions import router as questions_router
from app.api.v1.reviews import router as reviews_router
from app.api.v1.runs import router as runs_router
from app.api.v1.screens import router as screens_router
from app.api.v1.settings import router as settings_router
from app.api.v1.system_lessons import router as system_lessons_router
from app.api.v1.templates import router as templates_router
from app.api.v1.workspaces import router as workspaces_router
from app.api.v1.extraction_log import router as extraction_log_router
from app.api.v1.live import router as live_router
from app.api.v1.me_security import router as me_security_router
from app.api.v1.org_dashboard import router as org_dashboard_router
from app.api.v1.overview import router as overview_router
from app.api.v1.ws import router as ws_router

# Public API used by the frontend
api_router = APIRouter()
api_router.include_router(workspaces_router)
api_router.include_router(organization_router)
api_router.include_router(templates_router)
api_router.include_router(documents_router)
api_router.include_router(extractions_router)
api_router.include_router(reviews_router)
api_router.include_router(questions_router)
api_router.include_router(knowledge_sources_router)
api_router.include_router(knowledge_router)
api_router.include_router(conversations_router)
api_router.include_router(audit_router)
api_router.include_router(settings_router)
api_router.include_router(runs_router)
api_router.include_router(memories_router)
api_router.include_router(system_lessons_router)
api_router.include_router(quality_router)
api_router.include_router(account_router)
api_router.include_router(admin_router)
api_router.include_router(ws_router)
api_router.include_router(live_router)
api_router.include_router(me_security_router)
api_router.include_router(org_dashboard_router)
api_router.include_router(extraction_log_router)
api_router.include_router(overview_router)
api_router.include_router(screens_router)
api_router.include_router(intake_router)
api_router.include_router(channels_router)
api_router.include_router(destinations_router)
api_router.include_router(deliveries_router)
api_router.include_router(connections_router)
