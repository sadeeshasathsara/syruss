"""
Guides: detailed guidance for particular kinds of work, as markdown files next to this module.

The charter keeps only what every run needs. A guide is read when the work calls for it: attached to
the briefing when the task plainly needs it (designing a template, mapping a data source,
reflecting), or read by the agent with the read_guide tool when the need comes up mid-run (its first
flag, a blocking signal). Every model call resends the prompt, so what isn't needed isn't sent.
"""
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional

HERE = Path(__file__).parent

# What each guide is for, as the agent sees it in read_guide's description.
GUIDES: Dict[str, str] = {
    "template-design": "designing or changing the template, and the template format",
    "reference-data": "mapping a data source onto the template",
    "signals": "identity signals (likely misreads) and documents already in the database",
    "asking-people": "flags and conversations: when to use which, and how",
    "reflection": "looking back over recent work and corrections",
    "screens": "analytics screens (the Analytics tab): tables, calculated columns and analytics over the saved records",
}

# Guides a task always needs, attached to its briefing so the agent doesn't spend a step reading them.
FOR_TASK: Dict[str, str] = {
    "template.samples_added": "template-design",
    "knowledge.source_added": "reference-data",
    "schedule.reflect": "reflection",
    "schedule.insights": "screens",
}


@lru_cache(maxsize=None)
def read(name: str) -> str:
    if name not in GUIDES:
        raise ValueError(f"No guide '{name}'. Guides: {', '.join(GUIDES)}.")
    return (HERE / f"{name}.md").read_text(encoding="utf-8").strip()


def for_task(event_type: str) -> Optional[str]:
    name = FOR_TASK.get(event_type)
    return read(name) if name else None
