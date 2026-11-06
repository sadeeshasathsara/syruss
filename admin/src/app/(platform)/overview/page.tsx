"use client";

import { useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import Link from "next/link";
import { Bar, ErrorNote, PageHeader, Panel } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Pill, type Tone } from "@/components/ui/Pill";
import { api } from "@/lib/api";
import { describeAction } from "@/lib/audit";
import { ago, cn, plural } from "@/lib/format";
import type { PlatformHealth, PlatformStats, ServiceStatus } from "@/lib/types";

const STATUS: Record<ServiceStatus, { tone: Tone; dot: string; label: string }> = {
  ok: { tone: "positive", dot: "bg-positive", label: "Answering" },
  degraded: { tone: "warning", dot: "bg-[var(--pill-warning-ink)]", label: "Degraded" },
  down: { tone: "negative", dot: "bg-negative", label: "Down" },
};

export default function PlatformOverview() {
  const health = useQuery({ queryKey: ["health"], queryFn: api.health, refetchInterval: 30_000 });
  const stats = useQuery({ queryKey: ["stats"], queryFn: api.stats, refetchInterval: 60_000 });
  const actions = useQuery({ queryKey: ["audit", "latest"], queryFn: () => api.audit({ limit: 8 }) });
  const failing = (health.data?.services ?? []).filter((s) => s.status !== "ok");

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Platform overview"
        subtitle={!health.data ? "Checking every service…" : failing.length ? `${plural(failing.length, "service")} need${failing.length === 1 ? "s" : ""} attention: ${failing.map((s) => s.name).join(", ")}.` : "Every service is answering."}
        actions={
          <Button onClick={() => { health.refetch(); stats.refetch(); }} disabled={health.isFetching}>
            <RefreshCw className={cn(health.isFetching && "animate-spin")} /> Check now
          </Button>
        }
      />
      {(health.isError || stats.isError) && <ErrorNote message="Couldn't load the platform's state." onRetry={() => { health.refetch(); stats.refetch(); }} />}

      <Services health={health.data} />
      <Figures stats={stats.data} />

      <Panel title="Latest staff actions" aside={<Link href="/audit" className="hover:text-ink">Open the audit log</Link>}>
        {!actions.data ? (
          <div className="flex flex-col gap-3">{[0, 1, 2].map((i) => <Bar key={i} className="h-5 w-full" />)}</div>
        ) : actions.data.items.length === 0 ? (
          <p className="text-body text-muted">Nothing yet. Every change made with these tools is recorded here, with who made it and why.</p>
        ) : (
          <ul className="flex flex-col divide-y divide-rule">
            {actions.data.items.map((a) => (
              <li key={a.id} className="flex items-baseline gap-3 py-2">
                <span className="min-w-0 flex-1 text-body text-ink">
                  <span className="font-medium">{a.actor_name}</span> {describeAction(a)}
                  {a.reason && <span className="text-muted"> · {a.reason}</span>}
                </span>
                <span className="num flex-none text-meta text-muted">{ago(a.created_at)}</span>
              </li>
            ))}
          </ul>
        )}
      </Panel>
    </div>
  );
}

function Services({ health }: { health?: PlatformHealth }) {
  return (
    <section aria-label="Services" className="grid grid-cols-1 gap-3 @2xl:grid-cols-3 @5xl:grid-cols-5">
      {(health?.services ?? Array.from({ length: 5 }, () => null)).map((s, i) =>
        s ? (
          <div key={s.key} className="flex min-w-0 flex-col gap-1.5 rounded-card bg-card px-4 py-3 [box-shadow:var(--shadow-card)]">
            <div className="flex items-center gap-2">
              <span aria-hidden className={cn("size-2 flex-none rounded-full", STATUS[s.status].dot)} />
              <span className="min-w-0 flex-1 truncate text-body font-medium text-ink">{s.name}</span>
              <Pill tone={STATUS[s.status].tone}>{STATUS[s.status].label}</Pill>
            </div>
            <p className="line-clamp-2 text-meta text-muted" title={s.detail}>{s.detail}</p>
            <p className="num text-meta text-faint">{s.latency_ms} ms</p>
          </div>
        ) : (
          <Bar key={i} className="h-[92px] w-full rounded-card" />
        ),
      )}
    </section>
  );
}

function Figure({ label, value, notes, alert }: { label: string; value?: number; notes?: (string | false)[]; alert?: boolean }) {
  return (
    <div className="flex min-w-0 flex-col gap-1 rounded-card bg-card px-4 py-3 [box-shadow:var(--shadow-card)]">
      <span className="truncate text-body text-muted">{label}</span>
      {value === undefined ? <Bar className="h-7 w-14" /> : <span className={cn("num text-figure", alert ? "text-negative" : value ? "text-ink" : "text-faint")}>{value.toLocaleString()}</span>}
      {value !== undefined && notes && <span className="text-meta text-muted">{notes.filter(Boolean).join(" · ") || " "}</span>}
    </div>
  );
}

function Figures({ stats: s }: { stats?: PlatformStats }) {
  return (
    <section aria-label="The platform in numbers" className="grid grid-cols-1 gap-3 @xl:grid-cols-2 @4xl:grid-cols-3">
      <Figure label="Organizations" value={s?.organizations.total}
        notes={s && [`${s.organizations.active} active`, !!s.organizations.suspended && `${s.organizations.suspended} suspended`, !!s.organizations.closed && `${s.organizations.closed} closed`, `${s.organizations.new_this_week} new this week`]} />
      <Figure label="People" value={s?.people.total}
        notes={s && [`${s.people.seen_this_week} seen this week`, `${s.people.new_this_week} new`, !!s.people.disabled && `${s.people.disabled} disabled`]} />
      <Figure label="Workspaces" value={s?.workspaces.total} notes={s && ["Across every organization"]} />
      <Figure label="Documents today" value={s?.documents.today}
        notes={s && [`${s.documents.total.toLocaleString()} in all`, !!s.documents.failed && `${s.documents.failed} failed`, !!s.documents.stuck && `${s.documents.stuck} stuck over 30 min`]}
        alert={!!s?.documents.stuck} />
      <Figure label="Agent runs today" value={s?.agent.runs_today}
        notes={s && [`${s.agent.failed_today} failed`, `${s.agent.events_waiting} event${s.agent.events_waiting === 1 ? "" : "s"} waiting`]}
        alert={!!s && s.agent.runs_today > 0 && s.agent.failed_today / s.agent.runs_today > 0.2} />
      <Figure label="Failed deliveries" value={s?.deliveries.dead} notes={s && ["Gave up after every retry"]} alert={!!s?.deliveries.dead} />
    </section>
  );
}
