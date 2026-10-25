import os
import glob
import logging
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy import text, select, func, and_, or_, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal, get_db_session, Base
from app.core.config import settings
from app.core.auth import AuthContext, development_only, require_platform_admin, require_platform_admin_in_org
from app.core.permissions import ensure_system_roles
from app.models.tenant import Tenant
from app.models.trace import AgentExecutionTrace
from app.schemas.trace import (
    AgentExecutionTraceResponse,
    AgentTraceSummaryResponse,
    AgentTraceStatsResponse,
    AgentTraceListResponse,
)
from app.services.storage_service import storage_service
from app.services.event_broadcaster import broadcaster
import app.models  # noqa: F401 - ensure all SQLAlchemy models registered

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["Administration"])

# Resolve the samples directory relative to the project root
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
_CANDIDATE_SAMPLE_DIRS = [
    os.path.join(_PROJECT_ROOT, "www", "public", "samples"),
    os.path.join(_PROJECT_ROOT, "bridge", "testing-dataset"),
    os.path.join(_PROJECT_ROOT, "samples"),
]
_SAMPLES_DIR = next((d for d in _CANDIDATE_SAMPLE_DIRS if os.path.isdir(d)), os.path.join(_PROJECT_ROOT, "samples"))


@router.post("/reset", dependencies=[Depends(development_only), Depends(require_platform_admin)])
async def trigger_clean_slate_reset():
    """
    Clean slate reset: truncates all PostgreSQL tables, purges all MinIO objects,
    re-seeds the default simulated tenant, and clears the WS event broadcaster history.
    """
    results = []

    # 1. Purge MinIO Storage
    try:
        storage_service.ensure_bucket_exists()
        objects = list(storage_service.client.list_objects(settings.MINIO_BUCKET_NAME, recursive=True))
        count = 0
        for obj in objects:
            storage_service.client.remove_object(settings.MINIO_BUCKET_NAME, obj.object_name)
            count += 1
        results.append(f"MinIO: deleted {count} object(s)")
        logger.info(f"MinIO purged: {count} object(s) removed.")
    except Exception as ex:
        results.append(f"MinIO purge skipped: {ex}")
        logger.warning(f"MinIO purge warning: {ex}")

    # 2. Reset all core tables (the agent service keeps its own memory store)
    async with AsyncSessionLocal() as session:
        table_names = ", ".join(t.name for t in Base.metadata.sorted_tables)
        await session.execute(text(f"TRUNCATE TABLE {table_names} CASCADE;"))
        await session.commit()
    results.append(f"PostgreSQL: truncated {len(Base.metadata.sorted_tables)} tables")

    # 3. Re-seed the built-in roles (people sign up again through Keycloak: their accounts are gone with the tables)
    try:
        async with AsyncSessionLocal() as session:
            await ensure_system_roles(session)
            results.append("Roles: built-in roles re-created")
    except Exception as ex:
        results.append(f"Roles re-seed error: {ex}")

    # 4. Clear broadcaster history so stale WS events don't replay
    broadcaster.clear_history()
    results.append("WS broadcaster: history cleared")

    return {
        "status": "success",
        "message": "System fully reset and ready for fresh testing.",
        "details": results
    }


@router.get("/samples", dependencies=[Depends(development_only)])
async def list_sample_files():
    """List available sample document images in the samples/ directory."""
    if not os.path.isdir(_SAMPLES_DIR):
        return JSONResponse({"samples": [], "note": "samples/ directory not found"})
    
    files = [
        os.path.basename(f)
        for f in glob.glob(os.path.join(_SAMPLES_DIR, "*.jpeg"))
        + glob.glob(os.path.join(_SAMPLES_DIR, "*.jpg"))
        + glob.glob(os.path.join(_SAMPLES_DIR, "*.png"))
    ]
    return {"samples": sorted(files)}


@router.get("/samples/{filename}", dependencies=[Depends(development_only)])
async def serve_sample_file(filename: str):
    """Serve a sample document file for frontend presets."""
    # Sanitize filename — no path traversal
    safe_name = os.path.basename(filename)
    file_path = os.path.join(_SAMPLES_DIR, safe_name)
    if not os.path.isfile(file_path):
        return JSONResponse({"error": f"Sample '{safe_name}' not found."}, status_code=404)
    return FileResponse(file_path, media_type="image/jpeg", filename=safe_name)


# -----------------------------------------------------------------------------
# Agent Thread & Execution Forensics Endpoints
# -----------------------------------------------------------------------------

