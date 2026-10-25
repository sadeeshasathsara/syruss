"""Exact arithmetic: the agent never does sums in its head."""
import ast
import math
import operator
from pydantic import BaseModel, Field
from agentic_framework.agent.tools.base import RunContext, ToolOutcome, dump, tool


_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Mod: operator.mod, ast.Pow: operator.pow, ast.FloorDiv: operator.floordiv}
_FUNCS = {"abs": abs, "min": min, "max": max, "round": round, "sqrt": math.sqrt, "sum": sum}


def safe_calculate(expression: str) -> float:
    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            return -ev(node.operand) if isinstance(node.op, ast.USub) else ev(node.operand)
        if isinstance(node, (ast.List, ast.Tuple)):
            return [ev(e) for e in node.elts]
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS:
            return _FUNCS[node.func.id](*[ev(a) for a in node.args])
        raise ValueError(f"unsupported element {type(node).__name__}")
    return ev(ast.parse(expression, mode="eval"))


class CalcArgs(BaseModel):
    expression: str = Field(..., description="Arithmetic, e.g. '(19*60+30 - 7*60) / 60' or 'sum([8, 12, 12])'")


@tool("calculate", "Exact arithmetic.", CalcArgs)
async def calculate(ctx: RunContext, a: CalcArgs) -> ToolOutcome:
    return ToolOutcome(dump({"expression": a.expression, "result": safe_calculate(a.expression)}))
