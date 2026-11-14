"""
What people read names documents the way they know them.

The agent works with document ids and file names ("be513c10-...", "94776219512_20260722_182251.jpeg"); people know a
document by its identification key ("North Branch_2026-07-22"). Every message Syruss writes for people goes through
`name_documents`, which swaps the ids and file names of this workspace's documents for what to call them
(services/document_key.document_label). Ids inside a link or path are left alone, and so is anything that isn't one of
this workspace's documents.
"""
import re
from typing import Dict
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.document import Document
from app.services.document_key import document_label

# An id or a file name on its own (optionally in backticks), not part of a path or a longer word. A file name is at most
# 200 characters (as stored), so a long run of dots or dashes in a message can't make the search slow (polynomial backtracking).
_ID = re.compile(r"(?<![\w/-])`?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})`?(?![\w-])")
_FILE = re.compile(r"(?<![\w/.-])[`'\"]?([\w.-]{1,200}\.(?:jpe?g|png|gif|webp|tiff?|bmp|heic|pdf))[`'\"]?(?![\w.-])", re.I)


async def name_documents(db: AsyncSession, template_id: str, text: str) -> str:
    if not text:
        return text
    ids = set(_ID.findall(text))
    files = set(_FILE.findall(text))
    if not ids and not files:
        return text
    found = (await db.execute(select(Document).where(
        Document.template_id == template_id,
        or_(Document.id.in_(ids or {""}), Document.original_filename.in_(files or {""})),
    ))).scalars().all()
    by_id: Dict[str, str] = {d.id: document_label(d) for d in found}
    by_file: Dict[str, str] = {d.original_filename: document_label(d) for d in found}

    def swap(table: Dict[str, str]):
        return lambda m: table.get(m.group(1), m.group(0))

    return _FILE.sub(swap(by_file), _ID.sub(swap(by_id), text))
