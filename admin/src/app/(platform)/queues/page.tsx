"use client";

import { useQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import Link from "next/link";
import { ReasonAction } from "@/components/ops/Bits";
import { useStaff } from "@/components/shell/AdminFrame";
import { Bar, ErrorNote, PageHeader, Panel } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Pill } from "@/components/ui/Pill";
import { api } from "@/lib/api";
import { ago, cn } from "@/lib/format";
import { docStatus, short, status, trigger, where } from "@/lib/runs";
import type { EventFacts, QueuesPage } from "@/lib/types";

export default function Queues() {
  const q = useQuery({ queryKey: ["queues"], queryFn: api.queues, refetchInterval: 15_000 });
  const d = q.data;
  const problems = d ? [d.not_picked_up.count && "events not picked up", d.outbox.retrying && "events failing to send",
    d.consumers.status !== "ok" && "the agent's queue can't be read", d.documents.stuck && "stuck documents", d.runs.lost_today && "lost runs"].filter(Boolean) : [];

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Queues and problems"
        subtitle={!d ? "Checking every queue…" : problems.length ? `Needs attention: ${problems.join(", ")}.` : "Work is moving: nothing is waiting longer than it should."}
        actions={<Button onClick={() => q.refetch()} disabled={q.isFetching}><RefreshCw className={cn(q.isFetching && "animate-spin")} /> Check now</Button>}
      />
      {q.isError && <ErrorNote message="Couldn't check the queues." onRetry={() => q.refetch()} />}
      {!d ? <Bar className="h-96 w-full rounded-card" /> : <Body d={d} />}
    </div>
  );
}

function Body({ d }: { d: QueuesPage }) {
  const c = d.consumers;
  return (
    <>
      <section className="grid grid-cols-2 gap-3 @3xl:grid-cols-4 @5xl:grid-cols-7">
        <Tile label="Waiting to be sent" value={d.outbox.unsent} note={d.outbox.oldest_unsent_at ? `oldest ${ago(d.outbox.oldest_unsent_at).toLowerCase()}` : d.outbox.kafka_connected ? "Kafka connected" : "Kafka not connected"} alert={!d.outbox.kafka_connected} />
        <Tile label="Held by budgets" value={d.outbox.held_by_budget} note={`${d.outbox.held_workspaces} workspace${d.outbox.held_workspaces === 1 ? "" : "s"} paused`} />
        <Tile label="Not picked up" value={d.not_picked_up.count} note="Sent over 5 minutes ago, no run" alert={!!d.not_picked_up.count} />
        <Tile label="Agent queue lag" value={c.lag} note={c.status === "ok" ? `${c.partitions?.length ?? 0} partitions` : c.detail ?? "Unknown"} alert={c.status !== "ok"} />
        <Tile label="Running" value={d.runs.running} note={`${d.runs.failed_today} failed today`} />
        <Tile label="Stuck documents" value={d.documents.stuck} alert={!!d.documents.stuck} href="/documents?show=stuck" />
        <Tile label="Deliveries that gave up" value={d.deliveries.dead} note={`${d.deliveries.retrying} retrying, ${d.deliveries.held} held`} alert={!!d.deliveries.dead} />
      </section>

      <div className="grid grid-cols-1 gap-6 @5xl:grid-cols-2">
        <Events title="Sent but never picked up" items={d.not_picked_up.items} d={d} empty="Every event sent to the agent service has a run." resend />
        <Events title="Failing to send" items={d.outbox.retrying_items} d={d} empty="No event is failing to reach Kafka." resend />
        <Panel title="Failed in the last day" aside={<Link href="/runs?status=FAILURES" className="hover:text-ink">All failures</Link>}>
          {d.runs.failed_items.length === 0 ? <p className="text-body text-muted">No run failed, gave up or was lost.</p> : (
            <ul className="flex flex-col divide-y divide-rule">
              {d.runs.failed_items.map((r) => (
                <li key={r.id} className="flex items-center gap-2 py-2 text-body">
                  <Pill tone={status(r.status).tone}>{status(r.status).label}</Pill>
                  <Link href={`/runs/${r.id}`} className="min-w-0 flex-1 truncate text-ink hover:underline">{trigger(r.trigger)} · {where(d, r.tenant_id, r.template_id)}</Link>
                  <span className="num text-meta text-muted">{ago(r.started_at)}</span>
                </li>
              ))}
            </ul>
          )}
        </Panel>
        <Panel title="Stuck documents" aside={<Link href="/documents?show=stuck" className="hover:text-ink">All stuck</Link>}>
          {d.documents.items.length === 0 ? <p className="text-body text-muted">Nothing queued too long, being read without a run, or waiting a day for an answer.</p> : (
            <ul className="flex flex-col divide-y divide-rule">
              {d.documents.items.map((x) => (
                <li key={x.id} className="flex items-center gap-2 py-2 text-body">
                  <Pill tone={docStatus(x.status).tone}>{docStatus(x.status).label}</Pill>
                  <Link href={`/documents/${x.id}`} className="min-w-0 flex-1 truncate text-ink hover:underline">Document {short(x.id)} · {where(d, x.tenant_id, x.template_id)}</Link>
                  <span className="num text-meta text-muted">since {ago(x.updated_at).toLowerCase()}</span>
                </li>
              ))}
            </ul>
          )}
        </Panel>
        {c.status === "ok" && c.partitions && c.partitions.length > 0 && (
          <Panel title={`The agent's queue (${c.topic}, group ${c.group})`}>
            <ul className="grid grid-cols-2 gap-2 @2xl:grid-cols-4">
              {c.partitions.map((p) => (
                <li key={p.partition} className="rounded-control bg-raised px-3 py-2">
                  <span className="block text-meta text-muted">Partition {p.partition}</span>
                  <span className={cn("num text-section font-semibold", p.lag ? "text-[var(--pill-warning-ink)]" : "text-ink")}>{p.lag} behind</span>
                </li>
              ))}
            </ul>
          </Panel>
        )}
      </div>
    </>
  );
}

