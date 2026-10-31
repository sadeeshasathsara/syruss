"""
The extraction log: a plain-words trail of what happened to each document, kept per workspace.

`record` is called where things happen (an upload, the agent starting, a record saved, a review decided). It writes in its
own short transaction and pushes the entry live on the workspace channel, and it never raises: a problem with the log
must not stop a document being processed.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import AsyncSessionLocal
from app.models.extraction_log import ExtractionLogEntry
from app.services.ui_events import publish_ui_event

logger = logging.getLogger("core.extraction_log")

RETENTION_DAYS = 90
PAGE_MAX = 100
LEVELS = ("info", "success", "warning", "error")


def entry_dict(e: ExtractionLogEntry) -> Dict[str, Any]:
    return {
        "seq": e.seq, "id": e.id, "stage": e.stage, "level": e.level, "message": e.message, "document_id": e.document_id,
        "document_name": e.document_name, "actor": e.actor, "details": e.details or {}, "created_at": e.created_at,
    }


async def record(
    *, tenant_id: str, template_id: str, stage: str, message: str, level: str = "info", document_id: Optional[str] = None,
    document_name: Optional[str] = None, actor: str = "System", details: Optional[Dict[str, Any]] = None,
) -> None:
    try:
        async with AsyncSessionLocal() as db:
            entry = ExtractionLogEntry(
                tenant_id=tenant_id, template_id=template_id, document_id=document_id, document_name=document_name, stage=stage,
                level=level if level in LEVELS else "info", message=message[:1000], actor=actor[:100], details=details or {},
            )
            db.add(entry)
            await db.commit()
            await db.refresh(entry)
            payload = entry_dict(entry)
        await publish_ui_event(f"template:{template_id}", "extraction_log", payload, tenant_id=tenant_id, template_id=template_id)
    except Exception:
        logger.exception("Couldn't write an extraction log entry")


async def page(
    db: AsyncSession, template_id: str, *, limit: int = 50, before: Optional[int] = None, level: Optional[str] = None,
    document_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Newest first; `before` is the `seq` of the oldest entry already shown."""
    limit = max(1, min(limit, PAGE_MAX))
    stmt = select(ExtractionLogEntry).where(ExtractionLogEntry.template_id == template_id)
    if before is not None:
        stmt = stmt.where(ExtractionLogEntry.seq < before)
    if level == "problems":
        stmt = stmt.where(ExtractionLogEntry.level.in_(("warning", "error")))
    elif level in LEVELS:
        stmt = stmt.where(ExtractionLogEntry.level == level)
    if document_id:
        stmt = stmt.where(ExtractionLogEntry.document_id == document_id)
    rows: List[ExtractionLogEntry] = list((await db.execute(stmt.order_by(ExtractionLogEntry.seq.desc()).limit(limit + 1))).scalars().all())
    items = [entry_dict(e) for e in rows[:limit]]
    return {"items": items, "next_before": items[-1]["seq"] if len(rows) > limit and items else None}


async def prune(db: AsyncSession) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    result = await db.execute(delete(ExtractionLogEntry).where(ExtractionLogEntry.created_at < cutoff))
    return result.rowcount or 0
