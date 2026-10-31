"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { ConnectionsHealth } from "@/components/connections/HealthPanel";
import { Bar, Empty, PageHeader, Panel } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { StatusPill } from "@/components/ui/Pill";
import { ScrollingLog, useExtractionLog } from "@/components/workspace/ExtractionLog";
import { api } from "@/lib/api";
import { ago, cn } from "@/lib/format";
import type { DocumentStatus, Overview } from "@/lib/types";
import { useWorkspace } from "@/lib/workspace";

const STRIP: { status: DocumentStatus | "ALL"; label: string; tab: string }[] = [
  { status: "ALL", label: "Documents", tab: "" },
  { status: "COMPLETED", label: "Done", tab: "done" },
  { status: "NEEDS_REVIEW", label: "Needs review", tab: "review" },
  { status: "AWAITING_INPUT", label: "Awaiting answer", tab: "waiting" },
  { status: "FAILED", label: "Failed", tab: "failed" },
];

export default function Overview() {
  const { workspace, href } = useWorkspace();
  const wid = workspace.id;
  const documents = useQuery({ queryKey: ["documents", wid], queryFn: () => api.documents.list(wid, undefined, 200) });
  const reviews = useQuery({ queryKey: ["reviews", wid], queryFn: () => api.reviews.list(wid) });
  const questions = useQuery({ queryKey: ["questions", wid, "OPEN"], queryFn: () => api.questions.list(wid) });
  const proposals = useQuery({ queryKey: ["proposals", wid, "PENDING"], queryFn: () => api.proposals.list(wid) });
  const overview = useQuery({ queryKey: ["overview", wid], queryFn: () => api.overview(wid, 14) });
  const latest = useExtractionLog("all", 20);

  const docs = documents.data ?? [];
  const count = (s: DocumentStatus | "ALL") => (s === "ALL" ? docs.length : docs.filter((d) => d.status === s).length);
  const working = docs.filter((d) => d.status === "QUEUED" || d.status === "PROCESSING").length;
  const waiting = (reviews.data?.length ?? 0) + (questions.data?.length ?? 0) + (proposals.data?.length ?? 0);

  const subtitle = !documents.data
    ? " "
    : docs.length === 0
      ? "No documents yet. Upload some and the agent starts reading them straight away."
      : [
          waiting ? `${waiting} item${waiting === 1 ? " is" : "s are"} waiting on you` : "Nothing is waiting on you",
          working ? `the agent is reading ${working} document${working === 1 ? "" : "s"}` : null,
        ]
          .filter(Boolean)
          .join(", ") + ".";

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title={workspace.name}
        subtitle={subtitle}
        actions={
          <Button asChild variant="primary">
            <Link href={href("documents?upload=1")}>Upload documents</Link>
          </Button>
        }
      />

      {/* One container split by rules; each figure opens its filtered list. */}
      <section aria-label="Documents by status" className="grid grid-cols-2 overflow-hidden rounded-card bg-card [box-shadow:var(--shadow-card)] @2xl:grid-cols-5">
        {STRIP.map((s, i) => (
          <Link
            key={s.label}
            href={href(`documents${s.tab ? `?view=${s.tab}` : ""}`)}
            className={cn(
              "flex min-w-0 flex-col gap-1 border-border px-4 py-3 hover:bg-raised [&>span:first-child]:truncate",
              i > 0 && "@2xl:border-l",
              i % 2 === 1 && "border-l @2xl:border-l",
              i >= 2 && "border-t @2xl:border-t-0",
              i === STRIP.length - 1 && "col-span-2 @2xl:col-span-1",
            )}
          >
            <span className="text-body text-muted">{s.label}</span>
            {documents.data ? (
              <span className={cn("num text-figure", count(s.status) ? "text-ink" : "text-faint")}>{count(s.status)}</span>
            ) : (
              <Bar className="h-7 w-10" />
            )}
          </Link>
        ))}
      </section>

      <section aria-label="How work is going" className="grid grid-cols-1 gap-4 @3xl:grid-cols-3">
        <Insight
          label="Saved without a person"
          value={overview.data ? (overview.data.hands_free_rate === null ? "No data yet" : `${Math.round(overview.data.hands_free_rate * 100)}%`) : undefined}
          note="Of what was saved in the last 30 days, the share nobody had to check."
        />
        <Insight
          label="Time to save"
          value={overview.data ? (overview.data.avg_seconds_to_save === null ? "No data yet" : duration(overview.data.avg_seconds_to_save)) : undefined}
          note="Average from upload to saved record, last 30 days."
        />
        <Panel title="This month" className="@3xl:col-span-1">
          {overview.data ? (
            <div className="flex flex-col gap-3">
              <Meter label="Documents" used={overview.data.usage.documents_this_month} limit={overview.data.usage.documents_limit} />
              <Meter label="People" used={overview.data.usage.members} limit={overview.data.usage.members_limit} />
              <p className="text-meta text-muted">{overview.data.usage.plan} plan</p>
            </div>
          ) : (
            <div className="flex flex-col gap-3"><Bar className="h-4 w-full" /><Bar className="h-4 w-full" /></div>
          )}
        </Panel>
      </section>

      <Panel title="Documents received" aside="Last 14 days">
        {overview.data ? <Throughput days={overview.data.days} /> : <Bar className="h-40 w-full" />}
      </Panel>

      <ConnectionsHealth />

      <div className="grid grid-cols-1 items-start gap-6 @4xl:grid-cols-2">
        <Panel title="Waiting on you" aside={waiting ? <span className="num">{waiting}</span> : undefined}>
          {waiting === 0 ? (
            <p className="text-body text-muted">No reviews, questions or proposals right now.</p>
          ) : (
            <ul className="flex flex-col">
              {(questions.data ?? []).slice(0, 3).map((q) => (
                <li key={q.id} className="border-b border-rule py-2.5 last:border-0">
                  <Link href={href("agent")} className="block hover:underline">
                    <span className="block text-body text-ink">{q.question}</span>
                    <span className="text-meta text-muted">Question from the agent, {ago(q.created_at).toLowerCase()}</span>
                  </Link>
                </li>
              ))}
              {(proposals.data ?? []).slice(0, 2).map((p) => (
                <li key={p.id} className="border-b border-rule py-2.5 last:border-0">
                  <Link href={href("agent")} className="block hover:underline">
                    <span className="line-clamp-2 block text-body text-ink">{p.observation}</span>
                    <span className="text-meta text-muted">Proposed change, {ago(p.created_at).toLowerCase()}</span>
                  </Link>
                </li>
              ))}
              {(reviews.data ?? []).slice(0, 5).map((r) => (
                <li key={r.id} className="border-b border-rule py-2.5 last:border-0">
                  <Link href={href(`review?item=${r.id}`)} className="block hover:underline">
                    <span className="line-clamp-2 block text-body text-ink">{r.reason}</span>
                    <span className="text-meta text-muted">Review, {ago(r.created_at).toLowerCase()}</span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel title="Latest in the log" aside={<Link href={href("log")} className="hover:text-ink">Open the log</Link>}>
          {latest.isLoading ? (
            <div className="flex flex-col gap-3">{[0, 1, 2, 3].map((i) => <Bar key={i} className="h-5 w-full" />)}</div>
          ) : latest.items.length === 0 ? (
            <p className="text-body text-muted">Nothing has happened yet. Each step the agent takes with a document appears here.</p>
          ) : (
            <ScrollingLog log={latest} />
          )}
        </Panel>
      </div>

      <div className="grid grid-cols-1 gap-6">
        <Panel
          title="Recent documents"
          aside={docs.length > 6 ? <Link href={href("documents")} className="hover:text-ink">View all</Link> : undefined}
        >
          {documents.isLoading ? (
            <div className="flex flex-col gap-3">{[0, 1, 2].map((i) => <Bar key={i} className="h-5 w-full" />)}</div>
          ) : docs.length === 0 ? (
            <Empty title="No documents yet" action={<Button asChild size="sm" variant="secondary"><Link href={href("documents?upload=1")}>Upload documents</Link></Button>}>
              Drop in a batch; each one shows up here as the agent reads it.
            </Empty>
          ) : (
            <ul className="flex flex-col">
              {docs.slice(0, 6).map((d) => (
                <li key={d.id} className="border-b border-rule last:border-0">
                  <Link href={href(`documents/${d.id}`)} className="flex items-center gap-3 py-2.5 hover:underline">
                    <span className="min-w-0 flex-1 truncate text-body text-ink">{d.original_filename}</span>
                    <StatusPill status={d.status} />
                    <span className="num w-16 flex-none text-right text-meta text-muted">{ago(d.created_at)}</span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>
    </div>
  );
}

/** "42 s", "3 min", "1.4 h". */
function duration(seconds: number): string {
  if (seconds < 90) return `${Math.max(1, Math.round(seconds))} s`;
  if (seconds < 5400) return `${Math.round(seconds / 60)} min`;
  return `${(seconds / 3600).toFixed(1)} h`;
}

function Insight({ label, value, note }: { label: string; value?: string; note: string }) {
  return (
    <Panel title={label}>
      {value === undefined ? <Bar className="h-8 w-24" /> : <p className={cn("num text-figure", value.startsWith("No data") ? "text-faint" : "text-ink")}>{value}</p>}
      <p className="mt-1 text-meta text-muted">{note}</p>
    </Panel>
  );
}

function Meter({ label, used, limit }: { label: string; used: number; limit: number }) {
  const share = limit > 0 ? Math.min(1, used / limit) : 0;
  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between text-body">
        <span className="text-muted">{label}</span>
        <span className="num text-ink">{used.toLocaleString()} <span className="text-muted">of {limit.toLocaleString()}</span></span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-active" role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={limit} aria-valuenow={used}>
        <div className={cn("h-full rounded-full", share >= 0.9 ? "bg-negative" : share >= 0.7 ? "bg-[var(--pill-warning-ink)]" : "bg-accent")} style={{ width: `${share * 100}%` }} />
      </div>
    </div>
  );
}

const SEGMENTS = [
  { key: "saved", label: "Saved", tone: "bg-positive" },
  { key: "review", label: "Needs a person", tone: "bg-[var(--pill-warning-ink)]" },
  { key: "failed", label: "Failed", tone: "bg-negative" },
  { key: "working", label: "Being read", tone: "bg-faint" },
] as const;

/** One column per day, stacked by where each document stands now. */
function Throughput({ days }: { days: Overview["days"] }) {
  const max = Math.max(1, ...days.map((d) => d.received));
  const total = days.reduce((n, d) => n + d.received, 0);
  if (total === 0) return <p className="text-body text-muted">No documents were received in this period.</p>;
  return (
    <div>
      <div role="img" aria-label={`${total} documents received in the last ${days.length} days`} className="flex h-40 items-end gap-1.5">
        {days.map((d) => {
          const label = new Date(d.date + "T00:00:00").toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
          return (
            <div
              key={d.date}
              className="group flex h-full min-w-0 flex-1 flex-col-reverse gap-px"
              title={`${label}: ${d.received} received (${d.saved} saved, ${d.review} need a person, ${d.failed} failed)`}
            >
              {SEGMENTS.map((s) => d[s.key] > 0 && (
                <span key={s.key} className={cn("block w-full first:rounded-b-[3px] last:rounded-t-[3px]", s.tone)} style={{ height: `${(d[s.key] / max) * 100}%` }} />
              ))}
              {d.received === 0 && <span className="block h-px w-full bg-border" />}
            </div>
          );
        })}
      </div>
      <div className="mt-2 flex justify-between text-meta text-muted">
        <span>{new Date(days[0].date + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short" })}</span>
        <span>Busiest day: {max}</span>
        <span>Today</span>
      </div>
      <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-meta text-muted">
        {SEGMENTS.map((s) => (
          <li key={s.key} className="flex items-center gap-1.5"><span aria-hidden className={cn("size-2 rounded-full", s.tone)} />{s.label}</li>
        ))}
      </ul>
    </div>
  );
}
