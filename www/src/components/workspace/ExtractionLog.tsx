"use client";

import { type InfiniteData, useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useRef } from "react";
import { api } from "@/lib/api";
import { cn } from "@/lib/format";
import { useLive } from "@/lib/live";
import type { LogEntry, LogPage } from "@/lib/types";
import { useWorkspace } from "@/lib/workspace";

export type LogFilter = "all" | "problems" | "success";

const DOT: Record<LogEntry["level"], string> = {
  info: "bg-faint",
  success: "bg-positive",
  warning: "bg-[var(--pill-warning-ink)]",
  error: "bg-negative",
};

const matches = (filter: LogFilter, e: LogEntry) =>
  filter === "all" || (filter === "problems" ? e.level === "warning" || e.level === "error" : e.level === "success");

/**
 * The extraction log, newest first, a page at a time. New lines arrive on the workspace's live channel and are added
 * at the top without reloading anything; older pages load only when asked for.
 */
export function useExtractionLog(filter: LogFilter, pageSize = 40) {
  const { workspace } = useWorkspace();
  const queryClient = useQueryClient();
  const { subscribe } = useLive();
  const key = ["log", workspace.id, filter, pageSize];
  const query = useInfiniteQuery({
    queryKey: key,
    queryFn: ({ pageParam }) => api.log.list(workspace.id, { before: pageParam, level: filter, limit: pageSize }),
    initialPageParam: null as number | null,
    getNextPageParam: (last) => last.next_before,
  });

  useEffect(
    () =>
      subscribe((event) => {
        if ((event as { event_type?: string }).event_type !== "extraction_log") return;
        const entry = event as unknown as LogEntry;
        if (!entry.seq || !matches(filter, entry)) return;
        queryClient.setQueryData<InfiniteData<LogPage, number | null>>(key, (old) => {
          if (!old) return old;
          if (old.pages.some((p) => p.items.some((i) => i.seq === entry.seq))) return old;
          const [first, ...rest] = old.pages;
          return { ...old, pages: [{ ...first, items: [entry, ...first.items] }, ...rest] };
        });
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [subscribe, queryClient, filter, workspace.id, pageSize],
  );

  const items = (query.data?.pages ?? []).flatMap((p) => p.items);
  return { ...query, items };
}

const time = (iso: string) => new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });

export function dayLabel(iso: string): string {
  const d = new Date(iso);
  const today = new Date();
  const yesterday = new Date(today.getTime() - 86_400_000);
  if (d.toDateString() === today.toDateString()) return "Today";
  if (d.toDateString() === yesterday.toDateString()) return "Yesterday";
  return d.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });
}

/** One line: when, what happened, to which document, and who or what did it. */
export function LogRow({ e, compact = false }: { e: LogEntry; compact?: boolean }) {
  const { href } = useWorkspace();
  return (
    <li className={cn("flex items-start gap-3", compact ? "py-1.5" : "py-2")}>
      <span aria-hidden className={cn("mt-[7px] size-2 flex-none rounded-full", DOT[e.level])} />
      <span className="num w-[92px] flex-none whitespace-nowrap pt-px text-meta text-muted">{time(e.created_at)}</span>
      {/* File names are one long word: they break anywhere rather than run out of the box. */}
      <span className="min-w-0 flex-1 text-body text-ink [overflow-wrap:anywhere]">
        {e.document_id && e.document_name ? (
          <Link href={href(`documents/${e.document_id}`)} className="mr-1.5 font-medium hover:underline">{e.document_name}</Link>
        ) : e.document_name ? (
          <span className="mr-1.5 font-medium">{e.document_name}</span>
        ) : null}
        <span className="text-muted">{e.message}</span>
      </span>
      {!compact && <span className="hidden flex-none pt-px text-meta text-faint @2xl:block">{e.actor}</span>}
    </li>
  );
}

/**
 * The log as a box of its own height: newest at the top, and older lines load as you scroll down inside it. New lines
 * arrive at the top; if you are reading further down they are added without moving what you are reading.
 */
export function ScrollingLog({ log }: { log: ReturnType<typeof useExtractionLog> }) {
  const box = useRef<HTMLDivElement>(null);
  const end = useRef<HTMLLIElement>(null);
  const { hasNextPage, isFetchingNextPage, fetchNextPage } = log;

  useEffect(() => {
    const node = end.current;
    if (!node || !hasNextPage) return;
    const watcher = new IntersectionObserver((hits) => {
      if (hits[0]?.isIntersecting && !isFetchingNextPage) void fetchNextPage();
    }, { root: box.current, rootMargin: "80px" });
    watcher.observe(node);
    return () => watcher.disconnect();
  }, [hasNextPage, isFetchingNextPage, fetchNextPage, log.items.length]);

  return (
    <div ref={box} className="max-h-80 overflow-y-auto overflow-x-hidden pr-1 [overflow-anchor:auto]" tabIndex={0} aria-label="Latest in the log">
      <ul className="flex flex-col divide-y divide-rule">
        {log.items.map((e) => <LogRow key={e.seq} e={e} compact />)}
        <li ref={end} className="py-2 text-center text-meta text-faint" aria-hidden={!isFetchingNextPage && hasNextPage}>
          {isFetchingNextPage ? "Loading older lines…" : hasNextPage ? "" : "That is the start of the log."}
        </li>
      </ul>
    </div>
  );
}
