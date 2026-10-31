"""
Formulas for the calculated columns of custom screens.

A formula is one expression over the other columns of the same row, written the way a spreadsheet user would:

    round(hours_between(in_time, out_time) - 8, 1)          overtime in hours
    amount * 0.15                                           tax
    "Late" if days_between(due_date, today) > 0 else "On time"

It is parsed, never run: only the elements below are understood, so a formula cannot reach anything but the values it is
given. Blank cells stay blank through arithmetic, and anything that cannot be worked out (dividing by zero, text where a number
is needed) gives a blank cell and is counted, never a failed screen.

  values       numbers, "text", true/false, blank (None)
  operators    + - * / % //  **(small exponents)  comparisons  and or not   `a if condition else b`
  functions    abs min max round floor ceil sqrt  if_ coalesce isempty
               number text concat upper lower trim len contains left right
               hours_between minutes_between year month day weekday days_between
"""
import ast
import math
import operator
from datetime import date, datetime
from typing import Any, Callable, Dict, FrozenSet, List, Optional, Set
from app.services.rule_engine import parse_number, parse_time_minutes

MAX_LENGTH = 400
MAX_NODES = 150
MAX_EXPONENT = 12


class FormulaError(ValueError):
    """A formula that cannot be used, with a sentence a person can act on."""


class Blank(Exception):
    """This cell cannot be worked out (not a failure of the screen): it is shown blank."""


def _number(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    parsed = parse_number(value)
    if parsed is None:
        raise Blank(f"'{value}' is not a number")
    return float(parsed)


def _clean(x: float) -> Any:
    """Whole results read as whole numbers."""
    if isinstance(x, float) and x.is_integer() and abs(x) < 1e15:
        return int(x)
    return x


def _arith(op: Callable[[float, float], float]) -> Callable[[Any, Any], Any]:
    def run(a: Any, b: Any) -> Any:
        if a is None or b is None:
            return None
        if op is operator.add and isinstance(a, str) and isinstance(b, str) and parse_number(a) is None and parse_number(b) is None:
            return a + b
        x, y = _number(a), _number(b)
        if x is None or y is None:
            return None
        try:
            return _clean(op(x, y))
        except ZeroDivisionError:
            raise Blank("division by zero")
        except OverflowError:
            raise Blank("the number is too large")
    return run


def _power(a: Any, b: Any) -> Any:
    x, y = _number(a), _number(b)
    if x is None or y is None:
        return None
    if abs(y) > MAX_EXPONENT:
        raise Blank(f"exponents above {MAX_EXPONENT} are not allowed")
    try:
        return _clean(x ** y)
    except (ZeroDivisionError, OverflowError, ValueError):
        raise Blank("that power cannot be worked out")


_BIN = {
    ast.Add: _arith(operator.add), ast.Sub: _arith(operator.sub), ast.Mult: _arith(operator.mul),
    ast.Div: _arith(operator.truediv), ast.Mod: _arith(operator.mod), ast.FloorDiv: _arith(operator.floordiv), ast.Pow: _power,
}


def _compare(op: Callable[[Any, Any], bool], a: Any, b: Any) -> bool:
    if a is None or b is None:
        return op is operator.eq and a is None and b is None or op is operator.ne and (a is None) != (b is None)
    if isinstance(a, str) and isinstance(b, str) and (parse_number(a) is None or parse_number(b) is None):
        return op(a.strip().lower(), b.strip().lower())
    try:
        return op(_number(a), _number(b))
    except Blank:
        return op(str(a).strip().lower(), str(b).strip().lower())


_CMP = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.Lt: operator.lt, ast.LtE: operator.le, ast.Gt: operator.gt, ast.GtE: operator.ge}


# --- dates and times ---------------------------------------------------------------

def _date(value: Any) -> Optional[date]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d.%m.%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise Blank(f"'{value}' is not a date")


