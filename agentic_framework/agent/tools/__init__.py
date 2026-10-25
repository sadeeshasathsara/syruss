"""
The agent's tools. Each one is a capability, not a step: the agent decides when to use them.

Business tools call the core server's Agent Tool API (which enforces authority and scope).
Free-form objects are passed as JSON strings so the tool schemas work with every model provider.

One module per area; importing this package registers every tool (base.TOOLS), and registry
decides which tools each kind of run gets.
"""
from agentic_framework.agent.tools import (  # noqa: F401  (each module registers its tools)
    calc, documents, records, entities, people, template, sources, memory, guide, screens,
)
from agentic_framework.agent.tools.base import TOOLS, RunContext, Tool, ToolOutcome, tool
from agentic_framework.agent.tools.calc import safe_calculate
from agentic_framework.agent.tools.documents import budget_note
from agentic_framework.agent.tools.memory import save_memory
from agentic_framework.agent.tools.registry import (
    ANALYST_TOOLS, CHAT_TOOLS, EVAL_TOOLS, TOOLSETS, WORK_CORE, execute, tool_schemas, work_tools,
)

__all__ = [
    "TOOLS", "RunContext", "Tool", "ToolOutcome", "tool", "safe_calculate", "budget_note", "save_memory",
    "ANALYST_TOOLS", "CHAT_TOOLS", "EVAL_TOOLS", "TOOLSETS", "WORK_CORE", "execute", "tool_schemas", "work_tools",
]
