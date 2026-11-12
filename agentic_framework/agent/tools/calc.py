"""Exact arithmetic: the agent never does sums in its head."""
import ast
import math
import operator
from pydantic import BaseModel, Field
from agentic_framework.agent.tools.base import RunContext, ToolOutcome, dump, tool


_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Mod: operator.mod, ast.Pow: operator.pow, ast.FloorDiv: operator.floordiv}
_FUNCS = {"abs": abs, "min": min, "max": max, "round": round, "sqrt": math.sqrt, "sum": sum}


# What a document's text could ask for is bounded: arithmetic on lists ([1] * 10**9) or a tower of powers (9**9**9) would
# otherwise hold the worker's CPU and memory for as long as it takes, in the middle of an agent run.
MAX_EXPRESSION = 500
MAX_EXPONENT = 64
MAX_MAGNITUDE = 1e100
MAX_ITEMS = 1000


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("arithmetic works on numbers (lists only go into sum, min or max)")
    if abs(value) > MAX_MAGNITUDE:
        raise ValueError("the number is too large")
    return value


def safe_calculate(expression: str) -> float:
    if len(expression) > MAX_EXPRESSION:
        raise ValueError(f"expressions can be up to {MAX_EXPRESSION} characters")

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return _number(node.value)
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            left, right = _number(ev(node.left)), _number(ev(node.right))
            if isinstance(node.op, ast.Pow) and (abs(right) > MAX_EXPONENT or (abs(left) > 1 and abs(right) * math.log10(abs(left)) > 100)):
                raise ValueError("the power is too large")
            return _number(_OPS[type(node.op)](left, right))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            value = _number(ev(node.operand))
            return -value if isinstance(node.op, ast.USub) else value
        if isinstance(node, (ast.List, ast.Tuple)):
            if len(node.elts) > MAX_ITEMS:
                raise ValueError(f"lists can hold up to {MAX_ITEMS} numbers")
            return [_number(ev(e)) for e in node.elts]
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS and not node.keywords:
            return _FUNCS[node.func.id](*[ev(a) for a in node.args])
        raise ValueError(f"unsupported element {type(node).__name__}")
    return ev(ast.parse(expression, mode="eval"))


class CalcArgs(BaseModel):
    expression: str = Field(..., description="Arithmetic, e.g. '(19*60+30 - 7*60) / 60' or 'sum([8, 12, 12])'")


@tool("calculate", "Exact arithmetic.", CalcArgs)
async def calculate(ctx: RunContext, a: CalcArgs) -> ToolOutcome:
    return ToolOutcome(dump({"expression": a.expression, "result": safe_calculate(a.expression)}))
