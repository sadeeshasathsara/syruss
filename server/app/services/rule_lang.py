"""
The rule language: checks that run on a record after the agent has read a document.

A rule is a yes/no question about one record, written like a spreadsheet formula:

    scope Item        a == b * c                              a row's value follows from its other values
    scope document    abs(sum(Item.a) - Head.total) <= 0.01   rows add up to a total written once
    scope document    count(Item) == Head.count               the number of rows matches a written count
    scope Item        when: b == "X"    check: a >= "12:00"   a condition that holds for some rows only
    scope Item        party.a == a                            a row agrees with the thing it refers to
    scope Head        matches(a, "[A-Z]{3}-[0-9]{6}")         every value follows a format

The names are placeholders: a rule uses the template's own objects and fields, whatever the documents are.

The syntax is Python's expression syntax, so it is parsed by Python's own parser (`ast`), but it is never run by Python.
Only the elements below are accepted; anything else (attribute access that is not a template path, subscripts, lambdas,
comprehensions, keyword arguments, names with underscores in front) is refused when the rule is compiled. The compiled
rule is a tree of small functions that read values out of the record's dicts and call the functions listed here, so a rule
cannot reach the database, files, the network, other documents or the interpreter. Work is capped (length, nodes, steps,
exponents, string sizes) and `matches` uses RE2, which runs in linear time, so a rule cannot hang the server either.

Names
  scope <Object>    a bare name is a field of the row being checked (each row of <Object> is checked on its own)
  Object.field      a field of a one-per-document object; of a many-object only inside an aggregate: sum(Item.a)
  ref.field         follows a field that `resolves_to` another object to that row in the same record
  Object            all rows of an object, for count(Object), all(Object, condition), any(...), count_if(...)
  true, false, blank

Blank values make a rule "not applicable" for that row instead of failing it; required fields are checked elsewhere.
"""
import ast
import difflib
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import re2

from app.services import formula
from app.services.formula import Blank
from app.services.rule_engine import normalize_schema

MAX_LENGTH = 600
MAX_NODES = 200
MAX_STEPS = 200_000          # interpreter steps for one rule over one record
MAX_TEXT = 10_000            # characters in any text a rule produces
MAX_NESTED_AGGREGATES = 2    # all(A, any(B, ...)) is fine; deeper nesting multiplies the work
SEVERITIES = ("error", "warning")

# Types the compiler reasons with. "rows:<Obj>" and "list:<type>" exist only inside aggregates.
NUMBER, TEXT, DATE, TIME, BOOL, ANY = "number", "text", "date", "time", "boolean", "any"
FIELD_TYPES = {"number": NUMBER, "integer": NUMBER, "string": TEXT, "date": DATE, "time": TIME, "boolean": BOOL}
TYPE_WORDS = {NUMBER: "a number", TEXT: "text", DATE: "a date", TIME: "a time", BOOL: "yes/no", ANY: "a value"}


class RuleError(ValueError):
    """A rule that does not compile, with where and why, in words a person can act on."""

    def __init__(self, message: str, *, code: str = "invalid", explanation: str = "", hint: str = "",
                 node: Optional[ast.AST] = None, part: str = "check", col: Optional[int] = None, end_col: Optional[int] = None):
        super().__init__(message)
        self.message, self.code, self.explanation, self.hint, self.part = message, code, explanation, hint, part
        self.col = col if col is not None else getattr(node, "col_offset", None)
        self.end_col = end_col if end_col is not None else getattr(node, "end_col_offset", None)

    def to_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "part": self.part, "message": self.message, "explanation": self.explanation,
                "hint": self.hint, "col": self.col, "end_col": self.end_col}


class NotApplicable(Exception):
    """The values this rule needs are blank or unreadable in this row: the rule says nothing about it."""


# ---------------------------------------------------------------------------
# Functions: what each takes and gives, for the compiler, and its implementation (mostly formula.py's)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Signature:
    args: Tuple[str, ...]            # types of the arguments; ANY accepts anything
    result: str
    variadic: bool = False           # the last argument type repeats
    optional: int = 0                # how many trailing arguments may be left out
    usage: str = ""


