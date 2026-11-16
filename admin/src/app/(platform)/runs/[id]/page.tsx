"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight } from "lucide-react";
import Link from "next/link";
import { use, useCallback, useRef, useState } from "react";
import { Fact, LiveDot, ReasonAction } from "@/components/ops/Bits";
import { DocumentViewer } from "@/components/ops/DocumentViewer";
import { RunSteps } from "@/components/ops/RunSteps";
import { useStaff } from "@/components/shell/AdminFrame";
import { Bar, Empty, ErrorNote, PageHeader, Panel } from "@/components/ui/Blocks";
import { Pill } from "@/components/ui/Pill";
import { api } from "@/lib/api";
import { cn, dateTime } from "@/lib/format";
import { useLive } from "@/lib/live";
import { duration, short, status, tokens, trigger, usd, where } from "@/lib/runs";
import type { LiveFact, RunDetail, RunLink } from "@/lib/types";

const fmt = (v: unknown) => (typeof v === "string" ? v : JSON.stringify(v, null, 2));

export default function RunPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const me = useStaff();
  const queryClient = useQueryClient();
  const run = useQuery({ queryKey: ["run", id], queryFn: () => api.runs.get(id) });
  const d = run.data;
  const running = d?.run.status === "RUNNING";
  // While it runs: what the agent is thinking and doing in the step under way
  const [now, setNow] = useState<LiveFact[]>([]);
  const refetching = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const onEvent = useCallback((e: LiveFact) => {
    if (e.kind === "run.step" || e.kind === "run.finished") {
      setNow([]);
      clearTimeout(refetching.current);
      refetching.current = setTimeout(() => queryClient.invalidateQueries({ queryKey: ["run", id] }), 300);
    } else if (e.content && ["agent_thought", "tool_called", "tool_result"].includes(e.kind)) {
      setNow((items) => [...items, e].slice(-20));
    }
  }, [id, queryClient]);
  const live = useLive({ scope: "run", id }, onEvent, !running);

  if (run.isError) return <ErrorNote message={(run.error as Error).message} onRetry={() => run.refetch()} />;
  if (!d) return <div className="flex flex-col gap-4"><Bar className="h-8 w-72" /><Bar className="h-24 w-full rounded-card" /><Bar className="h-64 w-full rounded-card" /></div>;
  const r = d.run;
  const wait = r.queued_at ? new Date(r.started_at).getTime() - new Date(r.queued_at).getTime() : null;

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        back={{ href: "/runs", label: "‹ Agent runs" }}
        title={<span className="flex flex-wrap items-center gap-2">{trigger(r.trigger)} <Pill tone={status(r.status).tone}>{status(r.status).label}</Pill></span>}
        subtitle={`${where(d, r.tenant_id, r.template_id)} · started ${dateTime(r.started_at)}${r.attempt > 1 ? ` · try ${r.attempt}` : ""}${d.position?.number ? ` · run ${d.position.number} of ${d.position.of} for this document` : ""}`}
        actions={running ? (
          <>
            <LiveDot state={live.state} missed={live.missed} />
            {me.can("operations.manage") && <ReasonAction label="Stop this run" busyLabel="Stopping…" confirmLabel="Stop it" variant="danger"
              onRun={(reason) => api.runs.stop(r.id, reason).then(() => run.refetch())} />}
          </>
        ) : undefined}
      />
      {d.document && <Neighbours previous={d.previous} next={d.next} documentId={d.document.id} />}

      <Panel>
        <dl className="grid grid-cols-2 gap-x-4 gap-y-3 @3xl:grid-cols-4 @5xl:grid-cols-6">
          <Fact label="Asked by">{r.origin === "user" ? `A ${r.actor_type === "api_key" ? "API key" : "person"}, from ${r.source}` : `The platform (${r.source})`}</Fact>
          <Fact label="Model">{r.model}</Fact>
          <Fact label="Waited to start">{duration(wait)}</Fact>
          <Fact label="Took">{running ? "Still running" : duration(r.duration_ms)}</Fact>
          <Fact label="Steps / tool calls">{r.steps} / {r.tool_calls}{r.tool_errors ? <span className="text-[var(--pill-warning-ink)]"> ({r.tool_errors} refused)</span> : null}</Fact>
          <Fact label="Tokens">{tokens(r.input_tokens + r.output_tokens)} <span className="text-muted">· {tokens(r.input_tokens)} in, {tokens(r.output_tokens)} out</span></Fact>
          <Fact label="Model calls / cost">{r.model_calls} <span className="text-muted">· {usd(r.cost_usd)}</span></Fact>
          {r.document_id && <Fact label="Document"><Link href={`/documents/${r.document_id}`} className="text-ink underline-offset-2 hover:underline">{r.document_name ?? `Document ${short(r.document_id)}`}</Link></Fact>}
          {r.conversation_id && <Fact label="Conversation"><span className="num">{short(r.conversation_id)}</span></Fact>}
          {d.event && <Fact label="Event"><span className="num">{d.event.type} · {short(d.event.id)}</span></Fact>}
        </dl>
        {d.error && <p className="mt-3 rounded-control bg-[var(--pill-negative-bg)] px-3 py-2 text-body text-[var(--pill-negative-ink)]">{d.error}</p>}
      </Panel>

      {d.tries.length > 1 && (
        <Panel title="Every try of this work">
          <ol className="flex flex-col gap-1.5">
            {d.tries.map((t) => (
              <li key={t.id} className="flex items-center gap-2 text-body">
                <span className="num w-14 text-meta text-muted">Try {t.attempt}</span>
                <Pill tone={status(t.status).tone}>{status(t.status).label}</Pill>
                {t.id === r.id ? <span className="text-muted">this run</span> : <Link href={`/runs/${t.id}`} className="text-ink hover:underline">{dateTime(t.started_at)}</Link>}
                <span className="num text-meta text-muted">{duration(t.duration_ms)} · {usd(t.cost_usd)}</span>
              </li>
            ))}
          </ol>
        </Panel>
      )}

      {d.document && <DocumentViewer id={d.document.id} contentType={d.document.content_type} pages={d.document.page_count}
        name={d.document.name} sizeBytes={d.document.size_bytes} />}

      <Panel title="Step by step" aside="Open a step to see what was sent to the model, what it answered and the tools it ran">
        {d.steps.length === 0 && !now.length ? (
          <Empty title={running ? "No step finished yet" : "No steps recorded"}>{running ? "Each step appears here the moment it is done." : "The run ended before its first step, or its steps are past their keeping time."}</Empty>
        ) : (
          <>
            <RunSteps steps={d.steps} prompt={d.prompt} />
            {running && now.length > 0 && (
              <div className="flex flex-col gap-1.5 border-t border-rule py-3">
                <span className="text-meta font-medium text-[var(--pill-info-ink)]">Now</span>
                {now.map((e, i) => (
                  <p key={i} className="whitespace-pre-wrap text-body text-ink [overflow-wrap:anywhere]">
                    {e.kind === "agent_thought" ? String(e.data?.thought ?? "") : e.kind === "tool_called" ? `→ ${e.data?.tool_name} ${fmt(e.data?.arguments ?? "")}` : `← ${e.data?.tool_name}: ${String(e.data?.result ?? "").slice(0, 400)}`}
                  </p>
                ))}
              </div>
            )}
          </>
        )}
        {d.final_thought && d.final_thought.trim() !== (d.steps.at(-1)?.thought ?? "").trim() && <p className="mt-3 whitespace-pre-wrap rounded-control bg-raised px-3 py-2 text-body text-ink">{d.final_thought}</p>}
      </Panel>

      <Changes detail={d} />
    </div>
  );
}

