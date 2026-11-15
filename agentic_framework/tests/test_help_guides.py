"""Help on using Syruss itself: guides the chat reads before answering product questions, and billing as it stands."""
from agentic_framework.agent import guides
from agentic_framework.agent.charter import ASSISTANT_CHARTER, BOUNDARIES, CHARTER
from agentic_framework.agent.tools import CHAT_TOOLS, EVAL_TOOLS, TOOLSETS, RunContext, execute, work_tools
from agentic_framework.tests.conftest import make_event

EXPECTED = ("help-getting-started", "help-documents", "help-billing", "help-team-and-roles", "help-account-security",
            "help-integrations", "help-analytics")


def test_every_help_guide_is_listed_in_the_chat_charter():
    assert set(guides.HELP) == set(EXPECTED)
    section = ASSISTANT_CHARTER.split("# Helping with Syruss", 1)[1]
    for name in EXPECTED:
        assert f"- {name}:" in section
    # The shared boundaries stay last; the work charter doesn't carry the chat's help list.
    assert ASSISTANT_CHARTER.endswith(BOUNDARIES) and ASSISTANT_CHARTER.index("# Helping with Syruss") < ASSISTANT_CHARTER.index(BOUNDARIES)
    assert "- help-billing:" not in CHARTER


async def test_every_help_guide_is_readable_through_read_guide(core, memory):
    ctx = RunContext(event=make_event("human.message", {"conversation_id": "c1", "conversation_kind": "chat"}), api=core.api(), memory=memory)
    for name in EXPECTED:
        text = (await execute(ctx, "read_guide", {"name": name}, CHAT_TOOLS)).text
        assert text.startswith("# Help") and not text.startswith("ERROR")
    assert "already above" in (await execute(ctx, "read_guide", {"name": "help-billing"}, CHAT_TOOLS)).text


def test_help_guides_name_the_real_pages():
    billing = guides.read("help-billing")
    for words in ("Settings → Billing", "Invoices", "Transfer reference", "Being checked", "Not confirmed", "grace period",
                  "Free plan", "Stripe", "Billing details"):
        assert words in billing
    security = guides.read("help-account-security")
    for words in ("Account and security → Sign-in", "Keep me signed in", "30 days", "Trusted devices", "Google"):
        assert words in security


def test_billing_status_is_for_chats_only():
    assert "read_guide" in CHAT_TOOLS and "get_billing_status" in CHAT_TOOLS
    assert "get_billing_status" not in EVAL_TOOLS
    assert all("get_billing_status" not in tools for tools in TOOLSETS.values())
    for event in (make_event("human.answered", {"question_id": "q1"}), make_event("human.message", {"conversation_id": "c1"}),
                  make_event("document.submitted", {"document_id": "d1"})):
        assert "get_billing_status" not in work_tools(event)
