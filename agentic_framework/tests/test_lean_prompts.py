"""What is sent with every model call: a core charter, guides only when needed, the tools the task uses."""
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from agentic_framework.agent import guides
from agentic_framework.agent.briefing import build_briefing
from agentic_framework.agent.charter import CHARTER
from agentic_framework.agent.loop import TRIMMED, _trim_tool_results
from agentic_framework.agent.tools import RunContext, execute, work_tools
from agentic_framework.tests.conftest import make_event


def test_every_guide_exists_and_the_charter_points_to_it():
    for name in guides.GUIDES:
        assert guides.read(name).startswith("# ")
        assert f"- {name}:" in CHARTER
    # The detail lives in the guides, not in the charter sent with every call.
    assert "count_equals" not in CHARTER and "count_equals" in guides.read("template-design")


def test_each_task_gets_the_tools_it_uses():
    document = work_tools(make_event("document.submitted", {"document_id": "d1"}))
    assert "submit_record" in document and "read_guide" in document
    assert "inspect_source" not in document and "process_documents" not in document and "reply_to_user" not in document
    source = work_tools(make_event("knowledge.source_added", {"source_id": "s1"}))
    assert "propose_source_mapping" in source and "submit_record" not in source
    # Follow-ups can lead anywhere: everything except the chat-only tool.
    followup = work_tools(make_event("human.answered", {"question_id": "q1"}))
    assert "submit_record" in followup and "inspect_source" in followup and "process_documents" not in followup


async def test_guides_are_attached_when_the_task_needs_them_and_read_once(core, memory):
    design = RunContext(event=make_event("template.samples_added", {"sample_document_ids": ["s1"]}), api=core.api(), memory=memory)
    briefing = await build_briefing(design)
    assert "# Guide for this task (template-design)" in briefing and "resolves_to" in briefing
    assert "already above" in (await execute(design, "read_guide", {"name": "template-design"})).text

    document = RunContext(event=make_event("document.submitted", {"document_id": "d1"}), api=core.api(), memory=memory)
    assert "# Guide for this task" not in await build_briefing(document)
    signals = await execute(document, "read_guide", {"name": "signals"})
    assert "identity_lookalike" in signals.text
    assert "No guide" in (await execute(document, "read_guide", {"name": "nope"})).text


def test_old_tool_results_are_trimmed_and_recent_ones_kept():
    messages = [HumanMessage(content="briefing")]
    for i in range(8):
        messages += [AIMessage(content="", tool_calls=[{"name": "get_document", "args": {}, "id": f"c{i}"}]),
                     ToolMessage(content=f"result {i} " + "x" * 3000, tool_call_id=f"c{i}", name="get_document")]
    _trim_tool_results(messages, keep=6, limit=600)
    results = [m for m in messages if isinstance(m, ToolMessage)]
    assert all(m.content.endswith(TRIMMED) and len(m.content) < 700 for m in results[:2])
    assert all(len(m.content) > 3000 for m in results[2:])
    before = results[0].content
    _trim_tool_results(messages, keep=6, limit=600)  # trimming twice changes nothing
    assert results[0].content == before
