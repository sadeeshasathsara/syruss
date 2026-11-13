"""
The pages of a file. A JPEG, PNG or WebP is one page; a TIFF holds one page per frame; a PDF has its own pages. Everything
that counts pages (what reading a document is charged) or shows one (to the agent, to people in the app, to platform staff)
goes through here, so a document always has the same pages wherever it is looked at.

PDF pages are drawn with pdfium (the engine in Chrome). It never runs a PDF's scripts, and it is used one call at a time.
"""
import io
import threading
from typing import Tuple
from PIL import Image
import pypdfium2 as pdfium

PDF = "application/pdf"
TIFF = "image/tiff"
# Browsers show these in an <img> as they are; anything else is drawn as a JPEG for them.
SHOWN_AS_IS = {"image/jpeg", "image/png", "image/webp"}

PDF_DPI = 200            # what a page is drawn at: enough for small print and handwriting
PDF_MAX_SIDE = 3000      # a poster-sized page is drawn smaller rather than as a huge picture

_pdfium = threading.Lock()   # pdfium is not safe to call from two threads at once


class Unreadable(Exception):
    """The file can't be opened as what it claims to be: a sentence a person can act on."""


def count(content: bytes, content_type: str) -> int:
    """How many pages the file has. Raises Unreadable for a damaged or password-protected file."""
    if content_type == PDF:
        with _pdfium:
            try:
                pdf = pdfium.PdfDocument(content)
            except pdfium.PdfiumError as exc:
                raise Unreadable("The PDF is damaged or protected by a password. Send it without a password.") from exc
            try:
                return len(pdf)
            finally:
                pdf.close()
    try:
        with Image.open(io.BytesIO(content)) as image:
            return max(1, int(getattr(image, "n_frames", 1)))
    except Exception as exc:
        raise Unreadable("The picture is damaged or incomplete and cannot be opened.") from exc


def image(content: bytes, content_type: str, page: int = 1) -> Image.Image:
    """One page (1 is the first) as an RGB picture. Raises IndexError for a page the file doesn't have."""
    if page < 1:
        raise IndexError(page)
    if content_type == PDF:
        with _pdfium:
            try:
                pdf = pdfium.PdfDocument(content)
            except pdfium.PdfiumError as exc:
                raise Unreadable("The PDF can't be opened.") from exc
            try:
                if page > len(pdf):
                    raise IndexError(page)
                sheet = pdf[page - 1]
                width, height = sheet.get_size()  # in points, 72 to the inch
                scale = min(PDF_DPI / 72, PDF_MAX_SIDE / max(width, height, 1))
                return sheet.render(scale=scale).to_pil().convert("RGB")
            finally:
                pdf.close()
    try:
        picture = Image.open(io.BytesIO(content))
        if page > max(1, int(getattr(picture, "n_frames", 1))):
            raise IndexError(page)
        if page > 1:
            picture.seek(page - 1)
        picture.load()
        return picture.convert("RGB")
    except IndexError:
        raise
    except Exception as exc:
        raise Unreadable("The picture can't be opened.") from exc


def size(content: bytes, content_type: str, page: int = 1) -> Tuple[int, int]:
    """(width, height) of a page as it is shown."""
    return image(content, content_type, page).size


def jpeg(content: bytes, content_type: str, page: int = 1, quality: int = 88) -> bytes:
    """A page as a JPEG, for a browser that can't show the file itself (a PDF, a TIFF, any page after the first)."""
    out = io.BytesIO()
    image(content, content_type, page).save(out, format="JPEG", quality=quality)
    return out.getvalue()
