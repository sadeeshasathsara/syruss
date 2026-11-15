"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronRight } from "lucide-react";
import Link from "next/link";
import { use, useCallback, useRef, useState } from "react";
import { Fact, LiveDot, ReasonAction } from "@/components/ops/Bits";
import { DocumentViewer } from "@/components/ops/DocumentViewer";
import { RunSteps } from "@/components/ops/RunSteps";
import { useStaff } from "@/components/shell/AdminFrame";
import { Bar, ErrorNote, PageHeader, Panel } from "@/components/ui/Blocks";
import { Pill } from "@/components/ui/Pill";
import { api } from "@/lib/api";
import { cn, dateTime } from "@/lib/format";
import { useLive } from "@/lib/live";
import { docStatus, duration, short, status, tokens, trigger, usd, where } from "@/lib/runs";
import type { ProcessDetail, TimelineItem } from "@/lib/types";

const STAGES = [
  { key: "received", label: "Received" },
  { key: "queued", label: "Queued" },
  { key: "run", label: "Read by the agent", also: ["reading", "saved"] },
  { key: "review", label: "Checked by people", also: ["question", "waiting", "decided", "corrected"] },
  { key: "delivery", label: "Delivered" },
];

const DOT = { info: "bg-[var(--pill-info-ink)]", success: "bg-positive", warning: "bg-[var(--pill-warning-ink)]", error: "bg-negative" };

