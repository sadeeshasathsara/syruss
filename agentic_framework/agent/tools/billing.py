"""
Billing, read-only: the organization's plan, its invoices waiting for payment, and the last bank slip sent, so a chat can
answer "is my invoice paid?" from facts. Only for a person who manages the organization's billing; the platform refuses
anyone else, and any work run.
"""
from agentic_framework.agent.tools.base import NoArgs, RunContext, ToolOutcome, dump, tool


@tool("get_billing_status", "The organization's billing as it stands: its plan, invoices waiting for payment (number, total, amount "
      "still due in cents, status, due date) and the latest payment slip sent (being checked, confirmed, or not confirmed and why). "
      "Only for a person who manages billing, in their chat; read the help-billing guide to explain what it means.", NoArgs)
async def get_billing_status(ctx: RunContext, _: NoArgs) -> ToolOutcome:
    return ToolOutcome(dump(await ctx.api.get("/billing/status")))
