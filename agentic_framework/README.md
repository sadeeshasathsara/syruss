# Workspace agent service

An autonomous agent that works inside one **template workspace** at a time. It has a job
(the charter), memory, and tools — not a procedure. It decides its own steps: look at pages,
zoom into handwriting, recognize people and things from what the workspace already knows,
commit records, send documents to review, ask humans, propose template changes, and learn
from answers and corrections.

It is a separate service. It never touches the core server's database or file storage:

| Talks to | How | For |
|---|---|---|
| Core server | `agent.events` Kafka topic (in) | being woken up |
| Core server | HTTP Agent Tool API with the event's scoped token | every read and action |
| Frontend | `agent.telemetry` Kafka topic (out) | live thoughts and tool calls |
| Its own Postgres database (`agent_memory`) | SQLAlchemy | memory and idempotency |

## Layout

```
agentic_framework/
  __main__.py      python -m agentic_framework
  worker.py        Kafka consumer: per-workspace ordering, retries, dead-letter, outcome guarantees
  events.py        event envelope
  core_api.py      HTTP client for the Agent Tool API
  memory.py        observations, lessons, parked work; processed-event ledger
  telemetry.py     live events for the UI
  llm.py           model factory (gemini | anthropic | openai)
  agent/
    charter.py     the job description (system prompt)
    briefing.py    what happened + workspace state + memory, given at wake-up
    tools.py       the agent's capabilities
    loop.py        reason -> act -> observe until done or out of budget
  tests/
```

## Events that wake the agent

| Event | Sent when |
|---|---|
| `template.samples_added` | an admin uploads samples to design or refine the template |
| `knowledge.source_added` | an admin adds reference data (a file or an API), changes it, or asks for a new mapping |
| `document.submitted` | a document is handed to the agent |
| `human.message` | someone chats in the workspace (there is no separate help agent) |
| `human.answered` | someone answers or dismisses a question the agent asked |
| `review.completed` | a reviewer approved, corrected or rejected a record |
| `proposal.decided` | an admin approved or rejected a template change or entity merge |
| `schedule.reflect` | periodic look-back (reserved) |

Events of one workspace are processed in order (Kafka key = template id); different
workspaces run in parallel. Offsets are committed after an event is handled; handled event ids
are remembered, so re-deliveries are skipped.

## Authority

Enforced by the core server, not by the prompt:

- **Free:** read the workspace, view pages, validate drafts, ask humans, reply in chat, write memory.
- **Free, audited:** commit records that pass the active template, send to review, create entities,
  add aliases, update entity attributes.
- **Needs approval:** template changes and entity merges (proposals).
- **Impossible:** anything outside the workspace that woke it; deleting data.

The runtime also guarantees outcomes: a submitted document never stays `PROCESSING`, and a
chat message always gets a reply.

## Run

```bash
cp agentic_framework/.env.example agentic_framework/.env   # set the model API key
python -m agentic_framework                                 # from the repository root
```

## Test

```bash
cd agentic_framework && python -m pytest
```

Tests use a scripted model and a fake core server; only Postgres is needed (for memory).
