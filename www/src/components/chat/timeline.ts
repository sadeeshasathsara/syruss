import type { ThreadMessageLike } from "@assistant-ui/react";
import type { ActionNote } from "@/components/shell/ShellContext";
import type { AgentQuestion, ChatMessage, LiveEvent, Proposal } from "@/lib/types";

/** What the agent did in one wake-up, built from its live telemetry. */
export interface RunStep {
  step: number;
  kind: "thought" | "tool";
  text?: string;
  tool?: string;
  args?: string;
  result?: string;
  at: string;
}

/** Where a run came from: who asked, through what, and in which chat (see the server's event origin). */
export interface RunOrigin {
  type?: string;
  id?: string;
  name?: string | null;
  origin?: "user" | "system";
  source?: string;
  conversation_id?: string | null;
}

export interface Run {
  id: string;
  trigger: string;
  origin?: RunOrigin;
  channel?: string;
  startedAt: string;
  steps: RunStep[];
  finished?: { status: string; summary: string; at: string };
  /** The document's outcome, when the run was about a document. */
  outcome?: { status: string; confidence?: number; reason?: string };
}

export type TimelineItem =
  | { kind: "user"; id: string; text: string; at: string; pending?: boolean }
  | { kind: "agent"; id: string; text: string; at: string }
  | { kind: "note"; id: string; text: string; href?: string; at: string }
  | { kind: "run"; id: string; run: Run; at: string }
  | { kind: "question"; id: string; question: AgentQuestion; at: string }
  | { kind: "proposal"; id: string; proposal: Proposal; at: string };

const str = (v: unknown) => (typeof v === "string" ? v : v === undefined || v === null ? "" : JSON.stringify(v));

export function buildRuns(events: LiveEvent[]): Run[] {
  const runs = new Map<string, Run>();
  const latestRunForChannel = new Map<string, Run>();
  for (const e of events) {
    const at = e.timestamp ?? new Date().toISOString();
    if (e.run_id) {
      let run = runs.get(e.run_id);
      if (!run) {
        run = { id: e.run_id, trigger: str(e.trigger) || "work", channel: e.channel_id, startedAt: at, steps: [] };
        runs.set(e.run_id, run);
      }
      if (e.channel_id) latestRunForChannel.set(e.channel_id, run);
      switch (e.event_type) {
        case "processing_started":
          run.trigger = str(e.trigger) || run.trigger;
          if (e.origin && typeof e.origin === "object") run.origin = e.origin as RunOrigin;
          break;
        case "agent_thought":
          run.steps.push({ step: Number(e.step) || run.steps.length, kind: "thought", text: str(e.thought), at });
          break;
        case "tool_called":
          run.steps.push({ step: Number(e.step) || run.steps.length, kind: "tool", tool: str(e.tool_name), args: str(e.arguments), at });
          break;
        case "tool_result": {
          const call = [...run.steps].reverse().find((s) => s.kind === "tool" && s.tool === str(e.tool_name) && s.result === undefined);
          if (call) call.result = str(e.result);
          break;
        }
        case "agent_run_finished":
          run.finished = { status: str(e.status), summary: str(e.summary), at };
          break;
      }
    } else if (e.event_type === "agent_completed" && e.channel_id) {
      const run = latestRunForChannel.get(e.channel_id);
      if (run) run.outcome = { status: str(e.status), confidence: Number(e.confidence) || undefined, reason: str(e.reason) || undefined };
    }
  }
  return [...runs.values()];
}

export function buildTimeline(input: {
  thread: ChatMessage[];
  pending: { id: string; text: string; at: string }[];
  events: LiveEvent[];
  notes: ActionNote[];
  questions: AgentQuestion[];
  proposals: Proposal[];
}): TimelineItem[] {
  const items: TimelineItem[] = [];
  const messageIds = new Set(input.thread.map((m) => m.id));

  for (const m of input.thread) {
    if (m.role === "user") items.push({ kind: "user", id: m.id, text: m.content, at: m.created_at });
    else if (m.role === "assistant") items.push({ kind: "agent", id: m.id, text: m.content, at: m.created_at });
    else items.push({ kind: "note", id: m.id, text: m.content, at: m.created_at });
  }
  const sentTexts = new Set(input.thread.filter((m) => m.role === "user").map((m) => m.content.trim()));
  for (const p of input.pending) {
    if (!sentTexts.has(p.text.trim())) items.push({ kind: "user", id: p.id, text: p.text, at: p.at, pending: true });
  }
  for (const e of input.events) {
    if (e.event_type === "agent_message" && typeof e.message_id === "string" && !messageIds.has(e.message_id)) {
      items.push({ kind: "agent", id: e.message_id, text: str(e.content), at: e.timestamp ?? new Date().toISOString() });
      messageIds.add(e.message_id);
    }
  }
  for (const run of buildRuns(input.events)) items.push({ kind: "run", id: run.id, run, at: run.startedAt });
  for (const n of input.notes) items.push({ kind: "note", id: n.id, text: n.text, href: n.href, at: n.at });

  // Questions and proposals: the current server state wins over the live copy.
  const questions = new Map<string, AgentQuestion>();
  for (const e of input.events) if (e.event_type === "agent_question" && typeof e.id === "string") questions.set(e.id, e as unknown as AgentQuestion);
  for (const q of input.questions) questions.set(q.id, q);
  for (const q of questions.values()) items.push({ kind: "question", id: q.id, question: q, at: q.created_at });

  const proposals = new Map<string, Proposal>();
  for (const e of input.events) if (e.event_type === "agent_proposal" && typeof e.id === "string") proposals.set(e.id, e as unknown as Proposal);
  for (const p of input.proposals) proposals.set(p.id, p);
  for (const p of proposals.values()) items.push({ kind: "proposal", id: p.id, proposal: p, at: p.created_at });

  return items.sort((a, b) => new Date(a.at).getTime() - new Date(b.at).getTime());
}

/** The timeline as assistant-ui messages: cards are data parts, words are text parts. */
export function toThreadMessages(items: TimelineItem[]): ThreadMessageLike[] {
  return items.map((item) => {
    const createdAt = new Date(item.at);
    switch (item.kind) {
      case "user":
        return { id: item.id, role: "user", content: item.text, createdAt };
      case "agent":
        return { id: item.id, role: "assistant", content: [{ type: "text", text: item.text }], createdAt, status: { type: "complete", reason: "stop" } };
      default:
        return {
          id: `${item.kind}_${item.id}`,
          role: "assistant",
          content: [{ type: `data-${item.kind}`, data: item }],
          createdAt,
          status: item.kind === "run" && !item.run.finished ? { type: "running" } : { type: "complete", reason: "stop" },
        };
    }
  });
}
