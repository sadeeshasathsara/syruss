"use client";

import { useQuery } from "@tanstack/react-query";
import { ChevronRight } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { Markdown, plainText } from "@/components/ui/Markdown";
import { RuleCard, suggestedRuleId } from "@/components/rules/RuleCard";
import { Pill } from "@/components/ui/Pill";
import { api } from "@/lib/api";
import { cn, docName, humanize, percent, STATUS_LABEL, STATUS_TONE } from "@/lib/format";
import type { DocumentStatus } from "@/lib/types";
import { useWorkspace } from "@/lib/workspace";
import { useAgentChat } from "../ChatProvider";
import { StepDetail } from "../StepDetail";
import type { Run, RunStep } from "../timeline";

const TRIGGER: Record<string, string> = {
  "document.submitted": "Reading a document",
  "template.samples_added": "Studying the samples",
  "human.message": "Looking into your message",
  "human.answered": "Continuing with your answer",
  "review.completed": "Learning from a review",
  "proposal.decided": "Applying your decision",
  "schedule.reflect": "Looking back over recent work",
  "rules.ideas": "Thinking of rules for this template",
};

function toolLabel(step: RunStep): string {
  let args: Record<string, unknown> = {};
  try {
    args = step.args ? JSON.parse(step.args) : {};
  } catch {
    /* args were truncated for telemetry */
  }
  const q = (k: string) => (typeof args[k] === "string" && args[k] ? ` “${String(args[k]).slice(0, 40)}”` : "");
  switch (step.tool) {
    case "view_document":
      return Array.isArray(args.region) && args.region.length ? "Zoomed into part of the page" : "Looked at the page";
    case "search_entities":
      return `Checked known entities for${q("query")}`;
    case "search_past_records":
      return `Searched past records for${q("text")}`;
    case "query_records":
      return `Queried saved records${typeof args.object_type === "string" ? ` of ${humanize(args.object_type).toLowerCase()}` : ""}`;
    case "review_history":
      return "Read recent review decisions";
    case "validate_record":
      return "Checked the record against the template";
    case "submit_record":
      return "Saved the record";
    case "ask_human":
    case "flag":
      return "Asked you a question";
    case "open_conversation":
      return `Started a conversation${q("title")}`;
    case "resolve_conversation":
      return "Resolved a conversation";
    case "propose_template_change":
      return "Proposed a template change";
    case "create_entity":
      return `Registered${q("name")}`;
    case "add_entity_alias":
      return `Learned another spelling${q("alias")}`;
    case "remember":
      return "Made a note for later";
    case "recall":
      return `Recalled notes about${q("query")}`;
    case "calculate":
      return `Calculated ${String(args.expression ?? "")}`.trim();
    case "reply_to_user":
      return "Replied";
    case "get_workspace":
      return "Read the workspace";
    case "list_screens":
      return "Looked at the analytics screens";
    case "screen_catalogue":
      return "Looked up what an analytics screen can show";
    case "preview_screen":
      return "Tried an analytics screen";
    case "run_screen":
      return `Read the screen${typeof args.slug === "string" ? ` “${humanize(args.slug).toLowerCase()}”` : ""}`;
    case "save_screen":
      return `Saved the screen${q("name")}`;
    case "update_screen":
      return "Changed a screen";
    case "remove_screen":
      return "Removed a screen";
    case "list_rules":
      return "Looked at the rules";
    case "check_rule":
      return "Tried a rule on the saved records";
    case "suggest_rule":
      return `Suggested the rule${q("name")}`;
    case "list_documents":
      return "Listed documents";
    case "get_document":
      return "Read a saved record";
    default:
      return humanize(step.tool ?? "step");
  }
}

/**
 * Open or closed by default, with the person's own choice winning until something newer arrives:
 * then everything falls back to the default (the newest thing open, the rest folded).
 */
function useDisclosure(defaultOpen: boolean, resetKey: string | number | undefined) {
  const [choice, setChoice] = useState<{ key: string | number | undefined; open: boolean } | null>(null);
  const open = choice && choice.key === resetKey ? choice.open : defaultOpen;
  return [open, () => setChoice({ key: resetKey, open: !open })] as const;
}

