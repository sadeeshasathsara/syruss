"""The calculator does sums a document might ask for, and refuses work that would stall the worker."""
import pytest
from agentic_framework.agent.tools.calc import safe_calculate


@pytest.mark.parametrize("expression, result", [
    ("(19*60+30 - 7*60) / 60", 12.5), ("sum([8, 12, 12])", 32), ("round(10/3, 2)", 3.33), ("2**10", 1024), ("-3**2", -9),
    ("max(1, 2.5)", 2.5), ("abs(-4)", 4), ("sqrt(16)", 4.0),
])
def test_everyday_arithmetic(expression, result):
    assert safe_calculate(expression) == result


@pytest.mark.parametrize("expression", [
    "9**9**9**9", "10**200", "2**65", "[1] * 10**9", "sum([1]*5)", "1e308*10", "True + 1", "__import__('os')", "(1).__class__",
    "max(*[1], key=abs)", "1+" * 300 + "1",
])
def test_work_that_would_stall_the_worker_or_escape_is_refused(expression):
    with pytest.raises((ValueError, SyntaxError)):
        safe_calculate(expression)
