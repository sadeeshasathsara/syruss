"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Bar, Empty, ErrorNote, PageHeader, Panel } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { api } from "@/lib/api";
import { ACTION_GROUPS, describeAction } from "@/lib/audit";
import { cn, dateTime } from "@/lib/format";

export default function AuditLog() {
  const [group, setGroup] = useState<string>("");
  const log = useInfiniteQuery({
    queryKey: ["audit", group],
    queryFn: ({ pageParam }) => api.audit({ before: pageParam, action: group || undefined }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_before,
  });
  const items = log.data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Audit log" subtitle="Every change made with the platform tools: who, what, why and from where. Entries can't be edited or removed." />
      <div className="flex flex-wrap items-center gap-1" role="group" aria-label="Show">
        {ACTION_GROUPS.map((g) => (
          <button
            key={g.key}
            type="button"
            aria-pressed={group === g.key}
            onClick={() => setGroup(g.key)}
            className={cn("h-8 rounded-control px-3 text-body transition-colors", group === g.key ? "bg-active font-medium text-ink" : "text-muted hover:text-ink")}
          >
            {g.label}
          </button>
        ))}
      </div>
      {log.isError && <ErrorNote message="Couldn't load the audit log." onRetry={() => log.refetch()} />}
      <Panel bodyClassName="p-0">
        {log.isLoading ? (
          <div className="flex flex-col gap-3 p-4">{Array.from({ length: 6 }, (_, i) => <Bar key={i} className="h-5 w-full" />)}</div>
        ) : items.length === 0 ? (
          <div className="p-4"><Empty title="Nothing recorded yet">Changes made with these tools appear here as they happen.</Empty></div>
        ) : (
          <div className="overflow-x-auto scrollbar-thin">
            <table className="w-full min-w-[720px] text-body">
              <thead>
                <tr className="border-b border-rule text-left text-meta text-muted [&>th]:px-4 [&>th]:py-2 [&>th]:font-normal">
                  <th scope="col" className="w-40">When</th>
                  <th scope="col">What happened</th>
                  <th scope="col">Why</th>
                  <th scope="col" className="w-32">From</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-rule">
                {items.map((a) => (
                  <tr key={a.id} className="align-top [&>td]:px-4 [&>td]:py-2.5">
                    <td className="num whitespace-nowrap text-meta text-muted">{dateTime(a.created_at)}</td>
                    <td className="text-ink"><span className="font-medium">{a.actor_name}</span> {describeAction(a)}</td>
                    <td className={a.reason ? "text-ink" : "text-faint"}>{a.reason ?? "No reason given"}</td>
                    <td className="num text-meta text-muted">{a.ip ?? "Unknown"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
      {log.hasNextPage && (
        <Button className="self-center" onClick={() => log.fetchNextPage()} disabled={log.isFetchingNextPage}>
          {log.isFetchingNextPage ? "Loading…" : "Show older entries"}
        </Button>
      )}
    </div>
  );
}