function Chevron({ open }: { open: boolean }) {
  return (
    <ChevronRight
      aria-hidden
      className={cn("size-4 flex-none text-muted transition-transform duration-200 ease-out", open && "rotate-90")}
    />
  );
}

/** One action of the agent's, folded to its label; open, it shows what the action found. */
/** A step where Syruss actually looked at a page image (the costly kind; everything else reads the database). */
const readsImage = (s: RunStep) => s.tool === "view_document" && !s.result?.startsWith("REFUSED") && !s.result?.startsWith("ERROR");

function StepRow({ step, defaultOpen, resetKey }: { step: RunStep; defaultOpen: boolean; resetKey: number }) {
  const [open, toggle] = useDisclosure(defaultOpen, resetKey);
  const refused = step.result?.startsWith("REFUSED");
  return (
    <li className="animate-rise">
      <button
        type="button"
        onClick={toggle}
        aria-expanded={open}
        className="-mx-1.5 flex w-[calc(100%+12px)] items-center gap-1.5 rounded-control px-1.5 py-1 text-left text-body text-muted transition-colors hover:bg-raised hover:text-ink"
      >
        <Chevron open={open} />
        <span className={cn("min-w-0 flex-1 truncate", open && "text-ink")}>{toolLabel(step)}</span>
        {refused && <Pill tone="negative">Refused</Pill>}
        {readsImage(step) && <Pill tone="warning">Page image</Pill>}
        {step.result === undefined && <span className="flex-none text-meta text-accent">Working</span>}
      </button>
      <div className="collapsible" data-open={open}>
        <div>
          <div className="mb-1 ml-[22px] mt-1 border-l-2 border-rule pl-3"><StepDetail step={step} /></div>
        </div>
      </div>
    </li>
  );
}

/**
 * Answering a chat message: the reply is the message itself. While Syruss works there is a quiet
 * "thinking" line; afterwards, only the lookups it made (if any), folded, with no summary.
 */
export function ChatRunLine({ run }: { run: Run }) {
  const tools = run.steps.filter((s) => s.kind === "tool" && s.tool !== "reply_to_user");
  // Rules it suggested stay in view, with their Add button, rather than folded with the lookups.
  const rules = [...new Set(tools.filter((s) => s.tool === "suggest_rule").map((s) => suggestedRuleId(s.result)).filter((id): id is string => !!id))];
  if (!rules.length) return <ChatRunSteps run={run} tools={tools} />;
  return (
    <div className="flex flex-col gap-2">
      <ChatRunSteps run={run} tools={tools} />
      {rules.map((id) => <RuleCard key={id} ruleId={id} />)}
    </div>
  );
}

function ChatRunSteps({ run, tools }: { run: Run; tools: RunStep[] }) {
  const [open, setOpen] = useState(false);
  if (run.finished && tools.length === 0) return null;
  if (!run.finished) {
    // While it works, each action shows as it happens: the newest open, the one before folds away.
    return (
      <div className="flex animate-fade flex-col gap-1">
        {tools.length > 0 && (
          <ol className="flex flex-col gap-1 rounded-card bg-card [box-shadow:var(--shadow-card)] px-3 py-2">
            {tools.map((s, i) => <StepRow key={i} step={s} defaultOpen={i === tools.length - 1} resetKey={tools.length} />)}
          </ol>
        )}
        <p className="flex items-center gap-2 text-meta text-muted" aria-live="polite">
          <span aria-hidden className="size-1.5 animate-pulse rounded-full bg-accent" />
          Syruss is {tools.length ? "working" : "thinking"}…
        </p>
      </div>
    );
  }
  return (
    <div className="animate-fade">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex items-center gap-1 text-meta text-muted transition-colors hover:text-ink"
      >
        <Chevron open={open} />
        {tools.length === 1 ? toolLabel(tools[0]) : `Used ${tools.length} lookups`}
        {tools.filter(readsImage).length > 0 && (
          <span className="ml-1 text-[var(--pill-warning-ink)]">
            · read {tools.filter(readsImage).length} page image{tools.filter(readsImage).length === 1 ? "" : "s"}
          </span>
        )}
      </button>
      <div className="collapsible" data-open={open}>
        <div>
          <ol className="mt-1 flex flex-col gap-1 rounded-card bg-card [box-shadow:var(--shadow-card)] px-3 py-2">
            {tools.map((s, i) => <StepRow key={i} step={s} defaultOpen={false} resetKey={tools.length} />)}
          </ol>
        </div>
      </div>
    </div>
  );
}

