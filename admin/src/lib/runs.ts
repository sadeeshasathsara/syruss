import type { Tone } from "@/components/ui/Pill";
import type { Names, RunStatus } from "./types";

/** Agent work in words, as the main app says it. */
export const TRIGGER: Record<string, string> = {
  "document.submitted": "Reading a document",
  "template.samples_added": "Studying samples",
  "human.message": "Answering a message",
  "human.answered": "Continuing after an answer",
  "review.completed": "Learning from a review",
  "proposal.decided": "Applying a decision",
  "knowledge.source_added": "Mapping a data source",
  "schedule.reflect": "Reflecting on recent work",
  "schedule.insights": "Looking for insights",
  "rules.ideas": "Suggesting rules",
  "eval.item": "Evaluation",
};

export const trigger = (t: string) => TRIGGER[t] ?? t;

export const STATUS: Record<RunStatus, { label: string; tone: Tone }> = {
  RUNNING: { label: "Running", tone: "info" },
  COMPLETED: { label: "Completed", tone: "positive" },
  BUDGET_EXCEEDED: { label: "Ran out of steps", tone: "warning" },
  CANCELLED: { label: "Stopped", tone: "neutral" },
  FAILED: { label: "Failed", tone: "negative" },
  DEAD_LETTERED: { label: "Gave up", tone: "negative" },
  LOST: { label: "Lost", tone: "negative" },
};

export const status = (s: string) => STATUS[s as RunStatus] ?? { label: s, tone: "neutral" as Tone };

export const DOC_STATUS: Record<string, { label: string; tone: Tone }> = {
  PENDING: { label: "Not sent", tone: "neutral" },
  QUEUED: { label: "Queued", tone: "info" },
  PROCESSING: { label: "Being read", tone: "info" },
  AWAITING_INPUT: { label: "Waiting for an answer", tone: "warning" },
  NEEDS_REVIEW: { label: "In review", tone: "warning" },
  COMPLETED: { label: "Saved", tone: "positive" },
  FAILED: { label: "Failed", tone: "negative" },
};

export const docStatus = (s: string) => DOC_STATUS[s] ?? { label: s, tone: "neutral" as Tone };

export function usd(n: number | null | undefined): string {
  if (n == null) return "—";
  if (n === 0) return "$0";
  return n < 0.01 ? `$${n.toFixed(4)}` : `$${n.toFixed(2)}`;
}

export function duration(ms: number | null | undefined): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const s = ms / 1000;
  if (s < 60) return `${s.toFixed(1)} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ${Math.round(s % 60)} s`;
  const h = Math.floor(m / 60);
  return h < 48 ? `${h} h ${m % 60} min` : `${Math.round(h / 24)} days`;
}

export const tokens = (n: number) => (n >= 10_000 ? `${(n / 1000).toFixed(1)}k` : n.toLocaleString());

/** "Acme / Invoices", from the names a response carried. */
export function where(names: Partial<Names> | undefined, tenantId?: string | null, workspaceId?: string | null): string {
  const org = tenantId ? names?.organizations?.[tenantId]?.name : undefined;
  const ws = workspaceId ? names?.workspaces?.[workspaceId]?.name : undefined;
  return [org ?? (tenantId ? "An organization" : null), ws].filter(Boolean).join(" / ") || "—";
}

/** The moment `hours` ago, for a query's `since` (called when the query runs, never while rendering). */
export const hoursAgo = (hours: number) => new Date(Date.now() - hours * 3600_000).toISOString();

export const short =(id: string | null | undefined) => (id ? id.slice(0, 8) : "—");
