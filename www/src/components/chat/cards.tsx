"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronRight } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState } from "react";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Field";
import { Markdown, plainText } from "@/components/ui/Markdown";
import { MappingSummary } from "@/components/knowledge/MappingSummary";
import { Pill } from "@/components/ui/Pill";
import { api } from "@/lib/api";
import { cn, fieldName, humanize, percent, STATUS_LABEL, STATUS_TONE } from "@/lib/format";
import type { AgentQuestion, DocumentStatus, FlagAnswer, MappingPreview, Proposal, SourceMapping, TemplateSchema } from "@/lib/types";
import { useWorkspace } from "@/lib/workspace";
import { useAgentChat } from "./ChatProvider";
import { StepDetail } from "./StepDetail";
import type { Run, RunStep, TimelineItem } from "./timeline";

const TRIGGER: Record<string, string> = {
  "document.submitted": "Reading a document",
  "template.samples_added": "Studying the samples",
  "human.message": "Looking into your message",
  "human.answered": "Continuing with your answer",
  "review.completed": "Learning from a review",
  "proposal.decided": "Applying your decision",
  "schedule.reflect": "Looking back over recent work",
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
function ChatRunLine({ run }: { run: Run }) {
  const [open, setOpen] = useState(false);
  const tools = run.steps.filter((s) => s.kind === "tool" && s.tool !== "reply_to_user");
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
  const docName = isDocument ? documents.data?.find((d) => d.id === run.channel)?.original_filename : undefined;

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
              {docName ? <>Reading <span className="break-all">{docName}</span></> : (TRIGGER[run.trigger] ?? "Working")}
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

/** The rows the agent read, as a table: numbered, in the order they appear on the page. */
export function DataTableView({ table }: { table: NonNullable<AgentQuestion["data_table"]> }) {
  return (
    <figure className="mt-2 min-w-0">
      {table.title && <figcaption className="mb-1 text-meta font-medium text-muted">{table.title}, {table.rows.length} {table.rows.length === 1 ? "row" : "rows"}</figcaption>}
      <div className="max-h-80 overflow-auto rounded-control border border-border bg-sheet scrollbar-thin">
        <table className="w-full border-collapse text-body">
          <thead className="sticky top-0 bg-raised text-left text-meta text-muted">
            <tr>
              <th className="w-8 px-2 py-1.5 text-right font-medium">#</th>
              {table.columns.map((c, i) => <th key={i} className="px-2 py-1.5 font-medium">{c}</th>)}
            </tr>
          </thead>
          <tbody>
            {table.rows.map((r, i) => (
              <tr key={i} className="border-t border-rule">
                <td className="num px-2 py-1.5 text-right text-meta text-faint">{i + 1}</td>
                {table.columns.map((_, j) => <td key={j} className="px-2 py-1.5 text-ink [overflow-wrap:anywhere]">{r[j] ?? ""}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </figure>
  );
}

export function QuestionCard({ question }: { question: AgentQuestion }) {
  const { workspace } = useWorkspace();
  const queryClient = useQueryClient();
  const [text, setText] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const [otherOn, setOtherOn] = useState(false);
  const kind = question.answer_type ?? (question.options?.length ? "single" : "text");
  const { openConversation } = useAgentChat();
  const answer = useMutation({
    mutationFn: (value: FlagAnswer) => api.questions.answer(workspace.id, question.id, value),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ["questions", workspace.id] });
      queryClient.invalidateQueries({ queryKey: ["workspace"] });
      if (res.conversation_id) openConversation(res.conversation_id);
    },
  });
  const toggle = (option: string) => {
    if (kind === "single") {
      setPicked([option]);
      setOtherOn(false);
    } else {
      setPicked((p) => (p.includes(option) ? p.filter((x) => x !== option) : [...p, option]));
    }
  };
  const other = otherOn ? text.trim() : "";
  const ready = kind === "text" ? !!text.trim() : picked.length > 0 || !!other;
  const submit = () => {
    if (!ready) return;
    answer.mutate(kind === "text" ? { answer: text.trim() } : { choices: picked, other: other || undefined });
  };
  const dismiss = useMutation({
    mutationFn: () => api.questions.dismiss(workspace.id, question.id, "Not relevant"),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["questions", workspace.id] }),
  });
  const answered = answer.data ?? (question.status !== "OPEN" ? question : undefined);

  return (
    <div className={cn("rounded-card px-3 py-3 transition-colors [box-shadow:var(--shadow-card)]", answered ? "bg-card/60" : "bg-card border border-control")}>
      <div className="flex items-center gap-2">
        <Pill tone="info">{kind === "text" ? "Question for you" : "Decision for you"}</Pill>
        {question.priority === "high" && <span className="text-meta text-muted">Work is waiting on this</span>}
      </div>
      <p className="mt-2 text-body font-medium text-ink">{question.question}</p>
      {question.context && <p className="mt-1 text-body text-muted">{question.context}</p>}
      {question.data_table && question.data_table.rows.length > 0 && <DataTableView table={question.data_table} />}
      {answered ? (
        <div className="mt-2 flex flex-col gap-2">
          <p className="text-body text-ink">
            {answered.status === "DISMISSED" ? "Dismissed." : <>Answered: <span className="font-medium">{answered.answer}</span></>}
          </p>
          {answered.status === "ANSWERED" && answered.conversation_id && (
            answered.conversation_status === "resolved" ? (
              <p className="text-meta text-muted">Syruss resolved this.</p>
            ) : (
              <div className="flex flex-wrap items-center gap-2 rounded-control bg-[var(--pill-info-bg)] px-3 py-2 text-body text-[var(--pill-info-ink)]">
                <span className="min-w-0 flex-1">Pending: Syruss is working on your answer in the chat, and closes this when nothing is left.</span>
                <Button size="sm" variant="secondary" onClick={() => openConversation(answered.conversation_id!)}>Open the chat</Button>
              </div>
            )
          )}
        </div>
      ) : (
        <>
        <form
          className="mt-3 flex flex-col gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
        >
          {kind !== "text" && (
            <fieldset className="flex flex-col gap-1">
              <legend className="sr-only">{kind === "single" ? "Pick one" : "Pick any that apply"}</legend>
              {[...question.options, "Other"].map((o) => {
                const isOther = o === "Other";
                const checked = isOther ? otherOn : picked.includes(o);
                return (
                  <label key={o} className="flex cursor-pointer items-center gap-2 rounded-control px-1.5 py-1 text-body text-ink hover:bg-raised">
                    <input
                      type={kind === "single" ? "radio" : "checkbox"}
                      name={`flag-${question.id}`}
                      className="size-4 cursor-pointer accent-[var(--ink)]"
                      checked={checked}
                      onChange={() => {
                        if (!isOther) return toggle(o);
                        if (kind === "single") setPicked([]);
                        setOtherOn(kind === "single" ? true : !otherOn);
                      }}
                    />
                    {isOther ? "Other" : o}
                  </label>
                );
              })}
            </fieldset>
          )}
          {(kind === "text" || otherOn) && (
            <Input
              value={text}
              onChange={(e) => setText(e.target.value)}
              placeholder={kind === "text" ? "Your answer" : "Write your own answer"}
              aria-label={kind === "text" ? "Your answer" : "Other answer"}
              autoFocus={otherOn}
            />
          )}
          <div>
            <Button type="submit" variant="primary" size="sm" disabled={!ready || answer.isPending}>
              {answer.isPending ? "Sending…" : "Answer"}
            </Button>
          </div>
        </form>
          <button type="button" onClick={() => dismiss.mutate()} className="mt-2 text-meta text-muted hover:text-ink">
            Not relevant
          </button>
          {(answer.error || dismiss.error) && <p className="mt-1 text-meta text-negative">{(answer.error ?? dismiss.error)?.message}</p>}
        </>
      )}
    </div>
  );
}

/** Field-level difference between the active template and a proposed one. */
export function schemaDiff(current: TemplateSchema | undefined, next: TemplateSchema | undefined) {
  const index = (s?: TemplateSchema) =>
    new Map((s?.objects ?? []).map((o) => [o.type, new Set(o.fields.map(fieldName))]));
  const a = index(current);
  const b = index(next);
  const changes: { object: string; added: string[]; removed: string[]; isNew: boolean; isGone: boolean }[] = [];
  for (const [type, fields] of b) {
    const before = a.get(type);
    const added = [...fields].filter((f) => !before?.has(f));
    const removed = before ? [...before].filter((f) => !fields.has(f)) : [];
    if (!before || added.length || removed.length) changes.push({ object: type, added, removed, isNew: !before, isGone: false });
  }
  for (const [type, fields] of a) if (!b.has(type)) changes.push({ object: type, added: [], removed: [...fields], isNew: false, isGone: true });
  const rules = (next?.validation_rules?.length ?? 0) - (current?.validation_rules?.length ?? 0);
  return { changes, rules };
}

export function ProposalCard({ proposal }: { proposal: Proposal }) {
  const { workspace, href } = useWorkspace();
  const pathname = usePathname();
  const queryClient = useQueryClient();
  const template = useQuery({ queryKey: ["template", workspace.id], queryFn: () => api.template.get(workspace.id) });
  const review = useMutation({
    mutationFn: (action: "APPROVE" | "REJECT") => api.proposals.review(workspace.id, proposal.id, action),
    onSuccess: () => {
      ["proposals", "template", "workspace", "workspaces", "sources", "entities"].forEach((k) => queryClient.invalidateQueries({ queryKey: [k] }));
    },
  });
  const decided = review.data ?? (proposal.status !== "PENDING" ? proposal : undefined);
  const isTemplate = proposal.proposal_type === "TEMPLATE_CHANGE";
  const isMapping = proposal.proposal_type === "SOURCE_MAPPING";
  const first = isTemplate && !template.data?.active_version_id;
  const diff = isTemplate ? schemaDiff(first ? undefined : template.data?.schema_definition, proposal.proposed_data.schema_definition) : null;

  return (
    <div className={cn("rounded-card px-3 py-3 transition-colors [box-shadow:var(--shadow-card)]", decided ? "bg-card/60" : "bg-card border border-control")}>
      <Pill tone="accent">
        {first
          ? "Proposed template"
          : isTemplate
            ? "Template change"
            : isMapping
              ? `Mapping for ${String(proposal.proposed_data.source_name ?? "a data source")}`
              : humanize(proposal.proposal_type)}
      </Pill>
      <p className="mt-2 text-body text-ink">{proposal.observation}</p>
      {isMapping && !decided && (
        <div className="mt-2">
          <MappingSummary
            mapping={proposal.proposed_data.mapping as SourceMapping}
            preview={proposal.proposed_data.preview as MappingPreview[] | undefined}
          />
        </div>
      )}
      {diff && diff.changes.length > 0 && (
        <ul className="mt-2 flex flex-col gap-1 text-body">
          {diff.changes.map((c) => (
            <li key={c.object} className="text-muted">
              <span className="font-medium text-ink">{c.object}</span>
              {c.isNew ? " (new)" : c.isGone ? " (removed)" : ""}
              {c.added.length > 0 && <>: adds {c.added.join(", ")}</>}
              {c.removed.length > 0 && !c.isGone && <>; removes {c.removed.join(", ")}</>}
            </li>
          ))}
          {diff.rules !== 0 && <li className="text-muted">{diff.rules > 0 ? `${diff.rules} new rule(s)` : `${-diff.rules} rule(s) removed`}</li>}
        </ul>
      )}
      {decided ? (
        <p className="mt-2 text-body text-ink">
          {decided.status === "APPROVED"
            ? isMapping ? "Approved and imported." : "Approved and published."
            : decided.reviewer_notes || "Rejected."}
        </p>
      ) : first ? (
        pathname === href("setup") ? (
          <p className="mt-2 text-body text-muted">Edit it on the left, then publish.</p>
        ) : (
          <div className="mt-3">
            <Button asChild size="sm" variant="primary">
              <Link href={href("setup")}>Review the template</Link>
            </Button>
          </div>
        )
      ) : (
        <div className="mt-3 flex flex-wrap gap-2">
          <Button size="sm" variant="primary" disabled={review.isPending} onClick={() => review.mutate("APPROVE")}>
            {isMapping ? "Approve and import" : "Approve and publish"}
          </Button>
          <Button size="sm" variant="secondary" disabled={review.isPending} onClick={() => review.mutate("REJECT")}>
            Reject
          </Button>
          {review.error && <p className="w-full text-meta text-negative">{review.error.message}</p>}
        </div>
      )}
    </div>
  );
}

function NoteLine({ text, href }: { text: string; href?: string }) {
  return (
    <p className="text-right text-meta text-muted">
      {href ? (
        <Link href={href} className="underline-offset-2 hover:text-ink hover:underline">
          {text}
        </Link>
      ) : (
        text
      )}
    </p>
  );
}

export function TimelineCard({ data }: { data: TimelineItem }) {
  switch (data.kind) {
    case "run":
      return data.run.trigger === "human.message" ? <ChatRunLine run={data.run} /> : <RunCard run={data.run} />;
    case "question":
      return <QuestionCard question={data.question} />;
    case "proposal":
      return <ProposalCard proposal={data.proposal} />;
    case "note":
      return <NoteLine text={data.text} href={data.href} />;
    default:
      return null;
  }
}