@router.get("/traces", response_model=AgentTraceListResponse)
async def list_agent_traces(
    profile: Optional[str] = Query(None, description="Filter by agent profile"),
    status: Optional[str] = Query(None, description="Filter by status (COMPLETED, NEEDS_REVIEW, FAILED, RUNNING)"),
    document_id: Optional[str] = Query(None, description="Filter by document ID"),
    search: Optional[str] = Query(None, description="Search text in trace ID, document ID, model, prompt, or output"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db_session),
    auth: AuthContext = Depends(require_platform_admin_in_org)
):
    """
    List all agent execution threads across the platform with forensic metadata,
    step counts, tool invocation tallies, and aggregate telemetry KPIs.
    """
    # 1. Base query for this tenant
    base_stmt = select(AgentExecutionTrace).where(AgentExecutionTrace.tenant_id == auth.tenant_id)

    # 2. Compute aggregate stats across all traces
    all_res = await db.execute(select(AgentExecutionTrace).where(AgentExecutionTrace.tenant_id == auth.tenant_id))
    all_records = all_res.scalars().all()

    total_threads = len(all_records)
    completed_threads = sum(1 for t in all_records if t.status == "COMPLETED")
    needs_review_threads = sum(1 for t in all_records if t.status == "NEEDS_REVIEW")
    failed_threads = sum(1 for t in all_records if t.status == "FAILED")
    running_threads = sum(1 for t in all_records if t.status == "RUNNING")

    durations = [t.total_duration_ms for t in all_records if t.total_duration_ms is not None]
    avg_duration_ms = round(sum(durations) / len(durations), 2) if durations else 0.0

    total_tool_calls = 0
    profiles_count: Dict[str, int] = {}
    models_count: Dict[str, int] = {}

    for t in all_records:
        profiles_count[t.profile] = profiles_count.get(t.profile, 0) + 1
        models_count[t.model] = models_count.get(t.model, 0) + 1
        steps = t.steps or []
        for s in steps:
            if isinstance(s, dict):
                total_tool_calls += len(s.get("tool_calls", []))

    stats = AgentTraceStatsResponse(
        total_threads=total_threads,
        completed_threads=completed_threads,
        needs_review_threads=needs_review_threads,
        failed_threads=failed_threads,
        running_threads=running_threads,
        avg_duration_ms=avg_duration_ms,
        total_tool_calls=total_tool_calls,
        profiles=profiles_count,
        models=models_count,
    )

    # 3. Apply filters
    filtered_stmt = base_stmt
    if profile and profile.lower() != "all":
        filtered_stmt = filtered_stmt.where(AgentExecutionTrace.profile == profile)
    if status and status.upper() != "ALL":
        filtered_stmt = filtered_stmt.where(AgentExecutionTrace.status == status.upper())
    if document_id:
        filtered_stmt = filtered_stmt.where(AgentExecutionTrace.document_id == document_id)
    if search and search.strip():
        term = f"%{search.strip()}%"
        filtered_stmt = filtered_stmt.where(
            or_(
                AgentExecutionTrace.id.ilike(term),
                AgentExecutionTrace.document_id.ilike(term),
                AgentExecutionTrace.model.ilike(term),
                AgentExecutionTrace.final_thought.ilike(term),
                AgentExecutionTrace.error_message.ilike(term),
            )
        )

    # 4. Count filtered records
    count_stmt = select(func.count()).select_from(filtered_stmt.subquery())
    total_matching_res = await db.execute(count_stmt)
    total_matching = total_matching_res.scalar() or 0

    # 5. Fetch paginated records ordered by started_at DESC
    filtered_stmt = filtered_stmt.order_by(AgentExecutionTrace.started_at.desc()).offset(offset).limit(limit)
    res = await db.execute(filtered_stmt)
    matching_traces = res.scalars().all()

    summaries: List[AgentTraceSummaryResponse] = []
    for t in matching_traces:
        steps = t.steps or []
        tc_count = 0
        for s in steps:
            if isinstance(s, dict):
                tc_count += len(s.get("tool_calls", []))

        summaries.append(
            AgentTraceSummaryResponse(
                id=t.id,
                tenant_id=t.tenant_id,
                document_id=t.document_id,
                profile=t.profile,
                model=t.model,
                status=t.status,
                started_at=t.started_at,
                completed_at=t.completed_at,
                total_duration_ms=t.total_duration_ms,
                step_count=len(steps),
                tool_call_count=tc_count,
                final_thought=t.final_thought,
                error_message=t.error_message,
            )
        )

    return AgentTraceListResponse(
        stats=stats,
        traces=summaries,
        total=total_matching,
        limit=limit,
        offset=offset
    )


@router.get("/traces/{trace_id}", response_model=AgentExecutionTraceResponse)
async def get_agent_trace_details(
    trace_id: str,
    db: AsyncSession = Depends(get_db_session),
    auth: AuthContext = Depends(require_platform_admin_in_org)
):
    """
    Fetch the complete, verbatim forensic execution trail for an agentic thread:
    every prompt, reasoning thought, tool call arguments and observations, and final responses.
    """
    stmt = select(AgentExecutionTrace).where(
        AgentExecutionTrace.id == trace_id,
        AgentExecutionTrace.tenant_id == auth.tenant_id
    )
    res = await db.execute(stmt)
    trace = res.scalars().first()
    if not trace:
        raise HTTPException(
            status_code=404,
            detail=f"Agent execution thread '{trace_id}' was not found."
        )
    return trace


@router.delete("/traces/{trace_id}", status_code=204)
async def delete_agent_trace(
    trace_id: str,
    db: AsyncSession = Depends(get_db_session),
    auth: AuthContext = Depends(require_platform_admin_in_org)
):
    """Delete a single agent thread trace."""
    stmt = select(AgentExecutionTrace).where(
        AgentExecutionTrace.id == trace_id,
        AgentExecutionTrace.tenant_id == auth.tenant_id
    )
    res = await db.execute(stmt)
    trace = res.scalars().first()
    if not trace:
        raise HTTPException(status_code=404, detail=f"Trace '{trace_id}' not found.")
    await db.delete(trace)
    await db.commit()
    return None


@router.post("/traces/purge", status_code=200)
async def purge_all_traces(
    db: AsyncSession = Depends(get_db_session),
    auth: AuthContext = Depends(require_platform_admin_in_org)
):
    """Purge all agent thread execution traces for this tenant."""
    stmt = delete(AgentExecutionTrace).where(AgentExecutionTrace.tenant_id == auth.tenant_id)
    result = await db.execute(stmt)
    await db.commit()
    return {"status": "success", "purged_count": result.rowcount}

