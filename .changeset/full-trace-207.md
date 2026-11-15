---
agent: minor
server: minor
admin: minor
www: minor
---

- **The admin app shows a document and its agent runs in full.**
  - **The document itself** on the document and run pages: page by page, with zoom and "open full size".
  - **Every journey step opens** to show everything recorded about it: the event that woke the agent, its tries and errors, each log line, review, question, delivery attempt (with status code, request and response) and change. Opening one closes the one open before.
  - **A run is one journey step** that opens to its agent steps.
  - **Each agent step shows:**
    - what was sent to the model: on step 1, the agent's instructions and the briefing; after that, the tool results that came back, plus "Show the full prompt";
    - what the model answered, and its thinking when the provider returns it;
    - each tool it ran, with what it sent and what came back;
    - its tokens.
  - **Tokens add up:** per step, per run, and per document (in, out and total).
  - **Each run links to the run of the same document before and after it.** Runs of one document form a linked list ("run 2 of 3"), with Previous and Next on the run page.
- **The agent records** each step's tokens, the model's thinking, and what it added before a call (reminders, requested page images). It sends the run's prompt once. The system prompt is stored once per version; the briefing is cleared with old steps after `RUN_STEPS_RETENTION_DAYS`.
- **Staff see documents and runs without opening anything.** The support-access grant (the "Open content" step, the customer's switch, its banner and email) is gone. The customer's Settings → Organization page "Support and privacy" says plainly what the Syruss team sees and why.
- Migration `0038`. Existing runs are linked per document in the order they started.
