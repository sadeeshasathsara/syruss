"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo } from "react";
import { Bar, Empty, ErrorNote, PageHeader, Panel } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Pill } from "@/components/ui/Pill";
import { api } from "@/lib/api";
import { ago, cn, dateTime } from "@/lib/format";
import { docStatus, short, status, trigger, where } from "@/lib/runs";

const SHOW = [
  { key: "all", label: "All", count: (t: Record<string, number>) => Object.entries(t).filter(([k]) => k !== "stuck").reduce((a, [, n]) => a + n, 0) },
  { key: "running", label: "In progress", count: (t: Record<string, number>) => (t.QUEUED ?? 0) + (t.PROCESSING ?? 0) },
  { key: "stuck", label: "Stuck", count: (t: Record<string, number>) => t.stuck ?? 0 },
  { key: "failed", label: "Failed", count: (t: Record<string, number>) => t.FAILED ?? 0 },
  { key: "waiting", label: "Waiting for an answer", count: (t: Record<string, number>) => t.AWAITING_INPUT ?? 0 },
  { key: "review", label: "In review", count: (t: Record<string, number>) => t.NEEDS_REVIEW ?? 0 },
  { key: "done", label: "Saved", count: (t: Record<string, number>) => t.COMPLETED ?? 0 },
] as const;

export default function DocumentsPage() {
  return <Suspense><Documents /></Suspense>;
}

function Documents() {
  const router = useRouter();
  const params = useSearchParams();
  const show = params.get("show") ?? "all";
  const tenant = params.get("tenant_id") ?? undefined;
  const workspace = params.get("workspace_id") ?? undefined;
  const filters = { show, tenant_id: tenant, workspace_id: workspace };
  const docs = useInfiniteQuery({
    queryKey: ["processes", filters],
    queryFn: ({ pageParam }) => api.processes.list({ ...filters, before: pageParam ?? undefined }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_before,
    refetchInterval: 20_000,
  });
  const items = docs.data?.pages.flatMap((p) => p.items) ?? [];
  const totals = docs.data?.pages[0]?.totals;
  const names = useMemo(() => ({
    organizations: Object.assign({}, ...(docs.data?.pages.map((p) => p.organizations) ?? [])),
    workspaces: Object.assign({}, ...(docs.data?.pages.map((p) => p.workspaces) ?? [])),
  }), [docs.data]);
  const link = (k: string) => {
    const next = new URLSearchParams(params);
    next.set("show", k);
    return `/documents?${next}`;
  };

  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Documents" subtitle="Every document on the platform and where it is in its journey: open one to follow it from arrival to delivery." />
      <div className="flex flex-wrap items-center gap-1" role="group" aria-label="Show">
        {SHOW.map((s) => (
          <Link key={s.key} href={link(s.key)} aria-current={show === s.key ? "page" : undefined}
            className={cn("inline-flex h-8 items-center gap-1.5 rounded-control px-3 text-body transition-colors", show === s.key ? "bg-active font-medium text-ink" : "text-muted hover:text-ink")}>
            {s.label}
            {totals && <span className={cn("num text-meta", s.key === "stuck" && s.count(totals) ? "text-negative" : "text-muted")}>{s.count(totals).toLocaleString()}</span>}
          </Link>
        ))}
        {(tenant || workspace) && <Button size="sm" className="ml-auto" onClick={() => router.replace(`/documents?show=${show}`)}>Only {where(names, tenant, workspace)} ✕</Button>}
      </div>
      {docs.isError && <ErrorNote message="Couldn't load the documents." onRetry={() => docs.refetch()} />}
      <Panel bodyClassName="p-0">
        {docs.isLoading ? (
          <div className="flex flex-col gap-3 p-4">{Array.from({ length: 6 }, (_, i) => <Bar key={i} className="h-5 w-full" />)}</div>
        ) : items.length === 0 ? (
          <div className="p-4"><Empty title="Nothing here">{show === "stuck" ? "No document is stuck: everything queued or being read is moving." : "No documents match."}</Empty></div>
        ) : (
          <div className="overflow-x-auto scrollbar-thin">
            <table className="w-full min-w-[860px] text-body">
              <thead>
                <tr className="border-b border-rule text-left text-meta text-muted [&>th]:px-4 [&>th]:py-2 [&>th]:font-normal">
                  <th scope="col" className="w-32">Arrived</th><th scope="col">Where</th><th scope="col">Document</th><th scope="col">Now</th><th scope="col">Last run</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-rule">
                {items.map((d) => (
                  <tr key={d.id} className="cursor-pointer hover:bg-raised [&>td]:px-4 [&>td]:py-2.5" onClick={() => router.push(`/documents/${d.id}`)}>
                    <td className="num whitespace-nowrap text-meta text-muted" title={dateTime(d.created_at)}>{ago(d.created_at)}</td>
                    <td className="max-w-[240px] truncate text-ink">{where(names, d.tenant_id, d.template_id)}</td>
                    <td className="max-w-[260px]">
                      <Link href={`/documents/${d.id}`} onClick={(e) => e.stopPropagation()} className="block truncate font-medium text-ink hover:underline">{d.name ?? `Document ${short(d.id)}`}</Link>
                      <span className="text-meta text-muted">{d.via} · {d.content_type.split("/")[1] ?? d.content_type}{d.is_test ? " · test" : ""}</span>
                    </td>
                    <td>
                      <span className="flex flex-wrap gap-1"><Pill tone={docStatus(d.status).tone}>{docStatus(d.status).label}</Pill>{d.stuck && <Pill tone="negative">Stuck {ago(d.updated_at).toLowerCase()}</Pill>}</span>
                    </td>
                    <td className="text-muted">
                      {d.last_run ? <><Pill tone={status(d.last_run.status).tone}>{status(d.last_run.status).label}</Pill> <span className="text-meta">{trigger(d.last_run.trigger)}{d.runs > 1 ? ` · ${d.runs} runs` : ""}</span></> : <span className="text-meta text-faint">No run yet</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
      {docs.hasNextPage && <Button className="self-center" onClick={() => docs.fetchNextPage()} disabled={docs.isFetchingNextPage}>{docs.isFetchingNextPage ? "Loading…" : "Show older documents"}</Button>}
    </div>
  );
}
