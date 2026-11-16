"""
The pieces both search boxes share (staff: api/v1/sysadmin/console.search; customers: api/v1/search).

Every match is case-insensitive and ranked the way people expect: an exact match first, then one that starts with what
was typed, then one that only contains it; the newest breaks ties. Results come back in groups, each capped, in one
shape: {"query", "groups": [{"key", "label", "items": [{"id", "title", "detail", "href", ...}]}]}.
"""
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import case, func, literal, or_
from sqlalchemy.sql.elements import ColumnElement

ESCAPE = "\\"


def _escaped(text: str) -> str:
    return text.lower().replace(ESCAPE, ESCAPE * 2).replace("%", "\\%").replace("_", "\\_")


class Query:
    """What was typed, ready to match columns with."""

    def __init__(self, q: str):
        self.text = " ".join(q.split())
        self.lower = self.text.lower()
        self.contains = "%" + _escaped(self.text) + "%"
        self.prefix = _escaped(self.text) + "%"

    def matches(self, *columns: Any) -> ColumnElement:
        """Any of the columns contains what was typed."""
        return or_(*[func.lower(c).like(self.contains, escape=ESCAPE) for c in columns])

    def rank(self, *columns: Any) -> ColumnElement:
        """0 for an exact match on any column, 1 when one starts with it, 2 otherwise: order by it first."""
        whens = [(func.lower(c) == self.lower, 0) for c in columns] + [(func.lower(c).like(self.prefix, escape=ESCAPE), 1) for c in columns]
        return case(*whens, else_=literal(2))


def item(id: str, title: str, *, href: str, detail: Optional[str] = None, status: Optional[str] = None, **extra: Any) -> Dict[str, Any]:
    return {"id": id, "title": title, "detail": detail or "", "href": href, "status": status, **extra}


def group(key: str, label: str, items: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    return {"key": key, "label": label, "items": list(items)}


def answer(q: Query, groups: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The groups that found something, the ones with an exact match first (the order given otherwise)."""
    found = [g for g in groups if g["items"]]
    exact = lambda g: any(q.lower in ((i["title"] or "").lower(), (i["id"] or "").lower()) for i in g["items"])  # noqa: E731
    return {"query": q.text, "groups": sorted(found, key=lambda g: not exact(g))}


def joined(*parts: Optional[str]) -> str:
    return " · ".join(p for p in parts if p)
