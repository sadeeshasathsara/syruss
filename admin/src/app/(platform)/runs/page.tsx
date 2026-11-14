"use client";

import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useMemo } from "react";
import { field } from "@/components/ops/Bits";
import { Bar, Empty, ErrorNote, PageHeader, Panel } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Pill } from "@/components/ui/Pill";
import { api } from "@/lib/api";
import { ago, cn, dateTime } from "@/lib/format";
import { STATUS, duration, hoursAgo, short, status, tokens, trigger, usd, where } from "@/lib/runs";
import type { RunStats } from "@/lib/types";

const PERIODS = [
  { key: "1", label: "Last hour" },
  { key: "24", label: "Last 24 hours" },
  { key: "168", label: "Last 7 days" },
  { key: "720", label: "Last 30 days" },
] as const;

export default function RunsPage() {
  return <Suspense><Runs /></Suspense>;
}

function Runs() {
  const router = useRouter();
  const params = useSearchParams();
  const get = (k: string) => params.get(k) ?? undefined;
  const hours = get("hours") ?? "24";
  const set = (k: string, v?: string) => {
    const next = new URLSearchParams(params);
    if (v) next.set(k, v);
    else next.delete(k);
    router.replace(`/runs?${next}`);
  };
  const filters = { status: get("status"), trigger: get("trigger"), model: get("model"), origin: get("origin"), tenant_id: get("tenant_id"),
                    workspace_id: get("workspace_id"), document_id: get("document_id") };
  const runs = useInfiniteQuery({
    queryKey: ["runs", filters, hours],
    queryFn: ({ pageParam }) => api.runs.list({ ...filters, since: hoursAgo(Number(hours)), before: pageParam ?? undefined }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_before ?? null,
    refetchInterval: 30_000,
  });
  const stats = useQuery({ queryKey: ["runs", "stats", hours], queryFn: () => api.runs.stats(Number(hours)), refetchInterval: 60_000 });
  const first = runs.data?.pages[0];
  const items = runs.data?.pages.flatMap((p) => p.items) ?? [];
  const names = useMemo(() => ({
    organizations: Object.assign({}, ...(runs.data?.pages.map((p) => p.organizations) ?? [])),
    workspaces: Object.assign({}, ...(runs.data?.pages.map((p) => p.workspaces) ?? [])),
  }), [runs.data]);
  const scoped = filters.tenant_id || filters.workspace_id || filters.document_id;

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Agent runs"
        subtitle={first?.totals ? `${first.totals.runs.toLocaleString()} runs, ${first.totals.failed} failed, ${first.totals.running} running · ${usd(first.totals.cost_usd)} · ${tokens(first.totals.tokens)} tokens` : "Every time the agent woke up, in every organization and workspace."}
        actions={<Button asChild><Link href="/live">Watch live</Link></Button>}
      />

      <div className="flex flex-wrap items-end gap-2">
        <Select label="When" value={hours} onChange={(v) => set("hours", v)} options={PERIODS.map((p) => [p.key, p.label])} />
        <Select label="Outcome" value={get("status") ?? ""} onChange={(v) => set("status", v)}
          options={[["", "Any"], ["FAILURES", "Failed, gave up or lost"], ...Object.entries(STATUS).map(([k, s]) => [k, s.label] as [string, string])]} />
        <Select label="Work" value={get("trigger") ?? ""} onChange={(v) => set("trigger", v)} options={[["", "Any"], ...(first?.triggers ?? []).map((t) => [t, trigger(t)] as [string, string])]} />
        <Select label="Model" value={get("model") ?? ""} onChange={(v) => set("model", v)} options={[["", "Any"], ...(first?.models ?? []).map((m) => [m, m] as [string, string])]} />
        <Select label="Asked by" value={get("origin") ?? ""} onChange={(v) => set("origin", v)} options={[["", "Anyone"], ["user", "A person or API key"], ["system", "The platform"]]} />
        {scoped && (
          <Button onClick={() => router.replace(`/runs?hours=${hours}`)}>
            Only {filters.document_id ? `document ${short(filters.document_id)}` : where(names, filters.tenant_id, filters.workspace_id)} ✕
          </Button>
        )}
      </div>

      <Chart stats={stats.data} />
      {runs.isError && <ErrorNote message="Couldn't load the runs." onRetry={() => runs.refetch()} />}

      <Panel bodyClassName="p-0">
        {runs.isLoading ? (
          <div className="flex flex-col gap-3 p-4">{Array.from({ length: 6 }, (_, i) => <Bar key={i} className="h-5 w-full" />)}</div>
        ) : items.length === 0 ? (
          <div className="p-4"><Empty title="No runs match">Try a longer period or fewer filters.</Empty></div>
        ) : (
          <div className="overflow-x-auto scrollbar-thin">
            <table className="w-full min-w-[920px] text-body">
              <thead>
                <tr className="border-b border-rule text-left text-meta text-muted [&>th]:px-4 [&>th]:py-2 [&>th]:font-normal">
                  <th scope="col" className="w-32">Started</th><th scope="col">Where</th><th scope="col">Work</th><th scope="col">Outcome</th>
                  <th scope="col" className="text-right">Took</th><th scope="col" className="text-right">Steps</th><th scope="col" className="text-right">Cost</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-rule">
                {items.map((r) => (
                  <tr key={r.id} className="cursor-pointer hover:bg-raised [&>td]:px-4 [&>td]:py-2.5" onClick={() => router.push(`/runs/${r.id}`)}>
                    <td className="num whitespace-nowrap text-meta text-muted" title={dateTime(r.started_at)}>{ago(r.started_at)}</td>
                    <td className="max-w-[240px] truncate text-ink">
                      <button type="button" className="hover:underline" onClick={(e) => { e.stopPropagation(); set("workspace_id", r.template_id); }}>{where(names, r.tenant_id, r.template_id)}</button>
                    </td>
                    <td>
                      <Link href={`/runs/${r.id}`} onClick={(e) => e.stopPropagation()} className="font-medium text-ink hover:underline">{trigger(r.trigger)}</Link>
                      <span className="block truncate text-meta text-muted">
                        {r.origin === "user" ? `Asked from ${r.source}` : "By the platform"}{r.attempt > 1 ? ` · try ${r.attempt}` : ""}
                        {r.document_name ? ` · ${r.document_name}` : r.document_id ? ` · document ${short(r.document_id)}` : ""}
                      </span>
                    </td>
                    <td>
                      <Pill tone={status(r.status).tone}>{status(r.status).label}</Pill>
                      {r.tool_errors > 0 && <span className="ml-1.5 text-meta text-[var(--pill-warning-ink)]">{r.tool_errors} refused</span>}
                    </td>
                    <td className="num text-right text-muted">{r.status === "RUNNING" ? "…" : duration(r.duration_ms)}</td>
                    <td className="num text-right text-muted">{r.steps}<span className="text-faint"> / {r.tool_calls}</span></td>
                    <td className="num text-right text-muted">{usd(r.cost_usd)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
      {runs.hasNextPage && (
        <Button className="self-center" onClick={() => runs.fetchNextPage()} disabled={runs.isFetchingNextPage}>
          {runs.isFetchingNextPage ? "Loading…" : "Show older runs"}
        </Button>
      )}
    </div>
  );
}

function Select({ label, value, onChange, options }: { label: string; value: string; onChange: (v: string) => void; options: [string, string][] }) {
  return (
    <label className="flex flex-col gap-1 text-meta text-muted">
      {label}
      <select value={value} onChange={(e) => onChange(e.target.value)} className={cn(field, "max-w-[220px]")}>
        {options.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
      </select>
    </label>
  );
}

/** Runs per hour (or day), failures in red on top, and the slowest. */
function Chart({ stats }: { stats?: RunStats }) {
  if (!stats) return <Bar className="h-[148px] w-full rounded-card" />;
  const series = stats.series;
  const max = Math.max(1, ...series.map((s) => s.runs));
  const busiest = stats.by_organization.slice(0, 4);
  return (
    <section className="grid grid-cols-1 gap-3 @4xl:grid-cols-[1fr_300px]">
      <Panel title={`Runs per ${stats.unit}`} aside={series.some((s) => s.runs) ? `slowest 5%: ${duration(Math.max(...series.map((s) => s.p95_ms ?? 0)))}` : undefined}>
        {!series.some((s) => s.runs) ? <p className="text-body text-muted">No runs in this period.</p> : (
          <div className="flex h-24 items-end gap-[2px]" role="img" aria-label={`Runs per ${stats.unit}: ${series.map((s) => s.runs).join(", ")}`}>
            {series.map((s) => (
              <div key={s.at} className="group relative flex h-full min-w-[3px] flex-1 flex-col justify-end" title={`${dateTime(s.at)}: ${s.runs} runs, ${s.failed} failed, ${usd(s.cost_usd)}, median ${duration(s.p50_ms)}`}>
                {s.failed > 0 && <div className="w-full rounded-t-[2px] bg-negative" style={{ height: `${(s.failed / max) * 100}%` }} />}
                <div className={cn("w-full bg-accent/70 group-hover:bg-accent", !s.failed && "rounded-t-[2px]")} style={{ height: `${((s.runs - s.failed) / max) * 100}%` }} />
              </div>
            ))}
          </div>
        )}
      </Panel>
      <Panel title="Busiest organizations">
        {busiest.length === 0 ? <p className="text-body text-muted">None yet.</p> : (
          <ul className="flex flex-col gap-1.5">
            {busiest.map((o) => (
              <li key={o.key} className="flex items-baseline gap-2 text-body">
                <Link href={`/runs?tenant_id=${o.key}`} className="min-w-0 flex-1 truncate text-ink hover:underline">{stats.organizations[o.key]?.name ?? short(o.key)}</Link>
                <span className="num text-meta text-muted">{o.runs}{o.failed ? <span className="text-negative"> · {o.failed} failed</span> : ""} · {usd(o.cost_usd)}</span>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </section>
  );
}
