"""
Files people download: what the browser calls them, and CSV cells a spreadsheet will show but never run.
"""
import re
from typing import Any
from urllib.parse import quote

_UNSAFE_IN_NAME = re.compile(r'[\x00-\x1f\x7f"\\]')
# A cell starting with one of these is read as a formula by Excel, LibreOffice and Google Sheets.
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")
_NUMBER = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")


def content_disposition(kind: str, filename: str) -> str:
    """
    `inline` or `attachment` with the file's own name. Headers are Latin-1, so a name in another script (or with a quote in
    it) can't go in as it is: it is sent as an ASCII stand-in for old clients and, exactly, as UTF-8 (RFC 6266).
    """
    name = _UNSAFE_IN_NAME.sub("", filename or "").strip() or "file"
    fallback = name.encode("ascii", "replace").decode("ascii").replace("?", "_")
    return f"{kind}; filename=\"{fallback}\"; filename*=UTF-8''{quote(name, safe='')}"


def csv_cell(value: Any) -> Any:
    """
    A value as a CSV cell. Text that a spreadsheet would treat as a formula (it can come from a scanned document, a
    customer's own name or reference) gets a leading apostrophe, so it shows as text. Numbers, negative ones too, stay numbers.
    """
    if isinstance(value, str) and value.startswith(_FORMULA_START) and not _NUMBER.match(value):
        return "'" + value
    return value


def csv_row(values) -> list:
    return [csv_cell(v) for v in values]