export function RunCard({ run }: { run: Run }) {
  const { href, workspace } = useWorkspace();
  const { latestRunId } = useAgentChat();
  // Name the document the run is about, from the cached list (no extra request).
  const documents = useQuery({ queryKey: ["documents", workspace.id], queryFn: () => api.documents.list(workspace.id, undefined, 500), staleTime: 60_000 });
  // The newest run shows its work; older runs fold away when a new one starts.
  const [open, toggle] = useDisclosure(run.id === latestRunId, latestRunId);
  const running = !run.finished;
  // What it's doing now: its last words if it narrates, otherwise its last action.
  const lastStep = run.steps.at(-1);
  const current = lastStep ? (lastStep.kind === "thought" ? lastStep.text : toolLabel(lastStep)) : undefined;
  // The newest action is the one shown open; each new action folds the one before.
  const lastTool = run.steps.findLastIndex((s) => s.kind === "tool");
  // A run is about a document when it was submitted, or when its channel is a document
  // (its start event may have dropped out of the live replay).
  const isDocument = !!run.channel && (run.trigger === "document.submitted" || !!documents.data?.some((d) => d.id === run.channel));
  const outcome = run.outcome?.status as DocumentStatus | undefined;
  const found = isDocument ? documents.data?.find((d) => d.id === run.channel) : undefined;
  const documentName = found ? docName(found) : undefined;

  return (
    <div className="rounded-card bg-card [box-shadow:var(--shadow-card)]">
      <button
        type="button"
        onClick={toggle}
        aria-expanded={open}
        className="flex w-full items-start gap-2 rounded-card px-3 py-2.5 text-left transition-colors hover:bg-raised"
      >
        <span className="mt-0.5">
          <Chevron open={open} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span className="min-w-0 text-body font-medium text-ink">
              {documentName ? <>Reading <span className="break-all">{documentName}</span></> : (TRIGGER[run.trigger] ?? "Working")}
            </span>
            {running && (
              <span className="flex items-center gap-1.5 text-meta text-accent">
                <span aria-hidden className="size-1.5 animate-pulse rounded-full bg-accent" />
                Working, step {run.steps.length || 1}
              </span>
            )}
            {outcome && STATUS_LABEL[outcome] && <Pill tone={STATUS_TONE[outcome]}>{STATUS_LABEL[outcome]}</Pill>}
            {run.outcome?.confidence !== undefined && <span className="num text-meta text-muted">{percent(run.outcome.confidence)} sure</span>}
          </span>
          <span className="mt-0.5 line-clamp-2 text-body text-muted">
            {plainText(run.outcome?.reason || (running ? current : run.finished?.summary) || "") || (running ? "Starting…" : "Finished.")}
          </span>
        </span>
      </button>
      <div className="collapsible" data-open={open}>
        <div>
          <ol className="flex flex-col gap-1 border-t border-rule px-3 py-2">
            {run.steps.length === 0 && <li className="py-1 text-body text-muted">No steps yet.</li>}
            {run.steps.map((s, i) =>
              s.kind === "thought" ? (
                <li key={i} className="animate-rise py-1">
                  <Markdown>{s.text ?? ""}</Markdown>
                </li>
              ) : (
                <StepRow key={i} step={s} defaultOpen={i === lastTool} resetKey={lastTool} />
              ),
            )}
            {isDocument && (
              <li className="pt-1">
                <Link href={href(`documents/${run.channel}`)} className="text-body font-medium text-ink underline-offset-2 hover:underline">
                  Open the document
                </Link>
              </li>
            )}
          </ol>
        </div>
      </div>
    </div>
  );
}