function Events({ title, items, d, empty, resend }: { title: string; items: EventFacts[]; d: QueuesPage; empty: string; resend?: boolean }) {
  const me = useStaff();
  return (
    <Panel title={title} aside={<span className="num">{items.length}</span>}>
      {items.length === 0 ? <p className="text-body text-muted">{empty}</p> : (
        <ul className="flex flex-col divide-y divide-rule">
          {items.map((e) => (
            <li key={e.id} className="flex flex-wrap items-center gap-2 py-2 text-body">
              <span className="min-w-0 flex-1">
                <span className="text-ink">{trigger(e.type)}</span>
                <span className="text-muted"> · {where(d, e.tenant_id, e.template_id)} · queued {ago(e.created_at).toLowerCase()}</span>
                {e.last_error && <span className="block truncate text-meta text-negative" title={e.last_error}>{e.attempts} tries: {e.last_error}</span>}
                {e.published_at && <span className="block text-meta text-muted">sent {ago(e.published_at).toLowerCase()}</span>}
              </span>
              {e.document_id && <Link href={`/documents/${e.document_id}`} className="text-meta text-muted hover:underline">document {short(e.document_id)}</Link>}
              {resend && me.can("operations.manage") && <ReasonAction label="Send again" busyLabel="Sending…" confirmLabel="Send again" onRun={(reason) => api.events.resend(e.id, reason)} />}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function Tile({ label, value, note, alert, href }: { label: string; value?: number; note?: string; alert?: boolean; href?: string }) {
  const body = (
    <>
      <span className="truncate text-meta text-muted">{label}</span>
      <span className={cn("num text-figure", alert ? "text-negative" : value ? "text-ink" : "text-faint")}>{value == null ? "—" : value.toLocaleString()}</span>
      <span className="truncate text-meta text-muted">{note || " "}</span>
    </>
  );
  const cls = "flex min-w-0 flex-col gap-1 rounded-card bg-card px-4 py-3 [box-shadow:var(--shadow-card)]";
  return href ? <Link href={href} className={cn(cls, "hover:bg-raised")}>{body}</Link> : <div className={cls}>{body}</div>;
}
