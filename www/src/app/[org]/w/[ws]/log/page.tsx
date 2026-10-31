"use client";

import { Lock } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Bar, Empty, ErrorNote, PageHeader } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { dayLabel, LogRow, type LogFilter, useExtractionLog } from "@/components/workspace/ExtractionLog";
import { cn } from "@/lib/format";
import { useLive } from "@/lib/live";

const FILTERS: { key: LogFilter; label: string }[] = [
  { key: "all", label: "Everything" },
  { key: "problems", label: "Problems" },
  { key: "success", label: "Saved and approved" },
];

export default function ExtractionLogPage() {
  const [filter, setFilter] = useState<LogFilter>("all");
  const log = useExtractionLog(filter);
  const { status } = useLive();
  const more = useRef<HTMLDivElement>(null);

  // Older lines load as you reach the end of the list, one page at a time.
  const { hasNextPage, isFetchingNextPage, fetchNextPage } = log;
  useEffect(() => {
    const node = more.current;
    if (!node || !hasNextPage) return;
    const watcher = new IntersectionObserver((hits) => {
      if (hits[0]?.isIntersecting && !isFetchingNextPage) void fetchNextPage();
    }, { rootMargin: "240px" });
    watcher.observe(node);
    return () => watcher.disconnect();
  }, [hasNextPage, isFetchingNextPage, fetchNextPage, log.items.length]);

  const days: { label: string; rows: typeof log.items }[] = [];
  for (const e of log.items) {
    const label = dayLabel(e.created_at);
    const last = days.at(-1);
    if (last && last.label === label) last.rows.push(e);
    else days.push({ label, rows: [e] });
  }

  return (
    <div className="flex max-w-4xl flex-col gap-5">
      <PageHeader
        title="Extraction log"
        subtitle="What happened to each document, as it happens. Everyone in the workspace can read it; nobody can change it."
        actions={
          <span className="flex items-center gap-2 text-body text-muted" role="status">
            <span aria-hidden className={cn("size-2 rounded-full", status === "live" ? "animate-pulse bg-positive" : "bg-faint")} />
            {status === "live" ? "Live" : status === "connecting" ? "Connecting…" : "Reconnecting…"}
          </span>
        }
      />

      <div className="flex flex-wrap items-center gap-1" role="group" aria-label="Show">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            type="button"
            aria-pressed={filter === f.key}
            onClick={() => setFilter(f.key)}
            className={cn("h-8 rounded-control px-3 text-body transition-colors", filter === f.key ? "bg-active font-medium text-ink" : "text-muted hover:text-ink")}
          >
            {f.label}
          </button>
        ))}
        <span className="ml-auto flex items-center gap-1.5 text-meta text-muted"><Lock className="size-3.5" aria-hidden /> Read only</span>
      </div>

      {log.isError && <ErrorNote message="Couldn't load the log." onRetry={() => log.refetch()} />}
      {log.isLoading && <div className="flex flex-col gap-3">{Array.from({ length: 8 }, (_, i) => <Bar key={i} className="h-5 w-full" />)}</div>}
      {log.data && log.items.length === 0 && (
        <Empty title={filter === "all" ? "Nothing has happened yet" : "Nothing here"}>
          {filter === "all" ? "Upload a document and each step the agent takes with it shows up here." : "Nothing matches this view right now."}
        </Empty>
      )}

      {days.map((d) => (
        <section key={d.label} aria-label={d.label}>
          <h2 className="mb-1 text-meta font-medium uppercase tracking-wide text-muted">{d.label}</h2>
          <ul className="flex flex-col divide-y divide-rule rounded-card border border-border bg-sheet px-4">
            {d.rows.map((e) => <LogRow key={e.seq} e={e} />)}
          </ul>
        </section>
      ))}

      <div ref={more} className="flex justify-center py-2">
        {log.isFetchingNextPage ? (
          <Bar className="h-5 w-40" />
        ) : log.hasNextPage ? (
          <Button size="sm" variant="secondary" onClick={() => log.fetchNextPage()}>Load older</Button>
        ) : log.items.length > 0 ? (
          <span className="text-meta text-faint">That is the start of the log.</span>
        ) : null}
      </div>
    </div>
  );
}