SCALAR: Dict[str, Signature] = {
    "abs": Signature((NUMBER,), NUMBER, usage="abs(x)"),
    "floor": Signature((NUMBER,), NUMBER, usage="floor(x)"),
    "ceil": Signature((NUMBER,), NUMBER, usage="ceil(x)"),
    "sqrt": Signature((NUMBER,), NUMBER, usage="sqrt(x)"),
    "round": Signature((NUMBER, NUMBER), NUMBER, optional=1, usage="round(x, digits)"),
    "min": Signature((NUMBER,), NUMBER, variadic=True, usage="min(a, b, ...) of values in this row"),
    "max": Signature((NUMBER,), NUMBER, variadic=True, usage="max(a, b, ...) of values in this row"),
    "if_": Signature((BOOL, ANY, ANY), ANY, optional=1, usage="if_(condition, then, otherwise)"),
    "coalesce": Signature((ANY,), ANY, variadic=True, usage="coalesce(a, b, ...) the first that is not blank"),
    "isempty": Signature((ANY,), BOOL, usage="isempty(x) true when x is blank"),
    "number": Signature((ANY,), NUMBER, usage="number(x) text as a number"),
    "text": Signature((ANY,), TEXT, usage="text(x)"),
    "concat": Signature((ANY,), TEXT, variadic=True, usage="concat(a, b, ...)"),
    "upper": Signature((ANY,), TEXT, usage="upper(text)"),
    "lower": Signature((ANY,), TEXT, usage="lower(text)"),
    "trim": Signature((ANY,), TEXT, usage="trim(text)"),
    "len": Signature((ANY,), NUMBER, usage="len(text)"),
    "contains": Signature((ANY, ANY), BOOL, usage="contains(text, part)"),
    "left": Signature((ANY, NUMBER), TEXT, usage="left(text, n)"),
    "right": Signature((ANY, NUMBER), TEXT, usage="right(text, n)"),
    "hours_between": Signature((TIME, TIME), NUMBER, usage="hours_between(start, end) times, past midnight too"),
    "minutes_between": Signature((TIME, TIME), NUMBER, usage="minutes_between(start, end)"),
    "days_between": Signature((DATE, DATE), NUMBER, usage="days_between(from_date, to_date)"),
    "year": Signature((DATE,), NUMBER, usage="year(date)"),
    "month": Signature((DATE,), NUMBER, usage="month(date)"),
    "day": Signature((DATE,), NUMBER, usage="day(date)"),
    "weekday": Signature((DATE,), TEXT, usage="weekday(date) as Mon, Tue ..."),
    "matches": Signature((ANY, TEXT), BOOL, usage='matches(text, "pattern") the whole text matches the pattern'),
    "today": Signature((), DATE, usage="today()"),
}

# Aggregates read many rows. Their first argument is `Object.field` (values) or `Object` (rows).
AGGREGATES: Dict[str, Tuple[str, str, str]] = {
    # name: (what the first argument is, result type, usage)
    "sum": ("values", NUMBER, "sum(Object.field) the total of a field over the rows"),
    "avg": ("values", NUMBER, "avg(Object.field) the average"),
    "min_of": ("values", ANY, "min_of(Object.field) the smallest value"),
    "max_of": ("values", ANY, "max_of(Object.field) the largest value"),
    "count": ("rows_or_values", NUMBER, "count(Object) rows, or count(Object.field) values that are not blank"),
    "count_distinct": ("values", NUMBER, "count_distinct(Object.field) how many different values"),
    "unique": ("values", BOOL, "unique(Object.field) no value appears twice"),
    "count_if": ("rows_cond", NUMBER, "count_if(Object, condition) rows where the condition holds"),
    "all": ("rows_cond", BOOL, "all(Object, condition) true for every row"),
    "any": ("rows_cond", BOOL, "any(Object, condition) true for at least one row"),
}