export default function DocumentPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const me = useStaff();
  const queryClient = useQueryClient();
  const doc = useQuery({ queryKey: ["process", id], queryFn: () => api.processes.get(id) });
  const refetching = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const onEvent = useCallback((e: { kind: string }) => {
    if (e.kind === "snapshot" || e.kind === "missed") return;
    clearTimeout(refetching.current);  // a burst of events is one refresh
    refetching.current = setTimeout(() => queryClient.invalidateQueries({ queryKey: ["process", id] }), 600);
  }, [id, queryClient]);
  const live = useLive({ scope: "document", id }, onEvent);

  if (doc.isError) return <ErrorNote message={(doc.error as Error).message} onRetry={() => doc.refetch()} />;
  const d = doc.data;
  if (!d) return <div className="flex flex-col gap-4"><Bar className="h-8 w-72" /><Bar className="h-20 w-full rounded-card" /><Bar className="h-80 w-full rounded-card" /></div>;
  const x = d.document;
  const running = d.runs.some((r) => r.status === "RUNNING");
  const retryable = !running && ["FAILED", "QUEUED", "PROCESSING", "PENDING"].includes(x.status);

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        back={{ href: "/documents", label: "‹ Documents" }}
        title={<span className="flex flex-wrap items-center gap-2">{x.name ?? `Document ${short(x.id)}`} <Pill tone={docStatus(x.status).tone}>{docStatus(x.status).label}</Pill></span>}
        subtitle={`${where(d, x.tenant_id, x.template_id)} · arrived ${dateTime(x.created_at)} by ${x.via}${x.is_test ? " · a test" : ""}`}
        actions={
          <>
            <LiveDot state={live.state} missed={live.missed} />
            {me.can("operations.manage") && retryable && <ReasonAction label="Read it again" busyLabel="Sending…" confirmLabel="Send to the agent"
              onRun={(reason) => api.processes.retry(x.id, reason).then(() => doc.refetch())} />}
          </>
        }
      />
      <Stages detail={d} />

      <section aria-label="How long each part took, and what it cost" className="grid grid-cols-2 gap-3 @3xl:grid-cols-4 @6xl:grid-cols-8">
        <Tile label="Start to now" value={duration(d.timings.total_ms)} />
        <Tile label="Waiting in the queue" value={duration(d.timings.queue_wait_ms)} />
        <Tile label="The agent's time" value={duration(d.timings.agent_ms)} />
        <Tile label="Waiting for people" value={duration(d.timings.waiting_for_people_ms)} />
        <Tile label="Delivery" value={duration(d.timings.delivery_ms)} />
        <Tile label="Agent cost" value={usd(d.timings.cost_usd)} />
        <Tile label="Tokens used" value={d.timings.tokens ? tokens(d.timings.tokens) : "—"} />
        <Tile label="Tokens in / out" value={d.timings.tokens ? `${tokens(d.timings.input_tokens)} / ${tokens(d.timings.output_tokens)}` : "—"} />
      </section>
      {(x.error || x.has_error) && (
        <p className="rounded-control bg-[var(--pill-negative-bg)] px-3 py-2 text-body text-[var(--pill-negative-ink)]">
          {x.error ?? "It has a failure note. It can quote people or the record, so it opens with the content."}
        </p>
      )}

      <div className="grid grid-cols-1 gap-6 @5xl:grid-cols-[1fr_360px]">
        <Panel title="The whole journey" aside={<span className="num">{d.timeline.length} steps · open one to see everything that happened in it</span>}>
          <Journey items={d.timeline} />
        </Panel>
        <div className="flex flex-col gap-6">
          <Panel title="Runs" aside={<span className="num">{d.runs.length}</span>}>
            {d.runs.length === 0 ? <p className="text-body text-muted">The agent hasn&apos;t picked this document up.</p> : (
              <ul className="flex flex-col gap-2">
                {d.runs.map((r) => (
                  <li key={r.id}>
                    <Link href={`/runs/${r.id}`} className="flex items-center gap-2 rounded-control px-1 py-0.5 text-body hover:bg-raised">
                      <Pill tone={status(r.status).tone}>{status(r.status).label}</Pill>
                      <span className="min-w-0 flex-1 truncate text-ink">{trigger(r.trigger)}{r.attempt > 1 ? ` · try ${r.attempt}` : ""}</span>
                      <span className="num text-meta text-muted">{r.status === "RUNNING" ? "now" : duration(r.duration_ms)}</span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
          <DocumentViewer id={x.id} contentType={x.content_type} pages={x.page_count} name={x.name} sizeBytes={x.size_bytes} />
          {(x.filename || x.external_id) && (
            <Panel title="The file">
              <dl className="grid grid-cols-2 gap-3">
                {x.filename && <Fact label="File name" className="col-span-2">{x.filename}</Fact>}
                {x.external_id && <Fact label="Sender's reference" className="col-span-2">{x.external_id}</Fact>}
              </dl>
            </Panel>
          )}
          {d.record && (
            <Panel title="The record" aside={`${Math.round(d.record.confidence * 100)}% sure · revision ${d.record.revision}`}>
              {d.record.notes && <p className="mb-2 text-body text-muted">{d.record.notes}</p>}
              <pre className="max-h-96 overflow-auto rounded-control bg-raised p-2 text-meta text-ink scrollbar-thin">{JSON.stringify(d.record.data, null, 2)}</pre>
            </Panel>
          )}
        </div>
      </div>
    </div>
  );
}

function Stages({ detail: d }: { detail: ProcessDetail }) {
  const seen = new Set(d.timeline.map((t) => t.stage));
  const failed = d.document.status === "FAILED";
  return (
    <ol className="grid grid-cols-2 gap-2 @3xl:grid-cols-5" aria-label="Stages">
      {STAGES.map((s) => {
        const reached = seen.has(s.key) || (s.also ?? []).some((a) => seen.has(a));
        return (
          <li key={s.key} className={cn("flex items-center gap-2 rounded-card px-3 py-2 text-body", reached ? "bg-card text-ink [box-shadow:var(--shadow-card)]" : "border border-dashed border-control text-faint")}>
            <span aria-hidden className={cn("size-2 rounded-full", reached ? (failed && s.key === "run" ? "bg-negative" : "bg-positive") : "bg-control")} />
            {s.label}
          </li>
        );
      })}
    </ol>
  );
}

/**
 * The journey, one step under another. Opening a step shows everything known about it (a run opens to its agent steps);
 * opening another closes it. The open step is kept in the address (#step-N), so it can be shared.
 */
function Journey({ items }: { items: TimelineItem[] }) {
  const [open, setOpen] = useState<number | null>(() => {
    if (typeof window === "undefined") return null;
    const m = window.location.hash.match(/^#step-(\d+)$/);
    return m ? Number(m[1]) : null;
  });
  const toggle = (i: number) => {
    const next = open === i ? null : i;
    setOpen(next);
    window.history.replaceState(null, "", next == null ? window.location.pathname : `#step-${next}`);
  };
  return (
    <ol className="relative flex flex-col">
      {items.map((t, i) => (
        <Step key={i} item={t} last={i === items.length - 1} previous={items[i - 1]} open={open === i} onToggle={() => toggle(i)} />
      ))}
    </ol>
  );
}

function Step({ item: t, last, previous, open, onToggle }: {
  item: TimelineItem; last: boolean; previous?: TimelineItem; open: boolean; onToggle: () => void;
}) {
  const gap = previous ? new Date(t.at).getTime() - new Date(previous.at).getTime() : null;
  return (
    <li className="relative flex gap-3 pb-3 pl-1 last:pb-0">
      {!last && <span aria-hidden className="absolute bottom-0 left-[8px] top-3 w-px bg-rule" />}
      <span aria-hidden className={cn("relative mt-2 size-2.5 flex-none rounded-full ring-2 ring-[var(--color-card)]", DOT[t.level])} />
      <div className="min-w-0 flex-1">
        <button type="button" onClick={onToggle} aria-expanded={open}
          className={cn("flex w-full items-start gap-1.5 rounded-control px-1.5 py-1 text-left hover:bg-raised", open && "bg-raised")}>
          <ChevronRight className={cn("mt-0.5 size-3.5 flex-none text-muted transition-transform", open && "rotate-90")} aria-hidden />
          <span className="min-w-0 flex-1">
            <span className="block text-body text-ink [overflow-wrap:anywhere]">
              {t.title}{t.by && <span className="text-muted"> · {t.by}</span>}
            </span>
            {t.detail && <span className="block text-meta text-muted [overflow-wrap:anywhere]">{t.detail}</span>}
            <span className="num block text-meta text-faint">{dateTime(t.at)}{gap != null && gap > 1000 ? ` · ${duration(gap)} later` : ""}{t.duration_ms ? ` · took ${duration(t.duration_ms)}` : ""}</span>
          </span>
        </button>
        {open && <StepDetails item={t} />}
      </div>
    </li>
  );
}

/** Everything known about one journey step; a run's step also lists its agent steps, fetched when opened. */
function StepDetails({ item: t }: { item: TimelineItem }) {
  const run = useQuery({ queryKey: ["run", t.run], queryFn: () => api.runs.get(t.run!), enabled: !!t.run });
  return (
    <div className="mb-1 ml-5 mt-1.5 flex flex-col gap-3 rounded-card border border-border bg-sheet p-3">
      {t.details.length > 0 ? (
        <dl className="grid grid-cols-1 gap-x-4 gap-y-2 @3xl:grid-cols-[180px_1fr]">
          {t.details.map((f, i) => (
            <div key={i} className="contents">
              <dt className="text-meta text-muted">{f.label}</dt>
              <dd className="min-w-0 text-body text-ink">
                {f.kind === "text" ? <span className="whitespace-pre-wrap [overflow-wrap:anywhere]">{String(f.value)}</span> : (
                  <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-control bg-raised p-2 text-meta scrollbar-thin [overflow-wrap:anywhere]">
                    {typeof f.value === "string" ? f.value : JSON.stringify(f.value, null, 2)}
                  </pre>
                )}
              </dd>
            </div>
          ))}
        </dl>
      ) : <p className="text-meta text-muted">Nothing more was recorded about this step.</p>}
      {t.run && (
        <div className="flex flex-col gap-2 border-t border-rule pt-3">
          <p className="flex items-center gap-2 text-meta font-semibold uppercase tracking-wide text-faint">
            The agent&apos;s steps <Link href={`/runs/${t.run}`} className="ml-auto font-normal normal-case tracking-normal text-ink underline-offset-2 hover:underline">Open the whole run</Link>
          </p>
          {run.isError ? <p className="text-meta text-negative">{(run.error as Error).message}</p> : !run.data ? <Bar className="h-16 w-full" />
            : run.data.steps.length ? <RunSteps steps={run.data.steps} prompt={run.data.prompt} />
            : <p className="text-meta text-muted">No steps were recorded (it ended before its first step, or they are past their keeping time).</p>}
        </div>
      )}
    </div>
  );
}

function Tile({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex min-w-0 flex-col gap-1 rounded-card bg-card px-4 py-3 [box-shadow:var(--shadow-card)]">
      <span className="truncate text-meta text-muted">{label}</span>
      <span className={cn("num text-section font-semibold", value === "—" ? "text-faint" : "text-ink")}>{value}</span>
    </div>
  );
}