/** The runs of the same document before and after this one: each run names the one before it, a linked list. */
function Neighbours({ previous, next, documentId }: { previous: RunLink | null; next: RunLink | null; documentId: string }) {
  const link = (r: RunLink | null, dir: "previous" | "next") => r ? (
    <Link href={`/runs/${r.id}`} className={cn("flex min-w-0 items-center gap-2 rounded-control border border-border px-3 py-2 hover:bg-raised", dir === "next" && "justify-end text-right")}>
      {dir === "previous" && <ChevronLeft className="size-4 flex-none text-muted" aria-hidden />}
      <span className="min-w-0">
        <span className="block text-meta text-muted">{dir === "previous" ? "Previous run" : "Next run"} of this document</span>
        <span className="block truncate text-body text-ink">{trigger(r.trigger)} · {status(r.status).label} · {dateTime(r.started_at)}</span>
      </span>
      {dir === "next" && <ChevronRight className="size-4 flex-none text-muted" aria-hidden />}
    </Link>
  ) : <span className={cn("rounded-control border border-dashed border-control px-3 py-2 text-meta text-faint", dir === "next" && "text-right")}>{dir === "previous" ? "The first run of this document" : "No run after this one yet"}</span>;
  return (
    <nav aria-label="Runs of this document" className="grid grid-cols-1 gap-2 @2xl:grid-cols-[1fr_auto_1fr] @2xl:items-center">
      {link(previous, "previous")}
      <Link href={`/documents/${documentId}`} className="text-center text-meta text-ink underline-offset-2 hover:underline">The document&apos;s whole journey</Link>
      {link(next, "next")}
    </nav>
  );
}

const KIND = { log: "Log", change: "Change", message: "Message", conversation: "Thread" } as const;

function Changes({ detail: d }: { detail: RunDetail }) {
  return (
    <Panel title="What it changed" aside={<span className="num">{d.changes.length}</span>}>
      {d.changes.length === 0 ? <p className="text-body text-muted">Nothing recorded: no log lines, changes, messages or threads came from this run.</p> : (
        <ul className="flex flex-col divide-y divide-rule">
          {d.changes.map((c, i) => (
            <li key={i} className="flex items-baseline gap-3 py-2">
              <span className="num w-20 flex-none text-meta text-muted">{new Date(c.at).toLocaleTimeString()}</span>
              <Pill tone={c.level === "error" ? "negative" : c.level === "success" ? "positive" : c.level === "warning" ? "warning" : "neutral"}>{KIND[c.kind]}</Pill>
              <span className="min-w-0 flex-1 text-body text-ink [overflow-wrap:anywhere]">
                {c.text ?? c.what.replace(/_/g, " ").toLowerCase()}
                {c.target && <span className="text-muted"> · {c.target}</span>}
                {c.document_id && <Link href={`/documents/${c.document_id}`} className="text-muted hover:underline"> · document {short(c.document_id)}</Link>}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