def _minutes(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    minutes = parse_time_minutes(value)
    if minutes is None:
        raise Blank(f"'{value}' is not a time")
    return minutes


def _between_times(start: Any, end: Any) -> Optional[float]:
    a, b = _minutes(start), _minutes(end)
    if a is None or b is None:
        return None
    return (b - a) % (24 * 60)  # a shift that ends after midnight still counts forwards


def _hours_between(start: Any, end: Any) -> Any:
    m = _between_times(start, end)
    return None if m is None else _clean(round(m / 60, 4))


def _minutes_between(start: Any, end: Any) -> Any:
    m = _between_times(start, end)
    return None if m is None else _clean(m)


def _days_between(start: Any, end: Any) -> Any:
    a, b = _date(start), _date(end)
    return None if a is None or b is None else (b - a).days


def _part(read: Callable[[date], Any]) -> Callable[[Any], Any]:
    def run(value: Any) -> Any:
        d = _date(value)
        return None if d is None else read(d)
    return run


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _round(value: Any, digits: Any = 0) -> Any:
    x, n = _number(value), _number(digits)
    if x is None:
        return None
    return _clean(round(x + 0.0, int(n or 0)))


def _unary_math(fn: Callable[[float], float]) -> Callable[[Any], Any]:
    def run(value: Any) -> Any:
        x = _number(value)
        if x is None:
            return None
        try:
            return _clean(fn(x))
        except ValueError:
            raise Blank("that cannot be worked out")
    return run


def _extreme(pick: Callable[..., Any]) -> Callable[..., Any]:
    def run(*args: Any) -> Any:
        values = [v for v in (_number(a) for a in args) if v is not None]
        return _clean(pick(values)) if values else None
    return run


def _coalesce(*args: Any) -> Any:
    for a in args:
        if a is not None and a != "":
            return a
    return None


FUNCTIONS: Dict[str, Callable[..., Any]] = {
    "abs": _unary_math(abs), "floor": _unary_math(math.floor), "ceil": _unary_math(math.ceil), "sqrt": _unary_math(math.sqrt),
    "round": _round, "min": _extreme(min), "max": _extreme(max),
    "if_": lambda c, a, b=None: a if c else b, "coalesce": _coalesce, "isempty": lambda v: v is None or str(v).strip() == "",
    "number": lambda v: _number(v), "text": _text, "concat": lambda *a: "".join(_text(x) for x in a),
    "upper": lambda v: None if v is None else _text(v).upper(), "lower": lambda v: None if v is None else _text(v).lower(),
    "trim": lambda v: None if v is None else _text(v).strip(), "len": lambda v: 0 if v is None else len(_text(v)),
    "contains": lambda t, p: _text(p).strip().lower() in _text(t).lower(),
    "left": lambda t, n: _text(t)[: int(_number(n) or 0)], "right": lambda t, n: _text(t)[-int(_number(n) or 0):] if _number(n) else "",
    "hours_between": _hours_between, "minutes_between": _minutes_between, "days_between": _days_between,
    "year": _part(lambda d: d.year), "month": _part(lambda d: d.month), "day": _part(lambda d: d.day),
    "weekday": _part(lambda d: d.strftime("%a")),
}
FUNCTION_HELP: Dict[str, str] = {
    "abs": "abs(x)", "floor": "floor(x)", "ceil": "ceil(x)", "sqrt": "sqrt(x)", "round": "round(x, digits)", "min": "min(a, b, ...)",
    "max": "max(a, b, ...)", "if_": "if_(condition, then, otherwise)", "coalesce": "coalesce(a, b, ...) the first one that is not blank",
    "isempty": "isempty(x)", "number": "number(x) text as a number", "text": "text(x)", "concat": "concat(a, b, ...)",
    "upper": "upper(text)", "lower": "lower(text)", "trim": "trim(text)", "len": "len(text)", "contains": "contains(text, part)",
    "left": "left(text, n)", "right": "right(text, n)", "hours_between": "hours_between(start, end) times like 07:30, past midnight too",
    "minutes_between": "minutes_between(start, end)", "days_between": "days_between(from_date, to_date)",
    "year": "year(date)", "month": "month(date)", "day": "day(date)", "weekday": "weekday(date) as Mon, Tue ...",
}


# --- compiling and running ---------------------------------------------------------

class Compiled:
    def __init__(self, source: str, tree: ast.Expression, names: FrozenSet[str]):
        self.source = source
        self._tree = tree
        self.names = names

    def run(self, values: Dict[str, Any]) -> Any:
        """The formula's value for one row. Raises Blank when this cell cannot be worked out."""
        return _eval(self._tree.body, values)


def compile_formula(source: str, allowed: Optional[Set[str]] = None) -> Compiled:
    """Checks a formula and prepares it. `allowed` are the names it may use (the row's columns and fields)."""
    text = (source or "").strip()
    if not text:
        raise FormulaError("The formula is empty.")
    if len(text) > MAX_LENGTH:
        raise FormulaError(f"The formula is too long (at most {MAX_LENGTH} characters).")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError:
        raise FormulaError("This is not a formula I can read. Check brackets, quotes and commas.")
    names: Set[str] = set()
    count = 0
    for node in ast.walk(tree):
        count += 1
        if count > MAX_NODES:
            raise FormulaError("The formula is too long and complicated. Split it into two columns.")
        if isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Load) and node.id not in FUNCTIONS:
                names.add(node.id)
        elif isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in FUNCTIONS:
                called = node.func.id if isinstance(node.func, ast.Name) else "that"
                raise FormulaError(f"'{called}' is not a function I know. Functions: {', '.join(sorted(FUNCTIONS))}.")
            if node.keywords:
                raise FormulaError("Give function values in order, without names.")
        elif isinstance(node, ast.Constant):
            if not isinstance(node.value, (int, float, str, bool, type(None))):
                raise FormulaError("Only numbers, text, true/false and blank can be written in a formula.")
        elif not isinstance(node, (ast.Expression, ast.BinOp, ast.UnaryOp, ast.BoolOp, ast.Compare, ast.IfExp, ast.Load,
                                   ast.And, ast.Or, ast.Not, ast.USub, ast.UAdd, *_BIN, *_CMP)):
            raise FormulaError(f"This formula uses something not allowed ({type(node).__name__}). Keep to numbers, text, columns, operators and functions.")
    if allowed is not None:
        unknown = sorted(names - allowed)
        if unknown:
            raise FormulaError(f"Unknown column {', '.join(repr(n) for n in unknown)}. Columns you can use: {', '.join(sorted(allowed)) or 'none'}.")
    return Compiled(text, tree, frozenset(names))


