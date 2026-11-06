"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { use, useCallback, useRef } from "react";
import { ContentAccess, Fact, LiveDot, ReasonAction } from "@/components/ops/Bits";
import { useStaff } from "@/components/shell/AdminFrame";
import { Bar, ErrorNote, PageHeader, Panel } from "@/components/ui/Blocks";
import { Pill } from "@/components/ui/Pill";
import { api } from "@/lib/api";
import { cn, dateTime } from "@/lib/format";
import { useLive } from "@/lib/live";
import { docStatus, duration, short, status, trigger, usd, where } from "@/lib/runs";
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
  const org = d.organizations[x.tenant_id]?.name ?? "This organization";
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
      <ContentAccess tenantId={x.tenant_id} organization={org} grant={d.grant} />
      <Stages detail={d} />

      <section aria-label="How long each part took" className="grid grid-cols-2 gap-3 @3xl:grid-cols-6">
        <Tile label="Start to now" value={duration(d.timings.total_ms)} />
        <Tile label="Waiting in the queue" value={duration(d.timings.queue_wait_ms)} />
        <Tile label="The agent's time" value={duration(d.timings.agent_ms)} />
        <Tile label="Waiting for people" value={duration(d.timings.waiting_for_people_ms)} />
        <Tile label="Delivery" value={duration(d.timings.delivery_ms)} />
        <Tile label="Agent cost" value={usd(d.timings.cost_usd)} />
      </section>
      {(x.error || x.has_error) && (
        <p className="rounded-control bg-[var(--pill-negative-bg)] px-3 py-2 text-body text-[var(--pill-negative-ink)]">
          {x.error ?? "It has a failure note. It can quote people or the record, so it opens with the content."}
        </p>
      )}

      <div className="grid grid-cols-1 gap-6 @5xl:grid-cols-[1fr_360px]">
        <Panel title="The whole journey" aside={<span className="num">{d.timeline.length} steps</span>}>
          <ol className="relative flex flex-col">
            {d.timeline.map((t, i) => <Step key={i} item={t} last={i === d.timeline.length - 1} previous={d.timeline[i - 1]} />)}
          </ol>
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
          <Panel title="The file">
            <dl className="grid grid-cols-2 gap-3">
              <Fact label="Type">{x.content_type}</Fact>
              <Fact label="Size">{Math.round(x.size_bytes / 1024).toLocaleString()} KB</Fact>
              {x.filename && <Fact label="File name" className="col-span-2">{x.filename}</Fact>}
              {x.external_id && <Fact label="Sender's reference" className="col-span-2">{x.external_id}</Fact>}
            </dl>
            {d.content_open && (x.content_type.startsWith("image/") ? (
              // eslint-disable-next-line @next/next/no-img-element
              <a href={api.processes.fileUrl(x.id)} target="_blank" rel="noreferrer"><img src={api.processes.fileUrl(x.id)} alt="The document" className="mt-3 max-h-80 w-full rounded-control border border-border object-contain" /></a>
            ) : (
              <a href={api.processes.fileUrl(x.id)} target="_blank" rel="noreferrer" className="mt-3 inline-block text-body text-ink underline-offset-2 hover:underline">Open the file</a>
            ))}
          </Panel>
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

function Step({ item: t, last, previous }: { item: TimelineItem; last: boolean; previous?: TimelineItem }) {
  const gap = previous ? new Date(t.at).getTime() - new Date(previous.at).getTime() : null;
  return (
    <li className="relative flex gap-3 pb-3 pl-1 last:pb-0">
      {!last && <span aria-hidden className="absolute bottom-0 left-[8px] top-3 w-px bg-rule" />}
      <span aria-hidden className={cn("relative mt-1.5 size-2.5 flex-none rounded-full ring-2 ring-[var(--color-card)]", DOT[t.level])} />
      <div className="min-w-0 flex-1">
        <p className="text-body text-ink [overflow-wrap:anywhere]">
          {t.run ? <Link href={`/runs/${t.run}`} className="hover:underline">{t.title}</Link> : t.title}
          {t.by && <span className="text-muted"> · {t.by}</span>}
        </p>
        {t.detail && <p className="text-meta text-muted [overflow-wrap:anywhere]">{t.detail}</p>}
        <p className="num text-meta text-faint">{dateTime(t.at)}{gap != null && gap > 1000 ? ` · ${duration(gap)} later` : ""}{t.duration_ms ? ` · took ${duration(t.duration_ms)}` : ""}</p>
      </div>
    </li>
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