def catalogue(schema: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """The template as rules see it: each object, whether it has many rows, its fields with their types and references."""
    return {"objects": [{
        "type": t, "multiple": spec["multiple"],
        "fields": [{"name": n, "type": f.get("type", "string"), **({"resolves_to": f["resolves_to"]} if f.get("resolves_to") else {})}
                   for n, f in spec["fields"].items()],
    } for t, spec in normalize_schema(schema).items()]}


def language_help() -> Dict[str, Any]:
    """The language as the editor and the agent see it."""
    return {
        "functions": [{"name": n, "usage": s.usage} for n, s in sorted(SCALAR.items())],
        "aggregates": [{"name": n, "usage": a[2]} for n, a in sorted(AGGREGATES.items())],
        "operators": ["+ - * / % // **", "== != < <= > >=", "and or not", "x in [\"A\", \"B\"]", "a if condition else b"],
        "constants": ["true", "false", "blank", "numbers", '"text"', '"2026-07-22" (a date)', '"07:30" (a time)'],
        "severities": list(SEVERITIES),
    }


# ---------------------------------------------------------------------------
# Running: an environment per row, with a step budget
# ---------------------------------------------------------------------------

class Env:
    __slots__ = ("data", "row", "steps", "today")

    def __init__(self, data: Dict[str, Any], row: Optional[Dict[str, Any]], today: date):
        self.data, self.row, self.steps, self.today = data, row, 0, today

    def tick(self) -> None:
        self.steps += 1
        if self.steps > MAX_STEPS:
            raise Blank("the rule does too much work for one record")

    def inner(self, row: Dict[str, Any]) -> "Env":
        e = Env(self.data, row, self.today)
        e.steps = self.steps
        return e


Fn = Callable[[Env], Any]


def rows_of(data: Dict[str, Any], obj: str) -> List[Dict[str, Any]]:
    value = data.get(obj)
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [r for r in value if isinstance(r, dict)]
    return []


def _blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _typed(value: Any, t: str) -> Any:
    """A value as its type, for comparing; None when blank. Unreadable values make the rule not applicable."""
    if _blank(value):
        return None
    try:
        if t == NUMBER:
            return formula._number(value)
        if t == DATE:
            return formula._date(value)
        if t == TIME:
            return formula._minutes(value)
        if t == BOOL:
            return value if isinstance(value, bool) else str(value).strip().lower() in ("true", "yes", "1")
    except Blank:
        raise NotApplicable()
    return value


def _limit_text(v: Any) -> Any:
    if isinstance(v, str) and len(v) > MAX_TEXT:
        raise Blank("the text is too long")
    return v


# ---------------------------------------------------------------------------
# Compiling
# ---------------------------------------------------------------------------

@dataclass
class Scope:
    objects: Dict[str, Dict[str, Any]]
    row_object: Optional[str]                 # the object whose row bare names read, None at document level
    part: str = "check"
    depth: int = 0                            # nested aggregates with conditions


@dataclass
class Node:
    fn: Fn
    type: str                                 # NUMBER, TEXT, ... or "rows:<Obj>" / "list:<type>"
    reads: Tuple[str, ...] = ()               # template paths it reads, for showing what a rule depends on
    constant: bool = False


@dataclass
class CompiledRule:
    rule: Dict[str, Any]
    scope: Optional[str]
    check: Node
    when: Optional[Node]
    reads: List[str] = field(default_factory=list)

    def run(self, data: Dict[str, Any], *, today: Optional[date] = None) -> Dict[str, Any]:
        """{"checked", "failed": [{"path", "index"}], "not_applicable"} for one record."""
        today = today or date.today()
        targets: List[Tuple[Optional[int], Optional[Dict[str, Any]]]] = (
            [(i, r) for i, r in enumerate(rows_of(data, self.scope))] if self.scope else [(None, None)])
        failed: List[Dict[str, Any]] = []
        checked = skipped = 0
        for index, row in targets:
            env = Env(data, row, today)
            try:
                if self.when is not None and self.when.fn(env) is not True:
                    skipped += 1
                    continue
                result = self.check.fn(env)
            except (NotApplicable, Blank):
                skipped += 1
                continue
            if result is None:
                skipped += 1
                continue
            checked += 1
            if result is False:
                failed.append({"index": index, "path": f"{self.scope}[{index}]" if self.scope else ""})
        return {"checked": checked, "failed": failed, "not_applicable": skipped}


def _suggest(name: str, options: Sequence[str]) -> str:
    close = difflib.get_close_matches(name, list(options), n=1, cutoff=0.6)
    return f"Did you mean '{close[0]}'?" if close else ""


def _err(message: str, node: Optional[ast.AST], scope: Scope, *, code: str = "invalid", explanation: str = "", hint: str = "") -> RuleError:
    return RuleError(message, code=code, explanation=explanation, hint=hint, node=node, part=scope.part)


def _is_rows(t: str) -> bool:
    return t.startswith("rows:")


def _is_list(t: str) -> bool:
    return t.startswith("list:")


def _scalar(node: Node, at: ast.AST, scope: Scope) -> Node:
    if _is_rows(node.type):
        obj = node.type[5:]
        raise _err(f"'{obj}' is all the rows of {obj}, not one value.", at, scope, code="rows_not_value",
                   explanation="A name of an object stands for every row of it; it can only go into count, all, any or count_if.",
                   hint=f"count({obj}), all({obj}, ...), or name one of its fields: {obj}.<field>.")
    if _is_list(node.type):
        raise _err("This is one value per row of a many-object, not one value.", at, scope, code="many_not_value",
                   explanation="An object with many rows per document has many values of each field.",
                   hint="Wrap it in sum, avg, min_of, max_of, count, count_distinct or unique, or scope the rule to that object.")
    return node


def _accepts(want: str, got: str) -> bool:
    return want == ANY or got == ANY or want == got or (want in (DATE, TIME) and got == TEXT)


def _compat(a: str, b: str) -> bool:
    if ANY in (a, b) or a == b:
        return True
    return {a, b} in ({DATE, TEXT}, {TIME, TEXT})  # "2026-07-22" and "07:30" are written as text


def _compare_type(a: Node, b: Node) -> str:
    """The type both sides are compared as."""
    for t in (DATE, TIME, NUMBER, BOOL):
        if t in (a.type, b.type):
            return t
    return TEXT if TEXT in (a.type, b.type) else ANY


def _field_spec(scope: Scope, obj: str, name: str, node: ast.AST) -> Dict[str, Any]:
    fields = scope.objects[obj]["fields"]
    if name not in fields:
        raise _err(f"{obj} has no field '{name}'.", node, scope, code="unknown_field",
                   explanation=f"The fields of {obj} are: {', '.join(fields) or 'none'}.", hint=_suggest(name, fields))
    return fields[name]


def _read_field(obj_getter: Callable[[Env], Optional[Dict[str, Any]]], name: str) -> Fn:
    def run(env: Env) -> Any:
        env.tick()
        row = obj_getter(env)
        return None if row is None else _limit_text(row.get(name))
    return run


def _single_row(obj: str) -> Callable[[Env], Optional[Dict[str, Any]]]:
    def get(env: Env) -> Optional[Dict[str, Any]]:
        rows = rows_of(env.data, obj)
        return rows[0] if rows else None
    return get


def _reference(scope: Scope, base: Node, spec: Dict[str, Any], attr: str, node: ast.Attribute) -> Node:
    """`ref.field`: the row of the referenced object whose identity is the value of `ref`."""
    target = spec["resolves_to"]
    ident = scope.objects[target]["identity"] or {}
    id_field = ident.get("id_field")
    tspec = _field_spec(scope, target, attr, node)

    def key(v: Any) -> str:
        return "".join(str(v).split()).upper()

    def run(env: Env) -> Any:
        env.tick()
        ref = base.fn(env)
        if _blank(ref):
            return None
        for r in rows_of(env.data, target):
            env.tick()
            if key(r.get(id_field)) == key(ref):
                return _limit_text(r.get(attr))
        raise NotApplicable()  # a dangling reference is reported by the structure checks
    return Node(run, FIELD_TYPES.get(tspec.get("type", "string"), TEXT), base.reads + (f"{target}.{attr}",))


class _Compiler:
    def __init__(self, scope: Scope):
        self.scope = scope
        self.count = 0

    def compile(self, n: ast.AST) -> Node:
        self.count += 1
        if self.count > MAX_NODES:
            raise _err("The rule is too long and complicated.", n, self.scope, code="too_complex",
                       hint="Split it into two rules.")
        method = getattr(self, f"c_{type(n).__name__}", None)
        if method is None:
            raise _err(f"This part of the rule is not allowed ({_describe(n)}).", n, self.scope, code="not_allowed",
                       explanation="Rules may use values, fields, operators, comparisons and the listed functions only.",
                       hint="See the list of functions and operators.")
        return method(n)

    # --- values -------------------------------------------------------------

    def c_Constant(self, n: ast.Constant) -> Node:
        v = n.value
        if isinstance(v, bool):
            return Node(lambda env: v, BOOL, constant=True)
        if isinstance(v, (int, float)):
            return Node(lambda env: v, NUMBER, constant=True)
        if isinstance(v, str):
            if len(v) > MAX_TEXT:
                raise _err("This text is too long.", n, self.scope, code="too_long")
            return Node(lambda env: v, TEXT, constant=True)
        if v is None:
            return Node(lambda env: None, ANY, constant=True)
        raise _err("Only numbers, \"text\", true, false and blank can be written in a rule.", n, self.scope, code="not_allowed")

    def c_Name(self, n: ast.Name) -> Node:
        name = n.id
        if name.startswith("_"):
            raise _err(f"'{name}' is not allowed.", n, self.scope, code="not_allowed",
                       explanation="Names that start with an underscore are reserved.")
        low = name.lower()
        if low in ("true", "false"):
            v = low == "true"
            return Node(lambda env: v, BOOL, constant=True)
        if low in ("blank", "none", "null"):
            return Node(lambda env: None, ANY, constant=True)
        row_obj = self.scope.row_object
        if row_obj and name in self.scope.objects[row_obj]["fields"]:
            spec = self.scope.objects[row_obj]["fields"][name]
            fn = _read_field(lambda env: env.row, name)
            node = Node(fn, FIELD_TYPES.get(spec.get("type", "string"), TEXT), (f"{row_obj}.{name}",))
            node.spec = spec  # type: ignore[attr-defined]
            return node
        if name in self.scope.objects:
            return Node(lambda env: rows_of(env.data, name), f"rows:{name}", (name,))
        options = list(self.scope.objects) + (list(self.scope.objects[row_obj]["fields"]) if row_obj else [])
        where = f"a field of {row_obj} or an object" if row_obj else "an object (fields are written Object.field at document level)"
        raise _err(f"'{name}' is not {where}.", n, self.scope, code="unknown_name",
                   explanation=(f"Fields of {row_obj}: {', '.join(self.scope.objects[row_obj]['fields'])}. " if row_obj else "")
                   + f"Objects: {', '.join(self.scope.objects)}.", hint=_suggest(name, options))

    def c_Attribute(self, n: ast.Attribute) -> Node:
        if n.attr.startswith("_"):
            raise _err(f"'{n.attr}' is not allowed.", n, self.scope, code="not_allowed",
                       explanation="Names that start with an underscore are reserved.")
        base = n.value
        # Object.field
        if isinstance(base, ast.Name) and base.id in self.scope.objects and not (
                self.scope.row_object and base.id in self.scope.objects[self.scope.row_object]["fields"]):
            obj = base.id
            spec = _field_spec(self.scope, obj, n.attr, n)
            ftype = FIELD_TYPES.get(spec.get("type", "string"), TEXT)
            if self.scope.objects[obj]["multiple"]:
                attr = n.attr
                node = Node(lambda env: [r.get(attr) for r in rows_of(env.data, obj)], f"list:{ftype}", (f"{obj}.{attr}",))
            else:
                node = Node(_read_field(_single_row(obj), n.attr), ftype, (f"{obj}.{n.attr}",))
            node.spec = spec  # type: ignore[attr-defined]
            return node
        # ref.field: the base is a field that resolves_to an object
        inner = self.compile(base)
        spec = getattr(inner, "spec", None)
        if spec and spec.get("resolves_to") in self.scope.objects:
            if _is_list(inner.type):
                raise _err("Follow a reference from one row at a time.", n, self.scope, code="many_not_value",
                           hint="Scope the rule to that object, then write field.other_field.")
            return _reference(self.scope, inner, spec, n.attr, n)
        raise _err(f"'.{n.attr}' can only follow an object or a field that refers to another object.", n, self.scope,
                   code="bad_path", explanation="Write Object.field, or ref.field where ref is a field with resolves_to.")

    def c_List(self, n: ast.List) -> Node:
        items = [self.compile(e) for e in n.elts]
        if not all(i.constant for i in items):
            raise _err("A list may only hold written values, like [\"A\", \"B\"].", n, self.scope, code="not_allowed")
        values = [_constant(i) for i in items]
        return Node(lambda env: values, "list:" + (items[0].type if items else ANY), constant=True)

    c_Tuple = c_List

    # --- operators ----------------------------------------------------------

    def c_BinOp(self, n: ast.BinOp) -> Node:
        if type(n.op) not in formula._BIN:
            raise _err("This operator is not allowed.", n, self.scope, code="not_allowed",
                       hint="Use + - * / % // **.")
        left, right = _scalar(self.compile(n.left), n.left, self.scope), _scalar(self.compile(n.right), n.right, self.scope)
        text_join = isinstance(n.op, ast.Add) and left.type == TEXT and right.type == TEXT
        if not text_join:
            for side, at in ((left, n.left), (right, n.right)):
                if side.type not in (NUMBER, ANY):
                    raise _err(f"Arithmetic needs numbers, but this is {TYPE_WORDS.get(side.type, side.type)}.", at, self.scope,
                               code="type_mismatch", hint="Use number(x) if the field holds digits as text.")
        op = formula._BIN[type(n.op)]
        lf, rf = left.fn, right.fn

        def run(env: Env) -> Any:
            env.tick()
            a, b = lf(env), rf(env)
            if _blank(a) or _blank(b):
                return None
            return _limit_text(op(a, b))
        return Node(run, TEXT if text_join else NUMBER, left.reads + right.reads)

    def c_UnaryOp(self, n: ast.UnaryOp) -> Node:
        operand = _scalar(self.compile(n.operand), n.operand, self.scope)
        f = operand.fn
        if isinstance(n.op, ast.Not):
            def run_not(env: Env) -> Any:
                env.tick()
                v = f(env)
                return None if v is None else not _truth(v)
            return Node(run_not, BOOL, operand.reads)
        if isinstance(n.op, (ast.USub, ast.UAdd)):
            if operand.type not in (NUMBER, ANY):
                raise _err("A minus sign needs a number.", n, self.scope, code="type_mismatch")
            neg = isinstance(n.op, ast.USub)

            def run_sign(env: Env) -> Any:
                env.tick()
                v = formula._number(f(env))
                return None if v is None else formula._clean(-v if neg else v)
            return Node(run_sign, NUMBER, operand.reads, constant=operand.constant)
        raise _err("This operator is not allowed.", n, self.scope, code="not_allowed")

    def c_BoolOp(self, n: ast.BoolOp) -> Node:
        parts = [_scalar(self.compile(v), v, self.scope) for v in n.values]
        fns = [p.fn for p in parts]
        is_and = isinstance(n.op, ast.And)

        def run(env: Env) -> Any:
            env.tick()
            unknown = False
            for f in fns:
                try:
                    v = f(env)
                except NotApplicable:
                    v = None
                if v is None:
                    unknown = True
                    continue
                if _truth(v) != is_and:
                    return not is_and  # and: a false part decides; or: a true part decides
            return None if unknown else is_and
        return Node(run, BOOL, sum((p.reads for p in parts), ()))

    def c_Compare(self, n: ast.Compare) -> Node:
        nodes = [_scalar(self.compile(n.left), n.left, self.scope)]
        # (operator, kind, left, right): kind is the type both sides are compared as, "in" / "not in" for lists,
        # "blank" when one side is the written blank (`x == blank` asks whether x is empty).
        steps: List[Tuple[Optional[type], str, Node, Node]] = []
        for op, comp in zip(n.ops, n.comparators):
            left = nodes[-1]
            if isinstance(op, (ast.In, ast.NotIn)):
                right = self.compile(comp)
                if not (_is_list(right.type) and right.constant):
                    raise _err("'in' needs a written list, like status in [\"A\", \"B\"].", comp, self.scope, code="not_allowed")
                steps.append((type(op), "in", left, right))
                nodes.append(right)
                continue
            if type(op) not in formula._CMP:
                raise _err("This comparison is not allowed.", n, self.scope, code="not_allowed", hint="Use == != < <= > >= or in.")
            right = _scalar(self.compile(comp), comp, self.scope)
            blank_side = any(x.constant and _constant(x) is None for x in (left, right))
            if blank_side and not isinstance(op, (ast.Eq, ast.NotEq)):
                raise _err("Blank can only be compared with == or !=.", n, self.scope, code="type_mismatch", hint="isempty(x)")
            if not blank_side and not _compat(left.type, right.type):
                raise _err(f"This compares {TYPE_WORDS.get(left.type, left.type)} with {TYPE_WORDS.get(right.type, right.type)}.",
                           n, self.scope, code="type_mismatch",
                           explanation="Both sides of a comparison must be the same kind of value.",
                           hint="Use number(x) or text(x) to turn one side into the other's kind.")
            steps.append((type(op), "blank" if blank_side else _compare_type(left, right), left, right))
            nodes.append(right)

        def run(env: Env) -> Any:
            env.tick()
            unknown = False
            for op, kind, left, right in steps:
                a = left.fn(env)
                if kind == "in":
                    if _blank(a):
                        unknown = True
                        continue
                    hit = str(a).strip().lower() in {str(v).strip().lower() for v in right.fn(env)}
                    if hit != (op is ast.In):
                        return False
                    continue
                b = right.fn(env)
                if kind == "blank":
                    same = _blank(a) == _blank(b)
                    if same != (op is ast.Eq):
                        return False
                    continue
                if _blank(a) or _blank(b):
                    unknown = True
                    continue
                cmp = formula._CMP[op]
                x, y = _typed(a, kind), _typed(b, kind)
                if kind in (TEXT, ANY):
                    x, y = str(x).strip().lower(), str(y).strip().lower()
                    nx, ny = formula.parse_number(x), formula.parse_number(y)
                    if nx is not None and ny is not None:
                        x, y = nx, ny
                if not cmp(x, y):
                    return False
            return None if unknown else True
        return Node(run, BOOL, sum((x.reads for x in nodes), ()))

    def c_IfExp(self, n: ast.IfExp) -> Node:
        test = _scalar(self.compile(n.test), n.test, self.scope)
        body = _scalar(self.compile(n.body), n.body, self.scope)
        other = _scalar(self.compile(n.orelse), n.orelse, self.scope)
        rtype = body.type if body.type == other.type else ANY

        def run(env: Env) -> Any:
            env.tick()
            t = test.fn(env)
            if t is None:
                return None
            return body.fn(env) if _truth(t) else other.fn(env)
        return Node(run, rtype, test.reads + body.reads + other.reads)

    # --- calls --------------------------------------------------------------

    def c_Call(self, n: ast.Call) -> Node:
        if not isinstance(n.func, ast.Name):
            raise _err("Only the listed functions can be called.", n, self.scope, code="unknown_function")
        name = n.func.id
        if n.keywords:
            raise _err("Give function values in order, without names.", n, self.scope, code="not_allowed",
                       hint=f"Like {SCALAR[name].usage if name in SCALAR else AGGREGATES[name][2] if name in AGGREGATES else name + '(a, b)'}.")
        if any(isinstance(a, ast.Starred) for a in n.args):
            raise _err("'*' is not allowed in a function call.", n, self.scope, code="not_allowed")
        if name in AGGREGATES:
            return self._aggregate(name, n)
        if name not in SCALAR:
            raise _err(f"'{name}' is not a function rules know.", n.func, self.scope, code="unknown_function",
                       explanation=f"Functions: {', '.join(sorted(list(SCALAR) + list(AGGREGATES)))}.",
                       hint=_suggest(name, list(SCALAR) + list(AGGREGATES)))
        sig = SCALAR[name]
        need, given = len(sig.args), len(n.args)
        if sig.variadic:
            if given < 1:
                raise _err(f"{name} needs at least one value.", n, self.scope, code="bad_arguments", hint=sig.usage)
        elif not need - sig.optional <= given <= need:
            raise _err(f"{name} takes {need - sig.optional if sig.optional else need}"
                       f"{' or ' + str(need) if sig.optional else ''} value{'s' if need != 1 else ''}, got {given}.",
                       n, self.scope, code="bad_arguments", hint=sig.usage)
        args = [_scalar(self.compile(a), a, self.scope) for a in n.args]
        for i, (a, at) in enumerate(zip(args, n.args)):
            want = sig.args[min(i, need - 1)]
            if not _accepts(want, a.type):
                raise _err(f"{name} needs {TYPE_WORDS[want]} here, but this is {TYPE_WORDS.get(a.type, a.type)}.", at, self.scope,
                           code="type_mismatch", hint=sig.usage)
        reads = sum((a.reads for a in args), ())
        if name == "today":
            return Node(lambda env: env.today, DATE)
        if name == "matches":
            return self._matches(n, args, reads)
        impl = formula.FUNCTIONS[name]
        fns = [a.fn for a in args]

        def run(env: Env) -> Any:
            env.tick()
            try:
                return _limit_text(impl(*[f(env) for f in fns]))
            except Blank:
                raise NotApplicable()
        return Node(run, sig.result if sig.result != ANY else (args[1].type if name == "if_" and len(args) > 1 else ANY), reads)

    def _matches(self, n: ast.Call, args: List[Node], reads: Tuple[str, ...]) -> Node:
        if not args[1].constant:
            raise _err("The pattern must be written in the rule, in quotes.", n.args[1], self.scope, code="not_allowed",
                       hint='matches(number, "INV-[0-9]{6}")')
        pattern = formula._text(_constant(args[1]))
        if len(pattern) > 300:
            raise _err("The pattern is too long.", n.args[1], self.scope, code="too_long")
        try:
            compiled = re2.compile(pattern)
        except re2.error as exc:
            raise _err(f"The pattern is not valid: {exc}.", n.args[1], self.scope, code="bad_pattern",
                       explanation="Patterns use RE2 syntax (like most regular expressions, without back-references or look-arounds).")
        text = args[0].fn

        def run(env: Env) -> Any:
            env.tick()
            v = text(env)
            return None if _blank(v) else compiled.fullmatch(formula._text(v).strip()) is not None
        return Node(run, BOOL, reads)

    def _aggregate(self, name: str, n: ast.Call) -> Node:
        kind, rtype, usage = AGGREGATES[name]
        if kind == "rows_cond":
            if len(n.args) != 2:
                raise _err(f"{name} takes an object and a condition.", n, self.scope, code="bad_arguments", hint=usage)
        elif len(n.args) != 1:
            raise _err(f"{name} takes one value.", n, self.scope, code="bad_arguments", hint=usage)
        first = self.compile(n.args[0])
        if kind == "values" or (kind == "rows_or_values" and not _is_rows(first.type)):
            if not _is_list(first.type):
                if _is_rows(first.type):
                    raise _err(f"{name} needs a field of {first.type[5:]}, not its rows.", n.args[0], self.scope,
                               code="bad_arguments", hint=usage)
                raise _err(f"{name} works over the rows of an object with many rows: write Object.field.", n.args[0], self.scope,
                           code="bad_arguments", hint=usage)
            vtype = first.type[5:]
            if name in ("sum", "avg") and vtype not in (NUMBER, ANY):
                raise _err(f"{name} needs numbers, but these are {TYPE_WORDS.get(vtype, vtype)}.", n.args[0], self.scope,
                           code="type_mismatch", hint=usage)
            get = first.fn
            return Node(_values_aggregate(name, get, vtype), vtype if name in ("min_of", "max_of") else rtype, first.reads)
        if not _is_rows(first.type):
            raise _err(f"{name} needs an object's name first, like {usage}.", n.args[0], self.scope, code="bad_arguments")
        obj = first.type[5:]
        if kind == "rows_or_values":
            return Node(lambda env: len(rows_of(env.data, obj)), NUMBER, first.reads)
        if self.scope.depth >= MAX_NESTED_AGGREGATES:
            raise _err("Too many conditions inside conditions.", n, self.scope, code="too_complex", hint="Split it into two rules.")
        inner = _Compiler(Scope(self.scope.objects, obj, self.scope.part, self.scope.depth + 1))
        inner.count = self.count
        cond = _scalar(inner.compile(n.args[1]), n.args[1], inner.scope)
        self.count = inner.count
        return Node(_rows_aggregate(name, obj, cond.fn), rtype, first.reads + cond.reads)


def _constant(node: Node) -> Any:
    """The value of a written constant (a number, text, -5), worked out once while compiling."""
    return node.fn(Env({}, None, date.today()))


def _truth(v: Any) -> bool:
    if isinstance(v, str):
        return v.strip().lower() not in ("", "false", "no", "0")
    return bool(v)


def _values_aggregate(name: str, get: Fn, vtype: str) -> Fn:
    def run(env: Env) -> Any:
        env.tick()
        raw = [v for v in get(env) if not _blank(v)]
        for _ in raw:
            env.tick()
        if name == "count":
            return len(raw)
        if name in ("count_distinct", "unique"):
            keys = [str(v).strip().lower() for v in raw]
            return len(set(keys)) if name == "count_distinct" else len(set(keys)) == len(keys)
        typed = [_typed(v, vtype if vtype != ANY else NUMBER) for v in raw]
        if name == "sum":
            return formula._clean(sum(typed)) if typed else 0
        if not typed:
            return None
        if name == "avg":
            return formula._clean(sum(typed) / len(typed))
        pick = typed.index(min(typed) if name == "min_of" else max(typed))
        return raw[pick]
    return run


def _rows_aggregate(name: str, obj: str, cond: Fn) -> Fn:
    def run(env: Env) -> Any:
        env.tick()
        results = []
        for row in rows_of(env.data, obj):
            sub = env.inner(row)
            try:
                results.append(cond(sub))
            except NotApplicable:
                results.append(None)
            env.steps = sub.steps
            env.tick()
        if name == "count_if":
            return sum(1 for r in results if r is not None and _truth(r))
        known = [_truth(r) for r in results if r is not None]
        if name == "all":
            return False if not all(known) else (None if len(known) < len(results) else True)
        return True if any(known) else (None if len(known) < len(results) else False)
    return run


def _describe(n: ast.AST) -> str:
    return {
        "Subscript": "square brackets after a value", "Lambda": "lambda", "ListComp": "a list comprehension",
        "SetComp": "a set comprehension", "DictComp": "a dict comprehension", "GeneratorExp": "a generator",
        "JoinedStr": "an f-string", "NamedExpr": "':='", "Dict": "a dict", "Set": "a set", "Await": "await",
        "Yield": "yield", "YieldFrom": "yield", "Starred": "'*'", "Slice": "a slice",
    }.get(type(n).__name__, type(n).__name__)


def _parse(source: str, scope: Scope) -> ast.Expression:
    text = (source or "").strip()
    if not text:
        raise RuleError("This part of the rule is empty.", code="empty", part=scope.part)
    if len(text) > MAX_LENGTH:
        raise RuleError(f"The rule is too long (at most {MAX_LENGTH} characters).", code="too_long", part=scope.part)
    if "\n" in text:
        text = " ".join(text.split())
    try:
        return ast.parse(text, mode="eval")
    except SyntaxError as exc:
        col = (exc.offset or 1) - 1
        return_hint = "Check brackets, quotes and commas."
        if "=" in text and "==" not in text and not any(op in text for op in ("<=", ">=", "!=")):
            return_hint = "To compare, write == (two equals signs)."
        raise RuleError("This is not a rule I can read.", code="syntax", part=scope.part,
                        explanation=f"Python's parser stopped here: {exc.msg}.", hint=return_hint, col=col, end_col=col + 1)


def compile_rule(rule: Dict[str, Any], schema: Optional[Dict[str, Any]]) -> CompiledRule:
    """Checks a rule against the template and prepares it. Raises RuleError with the first problem."""
    objects = normalize_schema(schema)
    if not objects:
        raise RuleError("The template has no objects yet, so there is nothing a rule can check.", code="no_template", part="scope")
    scope_name = (rule.get("scope") or "document").strip()
    if scope_name.lower() == "document":
        row_object = None
    elif scope_name in objects:
        row_object = scope_name
    else:
        raise RuleError(f"'{scope_name}' is not an object of the template.", code="unknown_object", part="scope",
                        explanation=f"Use 'document', or one of: {', '.join(objects)}.", hint=_suggest(scope_name, objects))
    severity = rule.get("severity") or "error"
    if severity not in SEVERITIES:
        raise RuleError("severity must be 'error' or 'warning'.", code="bad_severity", part="severity")

    def build(part: str, source: str) -> Node:
        scope = Scope(objects, row_object, part)
        node = _scalar(_Compiler(scope).compile(_parse(source, scope).body), None, scope)  # type: ignore[arg-type]
        if node.type not in (BOOL, ANY):
            raise RuleError(f"The {part} must be a yes/no question, but this gives {TYPE_WORDS.get(node.type, node.type)}.",
                            code="not_boolean", part=part,
                            explanation="A rule passes when its check is true and fails when it is false.",
                            hint="Compare it with something, for example: ... >= 0, or ... == Object.field.")
        return node

    check = build("check", rule.get("check") or "")
    when = build("when", rule["when"]) if (rule.get("when") or "").strip() else None
    reads = sorted(set(check.reads + (when.reads if when else ())))
    return CompiledRule(rule=rule, scope=row_object, check=check, when=when, reads=reads)


def problems(rule: Dict[str, Any], schema: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """[] when the rule compiles; otherwise the problem, shaped for the editor and the agent."""
    try:
        compile_rule(rule, schema)
    except RuleError as exc:
        return [exc.to_dict()]
    return []
