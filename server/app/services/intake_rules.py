"""
What a file must be to be let in, whoever sends it (a person in the web app, another system through a channel). The sender's own claims
(file name, content type) are never trusted: the type is read from the file's first bytes, the picture is opened to check it is whole and
not absurdly large, and the file name is cleaned before it is stored or shown anywhere.
"""
import asyncio
import io
import re
import struct
from typing import Iterable, Optional, Tuple
import filetype
from PIL import Image
from app.core.config import settings

# What the agent can read today, with the extension each is stored under.
SUPPORTED = {"image/jpeg": "jpg", "image/png": "png", "image/tiff": "tif", "image/webp": "webp"}


CODES = {400: "invalid_file", 401: "unauthorized", 402: "plan_limit_reached", 403: "forbidden", 404: "not_found", 409: "conflict", 410: "expired",
         413: "file_too_large", 415: "unsupported_type", 422: "invalid_request", 429: "rate_limited", 503: "unavailable"}


def problem(status: int, message: str, code: Optional[str] = None, headers: Optional[dict] = None):
    """The one error shape every intake endpoint answers with: {"detail": {"code": "...", "message": "..."}}."""
    from fastapi import HTTPException
    return HTTPException(status_code=status, detail={"code": code or CODES.get(status, "error"), "message": message}, headers=headers)


class IntakeRejected(Exception):
    """A file that is not let in: the status to answer with, and a sentence the sender can act on."""

    def __init__(self, status: int, reason: str, code: Optional[str] = None):
        super().__init__(reason)
        self.status = status
        self.reason = reason
        self.code = code or CODES.get(status, "error")


def clean_filename(name: Optional[str]) -> str:
    """Only the file's own name: no folders, no control characters, no leading dots, at most 200 characters."""
    base = re.split(r"[\\/]", (name or "").strip())[-1]
    base = re.sub(r"[\x00-\x1f\x7f<>:\"|?*]", "", base).lstrip(". ").strip()
    return base[:200] or "document"


def inspect_image(content: bytes, allowed: Iterable[str]) -> Tuple[str, str]:
    """(content type, extension) of a file that really is an allowed image, or IntakeRejected."""
    if not content:
        raise IntakeRejected(400, "The file is empty.")
    kind = filetype.guess(content)
    found = kind.mime if kind else None
    if found not in SUPPORTED:
        shown = {"application/pdf": "a PDF", "application/zip": "a zip archive"}.get(found or "", f"'{found}'" if found else "an unknown kind of file")
        raise IntakeRejected(415, f"This is {shown}. Send a JPG, PNG, TIFF or WebP image.")
    if found not in set(allowed):
        accepted = ", ".join(sorted(t.split("/")[1].upper() for t in allowed if t in SUPPORTED)) or "none"
        raise IntakeRejected(415, f"{found.split('/')[1].upper()} images are not accepted here. This channel accepts: {accepted}.")
    try:
        with Image.open(io.BytesIO(content)) as image:
            width, height = image.size
            if width * height > settings.INTAKE_MAX_PIXELS:
                raise IntakeRejected(413, f"The picture is {width} x {height} pixels, larger than the {settings.INTAKE_MAX_PIXELS // 1_000_000} megapixels allowed.")
            image.verify()
    except IntakeRejected:
        raise
    except Exception:
        raise IntakeRejected(422, "The picture is damaged or incomplete and cannot be opened.")
    return found, SUPPORTED[found]


async def read_capped(upload, max_bytes: int) -> bytes:
    """An uploaded file's bytes, refusing as soon as it passes max_bytes."""
    chunks, total = [], 0
    while True:
        chunk = await upload.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise IntakeRejected(413, f"The file is larger than the {max_bytes // 1_000_000} MB allowed.")
        chunks.append(chunk)
    return b"".join(chunks)


async def scan_for_malware(content: bytes) -> None:
    """When a ClamAV daemon is configured, every file is scanned by it (clamd's INSTREAM protocol); a hit is refused."""
    if not settings.CLAMAV_HOST:
        return
    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(settings.CLAMAV_HOST, settings.CLAMAV_PORT), timeout=5)
        writer.write(b"zINSTREAM\0")
        for start in range(0, len(content), 64 * 1024):
            part = content[start:start + 64 * 1024]
            writer.write(struct.pack(">I", len(part)) + part)
        writer.write(struct.pack(">I", 0))
        await writer.drain()
        reply = (await asyncio.wait_for(reader.read(1024), timeout=30)).decode(errors="replace").strip("\0\n ")
        writer.close()
    except Exception as exc:  # a scanner that cannot be reached must not let files through unchecked
        raise IntakeRejected(503, f"The virus scanner could not be reached ({type(exc).__name__}). Try again shortly.")
    if "FOUND" in reply:
        raise IntakeRejected(422, "The file was refused: it looks harmful.")
    if not reply.endswith("OK"):
        raise IntakeRejected(503, "The virus scanner gave an unexpected answer. Try again shortly.")