def _eval(node: ast.AST, values: Dict[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return values.get(node.id)
    if isinstance(node, ast.BinOp):
        return _BIN[type(node.op)](_eval(node.left, values), _eval(node.right, values))
    if isinstance(node, ast.UnaryOp):
        v = _eval(node.operand, values)
        if isinstance(node.op, ast.Not):
            return not v
        n = _number(v)
        return None if n is None else _clean(-n if isinstance(node.op, ast.USub) else n)
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            result: Any = True
            for part in node.values:
                result = _eval(part, values)
                if not result:
                    return result
            return result
        for part in node.values:
            result = _eval(part, values)
            if result:
                return result
        return result
    if isinstance(node, ast.Compare):
        left = _eval(node.left, values)
        for op, right_node in zip(node.ops, node.comparators):
            right = _eval(right_node, values)
            if not _compare(_CMP[type(op)], left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.IfExp):
        return _eval(node.body, values) if _eval(node.test, values) else _eval(node.orelse, values)
    if isinstance(node, ast.Call):
        return FUNCTIONS[node.func.id](*[_eval(a, values) for a in node.args])  # type: ignore[attr-defined]
    raise Blank("this cannot be worked out")


def evaluate(source: str, values: Dict[str, Any]) -> Any:
    """One-off: compile and run (used for checks and tests)."""
    return compile_formula(source).run(values)


def functions_help() -> List[Dict[str, str]]:
    return [{"name": n, "usage": u} for n, u in sorted(FUNCTION_HELP.items())]
