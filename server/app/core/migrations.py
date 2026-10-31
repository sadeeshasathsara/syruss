"""
Brings the database schema up to date with Alembic (server/migrations).

A database created before migrations existed (tables made by create_all) is adopted: any
baseline table it is missing is created, it is marked as being at the baseline, and the later
migrations run.
"""
import asyncio
import logging
import re
from pathlib import Path
from typing import Optional
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from app.core.database import Base, engine

logger = logging.getLogger("core.migrations")
BASELINE = "0001"

# Database objects that exist only where an extension does, so the models don't declare them.
UNMODELLED = {("memories", "embedding"), ("memories", "ix_memories_embedding"), ("memories", "ix_memories_content_fts")}


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    """Autogenerate and drift checks skip the extension-only objects above."""
    table = getattr(getattr(obj, "table", None), "name", None)
    return (table, name) not in UNMODELLED


def _baseline_tables() -> set:
    """The tables the baseline migration creates, read from the migration itself."""
    versions = Path(__file__).resolve().parents[2] / "migrations" / "versions"
    baseline = next(versions.glob(f"*_{BASELINE}_*.py")).read_text(encoding="utf-8")
    return set(re.findall(r"op\.create_table\(\s*'([^']+)'", baseline))


def alembic_config(url: Optional[str] = None) -> Config:
    cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    cfg.attributes["skip_logging"] = True  # keep the app's logging setup
    if url:
        cfg.attributes["url"] = url
    return cfg


async def migrate() -> None:
    async with engine.connect() as conn:
        tables = await conn.run_sync(lambda c: inspect(c).get_table_names())
    if tables and "alembic_version" not in tables:
        logger.info("Adopting an existing database: marking it at the baseline before upgrading.")
        # Only the baseline's own tables: later migrations create theirs (creating them here too
        # made those migrations fail on an adopted database).
        baseline_tables = [t for name, t in Base.metadata.tables.items() if name in _baseline_tables() and name not in tables]
        if baseline_tables:
            async with engine.begin() as conn:
                await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=baseline_tables))
        await asyncio.to_thread(command.stamp, alembic_config(), BASELINE)
    await asyncio.to_thread(command.upgrade, alembic_config(), "head")
