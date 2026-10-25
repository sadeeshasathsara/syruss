"""
Turning a template folder entry and its variables into text. Email templates live in templates/email as
<name>.html (rich) and <name>.txt (plain), sharing _layout.html and _components.html. A missing variable is an error,
never a blank, and everything inserted into HTML is escaped.
"""
from pathlib import Path
from typing import Any, Dict
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

TEMPLATES = Path(__file__).parent / "templates"

_files = Environment(
    loader=FileSystemLoader(str(TEMPLATES / "email")),
    autoescape=select_autoescape(["html"]),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=False,
)
# One-line strings (subjects, in-app titles): plain text, no HTML escaping.
_lines = Environment(autoescape=False, undefined=StrictUndefined)


def email_parts(template: str, variables: Dict[str, Any]) -> Dict[str, str]:
    """{"html": ..., "text": ...} for templates/email/<template>.html and .txt."""
    return {
        "html": _files.get_template(f"{template}.html").render(**variables),
        "text": _files.get_template(f"{template}.txt").render(**variables).strip() + "\n",
    }


def line(source: str, variables: Dict[str, Any]) -> str:
    return " ".join(_lines.from_string(source).render(**variables).split())
