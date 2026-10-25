"""Reading a guide: detailed guidance for a kind of work, when the work calls for it."""
from pydantic import BaseModel, Field
from agentic_framework.agent import guides
from agentic_framework.agent.tools.base import RunContext, ToolOutcome, tool


class GuideArgs(BaseModel):
    name: str = Field(..., description="; ".join(f"{k}: {v}" for k, v in guides.GUIDES.items()))


@tool("read_guide", "Read detailed guidance for a kind of work, when the work calls for it (once per run).", GuideArgs)
async def read_guide(ctx: RunContext, a: GuideArgs) -> ToolOutcome:
    if a.name in ctx.guides_read:
        return ToolOutcome(f"The {a.name} guide is already above in this run.")
    text = guides.read(a.name)
    ctx.guides_read.add(a.name)
    return ToolOutcome(text)
