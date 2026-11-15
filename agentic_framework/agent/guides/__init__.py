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
    "rules": "rules: checks in the rule language that run on every record, writing one from plain words, and when to suggest one",
    # Help on using Syruss itself, for questions people ask in a chat ("how do I", "why can't I").
    "help-getting-started": "help: what Syruss is, setting up an organization and a workspace, where things are",
    "help-documents": "help: uploading, document statuses, review, the agent inbox, the template and knowledge",
    "help-billing": "help: plans, credits, what is owed to Syruss and paying it (card or bank transfer), renewals, billing details",
    "help-team-and-roles": "help: organization and workspace roles, inviting people, why someone can't do something",
    "help-account-security": "help: password, two-step verification, Keep me signed in, trusted browsers, Google sign-in",
    "help-integrations": "help: Connections (receiving documents, publishing approved data, deliveries), API keys and webhooks",
    "help-analytics": "help: analytics screens, suggestions, and who can build them",
}

# The guides on using Syruss itself (listed in the chat charter, not the work charter).
HELP = tuple(name for name in GUIDES if name.startswith("help-"))

# Guides a task always needs, attached to its briefing so the agent doesn't spend a step reading them.
FOR_TASK: Dict[str, str] = {
    "template.samples_added": "template-design",
    "knowledge.source_added": "reference-data",
    "schedule.reflect": "reflection",
    "schedule.insights": "screens",
    "rules.ideas": "rules",
}

# A chat's mode (fixed when it starts, by the page it was started from) brings the guide its every message needs.
FOR_CHAT_MODE: Dict[str, str] = {"rules": "rules", "screens": "screens"}


@lru_cache(maxsize=None)
def read(name: str) -> str:
    if name not in GUIDES:
        raise ValueError(f"No guide '{name}'. Guides: {', '.join(GUIDES)}.")
    return (HERE / f"{name}.md").read_text(encoding="utf-8").strip()


def for_task(event_type: str) -> Optional[str]:
    name = FOR_TASK.get(event_type)
    return read(name) if name else None
