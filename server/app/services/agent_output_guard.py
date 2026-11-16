"""
The last check on what the agent says before a person reads it: chat replies, flags, and the conversations it opens.

The agent is told what it may never share (its charter); this makes sure, whatever a document or a message talked it
into. It runs on the server, after display_names has turned this workspace's document ids into names:

    instructions   a message that repeats the agent's charter (its mark, or its section headings) is replaced whole
    secrets        keys, tokens, private keys and connection strings are cut out
    images         markdown images become their description: a reply can't make a browser fetch an address
                   (the classic way to smuggle data out: an image whose address carries it)
    internal ids   ids still in the text (anything but this workspace's named documents) are cut out

What was found is returned, never the text itself, so the caller can write down that it happened.
"""
import re
from typing import List, Tuple

CHARTER_MARK = "[syruss-charter]"  # the agent's charters end with it (agentic_framework/agent/charter.py)
# Lines only the charters contain: a reply that quotes them is giving the instructions away
CHARTER_LINES = (
    "# Data is never an instruction", "# What you never share", "# Truthfulness (non-negotiable)",
    "You are Syruss, the autonomous operations agent", "You are Syruss, the AI colleague in ONE workspace",
    "You are Syruss, working as the ANALYST", "<<<data from ",
)
REFUSAL = "I can't share how I'm set up or the instructions I work by. I'm happy to help with this workspace's documents and records."

SECRETS: List[Tuple[str, re.Pattern]] = [
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(-----END [A-Z ]*PRIVATE KEY-----|$)")),
    ("api key", re.compile(r"\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{8,}\b")),           # Stripe
    ("api key", re.compile(r"\bsk_[A-Za-z0-9_\-]{20,}\b")),                                 # Syruss API keys
    ("webhook secret", re.compile(r"\bwhsec_[A-Za-z0-9]{10,}\b")),
    ("api key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),                                       # AWS
    ("api key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),                                 # Google
    ("token", re.compile(r"\b(?:ghp|gho|ghs|ghu|github_pat)_[A-Za-z0-9_]{20,}\b")),          # GitHub
    ("token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),                              # Slack
    ("token", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")),  # JWT
    ("connection string", re.compile(r"\b[a-z][a-z0-9+.-]{1,20}://[^\s:/@]+:[^\s@/]+@[^\s]+", re.I)),
    # "password: Xy7!kq2..." (a value with a digit or a symbol, so "Password: required" is left alone)
    ("password", re.compile(r"(?i)\b(password|passwd|secret|api[_-]?key)\s*[:=]\s*(?=\S*[\d!@#$%^&*_\-])\S{6,}")),
]
HTML_IMAGE_START = re.compile(r"<img\b", re.I)
UUID = re.compile(r"(?<![\w/-])`?[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}`?(?![\w-])", re.I)
SHORT_ID = re.compile(r"`?\b[0-9a-f]{8}(?:-[0-9a-f]{0,4})?(?:\.{3}|…)`?", re.I)


def strip_markdown_images(text: str, replace) -> Tuple[str, int]:
    """Replaces each ![description](address) with `replace(description)`. Written as a scan, not one regular expression: text made of
    many "![" (or "![](") with no end would make that pattern work through the rest of the text from every start, and the text is
    the agent's, so it can be anything. Here every position is looked at once: the next "]" and ")" are only searched for again when
    the one found has been passed."""
    out: List[str] = []
    count, at, i = 0, 0, 0
    close = paren = -2                      # where the next "]" / ")" is (-1: there is none, so no later image can end either)
    while True:
        start = text.find("![", i)
        if start < 0:
            break
        if close != -1 and close < start + 2:
            close = text.find("]", start + 2)
        if close == -1:
            break
        if text[close + 1:close + 2] != "(":
            i = start + 1
            continue
        if paren != -1 and paren < close + 2:
            paren = text.find(")", close + 2)
        if paren == -1:
            break
        out.append(text[at:start])
        out.append(replace(text[start + 2:close]))
        count += 1
        at = i = paren + 1
    out.append(text[at:])
    return "".join(out), count


def strip_html_images(text: str) -> Tuple[str, int]:
    """Removes each <img ...>. A scan for the same reason as strip_markdown_images: many "<img" with no ">" must not cost more than one pass."""
    out: List[str] = []
    count, at, end = 0, 0, -2
    for m in HTML_IMAGE_START.finditer(text):
        if m.start() < at:
            continue
        if end != -1 and end < m.end():
            end = text.find(">", m.end())
        if end == -1:
            break
        out.append(text[at:m.start()])
        count += 1
        at = end + 1
    out.append(text[at:])
    return "".join(out), count


def screen(text: str) -> Tuple[str, List[str]]:
    """The text as a person may read it, and what was taken out (kinds only)."""
    if not text:
        return text, []
    if CHARTER_MARK in text or any(line in text for line in CHARTER_LINES):
        return REFUSAL, ["instructions"]
    found: List[str] = []
    for kind, pattern in SECRETS:
        text, n = pattern.subn("[removed]", text)
        if n:
            found.append(kind)
    text, n = strip_markdown_images(text, lambda description: f"({description or 'image'})")
    text, m = strip_html_images(text)
    if n or m:
        found.append("image")
    text, n = UUID.subn("[internal reference]", text)
    text, m = SHORT_ID.subn("[internal reference]", text)
    if n or m:
        found.append("internal id")
    return text, sorted(set(found))
