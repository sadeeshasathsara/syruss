"use client";

import { useQuery } from "@tanstack/react-query";
import { RefreshCw, TriangleAlert } from "lucide-react";
import Link from "next/link";
import { TrendBars } from "@/components/charts/TrendBars";
import { Bar, ErrorNote, PageHeader, Panel } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Pill, type Tone } from "@/components/ui/Pill";
import { api } from "@/lib/api";
import { describeAction } from "@/lib/audit";
import { ago, cn, plural } from "@/lib/format";
import { money } from "@/lib/billing";
import type { AtRisk, BusinessMetrics, PlatformControls, PlatformHealth, PlatformStats, ServiceStatus } from "@/lib/types";

const STATUS: Record<ServiceStatus, { tone: Tone; dot: string; label: string }> = {
  ok: { tone: "positive", dot: "bg-positive", label: "Answering" },
  degraded: { tone: "warning", dot: "bg-[var(--pill-warning-ink)]", label: "Degraded" },
  down: { tone: "negative", dot: "bg-negative", label: "Down" },
};

export default function PlatformOverview() {
  const health = useQuery({ queryKey: ["health"], queryFn: api.health, refetchInterval: 30_000 });
  const stats = useQuery({ queryKey: ["stats"], queryFn: api.stats, refetchInterval: 60_000 });
  const actions = useQuery({ queryKey: ["audit", "latest"], queryFn: () => api.audit({ limit: 8 }) });
  const metrics = useQuery({ queryKey: ["console", "metrics"], queryFn: api.console.metrics, refetchInterval: 5 * 60_000 });
  const risk = useQuery({ queryKey: ["console", "at-risk"], queryFn: api.console.atRisk, refetchInterval: 5 * 60_000 });
  const controls = useQuery({ queryKey: ["console", "platform"], queryFn: api.console.platform, refetchInterval: 60_000 });
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

      <ControlsNotice controls={controls.data} />
      <Business metrics={metrics.data} />
      <Services health={health.data} />
      <Figures stats={stats.data} />
      <AtRiskList rows={risk.data} />

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

/** Anything switched off on the platform, said first: it changes what every customer sees. */
function ControlsNotice({ controls }: { controls?: PlatformControls }) {
  if (!controls) return null;
  const off = [
    controls.maintenance.on && "maintenance mode is on (the platform is read-only)",
    !controls.switches.signups && "new sign-ups are paused",
    !controls.switches.agent && "the agent is paused for everyone",
    !controls.switches.intake && "documents from other systems are paused",
    controls.agent_paused_orgs.length > 0 && `the agent is paused for ${plural(controls.agent_paused_orgs.length, "organization")}`,
  ].filter(Boolean) as string[];
  if (!off.length) return null;
  return (
    <div role="status" className="flex items-start gap-2 rounded-card bg-[var(--pill-warning-bg)] px-4 py-3 text-body text-[var(--pill-warning-ink)]">
      <TriangleAlert className="mt-0.5 size-4 flex-none" aria-hidden />
      <p className="min-w-0 flex-1">Right now {off.join("; ")}. <Link href="/platform" className="font-medium underline underline-offset-2">Platform controls</Link></p>
    </div>
  );
}

function Tile({ label, value, notes }: { label: string; value?: string; notes?: string }) {
  return (
    <div className="flex min-w-0 flex-col gap-1 rounded-card bg-card px-4 py-3 [box-shadow:var(--shadow-card)]">
      <span className="truncate text-body text-muted">{label}</span>
      {value === undefined ? <Bar className="h-7 w-24" /> : <span className="num text-figure text-ink">{value}</span>}
      {value !== undefined && notes && <span className="text-meta text-muted">{notes}</span>}
    </div>
  );
}

/** How the business is doing: what recurs each month, who is paying and active, and the last 30 days a day at a time. */
function Business({ metrics: m }: { metrics?: BusinessMetrics }) {
  const revenue = m?.revenue[0];
  const others = m?.revenue.slice(1) ?? [];
  const o = m?.organizations;
  const usd = (v: number) => `$${v.toFixed(v >= 100 ? 0 : 2)}`;
  return (
    <section aria-label="The business" className="flex flex-col gap-3">
      <div className="grid grid-cols-1 gap-3 @xl:grid-cols-2 @4xl:grid-cols-4">
        <Tile label="Monthly recurring revenue" value={m ? (revenue ? money(revenue.mrr_cents, revenue.currency) : money(0)) : undefined}
          notes={m && [revenue && `${money(revenue.arr_cents, revenue.currency)} a year`, ...others.map((r) => `+ ${money(r.mrr_cents, r.currency)}`)].filter(Boolean).join(" · ")} />
        <Tile label="Paying organizations" value={o?.paying.toLocaleString()} notes={o && `${o.trialing} on a trial · ${o.active.toLocaleString()} open in all`} />
        <Tile label="Active this week" value={o?.active_7d.toLocaleString()} notes={o && `${o.active_30d} this month · sent a document or ran the agent`} />
        <Tile label="Lost this month" value={o ? String(o.closed_this_month + o.lapsed_this_month) : undefined}
          notes={o && `${o.closed_this_month} closed · ${o.lapsed_this_month} moved to Free unpaid`} />
      </div>
      <div className="grid grid-cols-1 gap-3 @xl:grid-cols-2 @5xl:grid-cols-4">
        <TrendBars title="New organizations" points={m?.series.organizations} note={m && `${m.series.people.reduce((a, p) => a + p.value, 0)} new people`} />
        <TrendBars title="Documents received" points={m?.series.documents} />
        <TrendBars title="Pages read" points={m?.series.pages} />
        <TrendBars title="Agent cost" points={m?.series.agent_cost_usd} format={usd}
          note={m && `${m.runs.last_30d.toLocaleString()} runs · ${m.runs.failed_30d} failed`} />
      </div>
    </section>
  );
}

const LEVEL: Record<string, Tone> = { high: "negative", medium: "warning", low: "neutral" };

/** Customers worth a look now, and why. */
function AtRiskList({ rows }: { rows?: AtRisk[] }) {
  return (
    <Panel title="Customers worth a look" aside={rows ? <span className="num">{rows.length}</span> : undefined}>
      {!rows ? (
        <div className="flex flex-col gap-3">{[0, 1, 2].map((i) => <Bar key={i} className="h-5 w-full" />)}</div>
      ) : rows.length === 0 ? (
        <p className="text-body text-muted">Nobody right now: no renewal past due, no trial ending this week, no sharp drop in use and no run of failures.</p>
      ) : (
        <ul className="flex flex-col divide-y divide-rule">
          {rows.map((r) => (
            <li key={r.tenant_id} className="flex flex-wrap items-center gap-x-3 gap-y-1.5 py-2.5">
              <Link href={`/accounts/organizations/${r.tenant_id}`} className="min-w-0 flex-1 truncate text-body font-medium text-ink hover:underline">{r.name}</Link>
              {r.plan && <span className="text-meta text-muted">{r.plan}</span>}
              <span className="flex flex-wrap gap-1.5">{r.reasons.map((x) => <Pill key={x.code} tone={LEVEL[x.level]}>{x.text}</Pill>)}</span>
            </li>
          ))}
        </ul>
      )}
    </Panel>
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
