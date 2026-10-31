import logging
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import app.models  # noqa: F401  (registers all tables)
from app.api.internal.agent_tools import router as agent_tools_router
from app.api.router import api_router
from app.core.config import settings
from app.core.database import AsyncSessionLocal, engine
from app.core.migrations import migrate
from app.services.agent_events import agent_event_relay
from app.services.delivery_service import delivery_worker
from app.services.intake_email import email_receiver
from app.services.intake_sftp import sftp_receiver
from app.services.kafka_ws_bridge import kafka_ws_bridge
from app.services.scheduler import scheduler
from app.services.storage_service import storage_service
from app.services import system_lessons
from app.core.auth import validate_auth_config
from app.core.security_headers import RequestSizeLimit, SecurityHeaders
from app.core.permissions import ensure_system_roles

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.AUTO_MIGRATE:
        await migrate()

    validate_auth_config()
    async with AsyncSessionLocal() as session:
        await ensure_system_roles(session)
        await system_lessons.ensure_seeded(session)

    storage_service.ensure_bucket_exists()
    await agent_event_relay.start()
    await kafka_ws_bridge.start()
    await scheduler.start()
    delivery_worker.start()
    await email_receiver.start()
    await sftp_receiver.start()
    yield
    await sftp_receiver.stop()
    await email_receiver.stop()
    await delivery_worker.stop()
    await scheduler.stop()
    await kafka_ws_bridge.stop()
    await agent_event_relay.stop()
    await engine.dispose()


app = FastAPI(
    title=settings.PROJECT_NAME,
    description="Core business API. The workspace agent runs as a separate service and acts through the internal Agent Tool API.",
    version="2.0.0",
    lifespan=lifespan,
    # The interactive docs list every route: development only.
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
    openapi_url=None if settings.is_production else "/openapi.json",
)
app.add_middleware(SecurityHeaders)
app.add_middleware(RequestSizeLimit)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.API_V1_PREFIX)
app.include_router(agent_tools_router, prefix=settings.AGENT_API_PREFIX)


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": settings.PROJECT_NAME,
        "database": "PostgreSQL",
        "storage": "MinIO",
        "agent_events": "kafka" if agent_event_relay._producer else "queued (Kafka unavailable)",
    }


www_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "www")
if os.path.isdir(www_dir):
    app.mount("/", StaticFiles(directory=www_dir, html=True), name="frontend")
