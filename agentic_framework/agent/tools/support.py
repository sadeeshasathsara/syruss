"""Getting a person help from the Syruss team: a support ticket, opened for them from their chat."""
from typing import Optional
from pydantic import BaseModel, Field
from agentic_framework.agent.tools.base import RunContext, ToolOutcome, dump, tool


class TicketArgs(BaseModel):
    subject: str = Field(..., description="The problem in a few words, as the person would put it")
    summary: str = Field(..., description="For the support team: what they asked, what you checked and found, what you already "
                         "tried, and what they need. Name documents and bills the way people know them")
    category: str = Field("other", description="billing, account, technical or other")
    document_id: Optional[str] = Field(None, description="A document of this workspace the problem is about")
    bill_number: Optional[str] = Field(None, description="The number of a bill from Syruss to their organization the problem is about (SYR-2026-00042)")


@tool("create_support_ticket", "Open a support ticket for the person you are chatting with, when they need the Syruss team: a "
      "problem you can't solve (a payment that went wrong, something broken, their account), or they ask for a person. Ask "
      "first unless they already asked for help, and write a summary the team can act on without asking again. Then tell "
      "them its number and that answers come by email and under Settings > Help & support.", TicketArgs)
async def create_support_ticket(ctx: RunContext, a: TicketArgs) -> ToolOutcome:
    body = a.model_dump(exclude_none=True, exclude={"bill_number"})
    if a.bill_number:
        body["invoice_number"] = a.bill_number
    result = await ctx.api.post("/support/tickets", body)
    return ToolOutcome(dump(result))
