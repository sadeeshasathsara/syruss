"use client";

import { useQuery } from "@tanstack/react-query";
import { Pause, Play } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { LiveDot } from "@/components/ops/Bits";
import { Bar, Empty, PageHeader, Panel } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Pill } from "@/components/ui/Pill";
import { api } from "@/lib/api";
import { cn } from "@/lib/format";
import { useLive } from "@/lib/live";
import { useNames } from "@/lib/names";
import { duration, hoursAgo, short, status, trigger, usd, where } from "@/lib/runs";
import type { LiveFact, RunFacts } from "@/lib/types";

const FEED_MAX = 300;

type Running = Pick<RunFacts, "id" | "run_id" | "tenant_id" | "template_id" | "trigger" | "started_at" | "document_id" | "attempt" | "cost_usd"> & {
  step: number | null; tool: string | null; thinking?: boolean; ended?: string;
};

const GROUPS = [
  { key: "", label: "Everything" },
  { key: "run", label: "Runs" },
  { key: "document,extraction", label: "Documents" },
  { key: "question,conversation,message", label: "People" },
  { key: "delivery,intake", label: "In and out" },
] as const;

/** One live fact in words. */
function sentence(f: LiveFact): { text: string; tone: "info" | "positive" | "negative" | "warning" | "neutral" } {
  switch (f.kind) {
    case "run.started": return { text: `Started ${trigger(f.trigger ?? "").toLowerCase()}${(f.attempt as number) > 1 ? ` (try ${f.attempt})` : ""}`, tone: "info" };
    case "run.finished": {
      const s = status(f.status ?? "");
      return { text: `${s.label}: ${trigger(f.trigger ?? "").toLowerCase()} in ${duration(f.duration_ms)}${f.cost_usd ? `, ${usd(f.cost_usd)}` : ""}`, tone: s.tone === "info" ? "neutral" : s.tone as "positive" };
    }
    case "run.tool_done": return f.ok ? { text: `Used ${f.tool}`, tone: "neutral" } : { text: `${f.tool} was refused or failed`, tone: "warning" };
    case "run.stop_asked": return { text: "Asked to stop", tone: "warning" };
    case "document.received": return { text: "A document arrived", tone: "info" };
    case "intake.rejected": return { text: "A document was refused at the door", tone: "warning" };
    case "document.status": return { text: `Document ${String(f.status ?? "").toLowerCase().replace("_", " ")}`, tone: f.status === "FAILED" ? "negative" : "neutral" };
    case "document.stage": return { text: `Document: ${f.stage}`, tone: f.level === "error" ? "negative" : f.level === "warning" ? "warning" : f.level === "success" ? "positive" : "neutral" };
    case "extraction.saved": return { text: f.status === "COMPLETED" ? `Record saved${f.confidence != null ? ` (${Math.round(Number(f.confidence) * 100)}% sure)` : ""}` : "Record sent to review", tone: f.status === "COMPLETED" ? "positive" : "warning" };
    case "question.opened": return { text: `The agent asked a question${f.priority === "high" ? " (blocking work)" : ""}`, tone: "warning" };
    case "conversation.opened": return { text: "The agent opened a thread", tone: "neutral" };
    case "message.sent": return { text: f.private ? "A message in a private chat" : "A message in a thread", tone: "neutral" };
    case "delivery.changed": return { text: `Delivery ${f.status ?? "changed"}`, tone: f.status === "dead" ? "negative" : f.status === "delivered" ? "positive" : "neutral" };
    case "workspace.changed": return { text: `Workspace changed (${String(f.what ?? "").replace("_", " ")})`, tone: "neutral" };
    default: return { text: f.kind, tone: "neutral" };
  }
}

const TONE_DOT = { info: "bg-[var(--pill-info-ink)]", positive: "bg-positive", negative: "bg-negative", warning: "bg-[var(--pill-warning-ink)]", neutral: "bg-faint" };

export default function LivePage() {
  const [paused, setPaused] = useState(false);
  const [group, setGroup] = useState("");
  const [running, setRunning] = useState<Record<string, Running>>({});
  const [feed, setFeed] = useState<LiveFact[]>([]);
  const [now, setNow] = useState(() => Date.now());
  const { names, learn, need } = useNames();
  const hour = useQuery({ queryKey: ["runs", "hour"], queryFn: () => api.runs.list({ since: hoursAgo(1), limit: "1" }), refetchInterval: 20_000 });
  const queues = useQuery({ queryKey: ["queues"], queryFn: api.queues, refetchInterval: 20_000 });

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  const onEvent = useCallback((f: LiveFact) => {
    if (f.kind === "snapshot") {
      const items = (f.items as RunFacts[] | undefined) ?? [];
      learn(f as never);
      setRunning(Object.fromEntries(items.map((r) => [r.run_id ?? r.id, { ...r }])));
      return;
    }
    if (f.kind === "missed") return;
    need(f.tenant_id, f.template_id);
    const key = f.run_id;
    if (key && f.kind.startsWith("run.")) {
      setRunning((all) => {
        const was = all[key];
        if (f.kind === "run.started" || f.kind === "run.step") {
          return { ...all, [key]: { ...(was ?? {}), id: f.trace_id ?? was?.id ?? key, run_id: key, tenant_id: f.tenant_id!, template_id: f.template_id!,
            trigger: f.trigger ?? was?.trigger ?? "", started_at: (f.started_at as string) ?? was?.started_at ?? new Date().toISOString(),
            document_id: (f.document_id as string) ?? was?.document_id ?? null, attempt: (f.attempt as number) ?? 1, cost_usd: f.cost_usd ?? was?.cost_usd ?? null,
            step: f.step ?? was?.step ?? null, tool: f.kind === "run.step" ? (f.tool ?? null) : was?.tool ?? null, thinking: false } };
        }
        if (!was) return all;
        if (f.kind === "run.tool") return { ...all, [key]: { ...was, tool: f.tool ?? null, step: f.step ?? was.step, thinking: false } };
        if (f.kind === "run.thought") return { ...all, [key]: { ...was, step: f.step ?? was.step, thinking: true } };
        if (f.kind === "run.finished") return { ...all, [key]: { ...was, ended: f.status ?? "COMPLETED", cost_usd: f.cost_usd ?? was.cost_usd } };
        return all;
      });
      if (f.kind === "run.finished") setTimeout(() => setRunning((all) => { const next = { ...all }; delete next[key]; return next; }), 4000);
    }
    // The feed keeps what a person reads: not every thought and step.
    if (["run.thought", "run.tool", "run.step"].includes(f.kind) || (f.kind === "run.tool_done" && f.ok)) return;
    setFeed((items) => [f, ...items].slice(0, FEED_MAX));
  }, [learn, need]);

  const live = useLive({ scope: "ops" }, onEvent, paused);
  const shown = feed.filter((f) => !group || group.split(",").some((g) => f.kind.startsWith(`${g}.`)));
  const runs = Object.values(running).sort((a, b) => a.started_at.localeCompare(b.started_at));
  const t = hour.data?.totals;
  const q = queues.data;

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Live"
        subtitle="Every agent run and document on the platform as it happens, in every organization. Facts only: open a run or a document to see more."
        actions={
          <>
            <LiveDot state={live.state} missed={live.missed} />
            <Button onClick={() => setPaused((p) => !p)}>{paused ? <><Play /> Resume</> : <><Pause /> Pause</>}</Button>
          </>
        }
      />

      <section aria-label="Right now" className="grid grid-cols-2 gap-3 @3xl:grid-cols-5">
        <Tile label="Running now" value={runs.filter((r) => !r.ended).length} />
        <Tile label="Waiting to start" value={q ? q.outbox.unsent + q.not_picked_up.count : undefined} alert={!!q && q.not_picked_up.count > 0}
          note={q && (q.not_picked_up.count ? `${q.not_picked_up.count} not picked up` : q.outbox.held_by_budget ? `${q.outbox.held_by_budget} held by budgets` : "Nothing late")} href="/queues" />
        <Tile label="Runs in the last hour" value={t?.runs} note={t && `${t.failed} failed`} alert={!!t && t.failed > 0} href="/runs" />
        <Tile label="Cost in the last hour" text={t ? usd(t.cost_usd) : undefined} note={t && `${(t.tokens / 1000).toFixed(1)}k tokens`} />
        <Tile label="Stuck documents" value={q?.documents.stuck} alert={!!q?.documents.stuck} href="/documents?show=stuck" note={q && (q.runs.lost_today ? `${q.runs.lost_today} lost runs today` : "No lost runs today")} />
      </section>

      <Panel title="Running now" aside={<span className="num">{runs.length}</span>} bodyClassName="p-0">
        {live.state === "connecting" && !runs.length ? (
          <div className="flex flex-col gap-3 p-4">{[0, 1].map((i) => <Bar key={i} className="h-5 w-full" />)}</div>
        ) : runs.length === 0 ? (
          <div className="p-4"><Empty title="Nothing is running">Runs appear here the moment they start, anywhere on the platform.</Empty></div>
        ) : (
          <div className="overflow-x-auto scrollbar-thin">
            <table className="w-full min-w-[760px] text-body">
              <thead>
                <tr className="border-b border-rule text-left text-meta text-muted [&>th]:px-4 [&>th]:py-2 [&>th]:font-normal">
                  <th scope="col">Where</th><th scope="col">Doing</th><th scope="col">Now</th>
                  <th scope="col" className="text-right">For</th><th scope="col" className="text-right">Cost</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-rule">
                {runs.map((r) => (
                  <tr key={r.run_id} className={cn("[&>td]:px-4 [&>td]:py-2.5", r.ended && "opacity-60 transition-opacity")}>
                    <td className="max-w-[260px] truncate text-ink">{where(names, r.tenant_id, r.template_id)}</td>
                    <td>
                      <Link href={`/runs/${r.id}`} className="font-medium text-ink hover:underline">{trigger(r.trigger)}</Link>
                      {r.attempt > 1 && <span className="text-meta text-muted"> · try {r.attempt}</span>}
                    </td>
                    <td className="text-muted">
                      {r.ended ? <Pill tone={status(r.ended).tone}>{status(r.ended).label}</Pill>
                        : <span>Step {r.step ?? 1}{r.thinking ? " · thinking" : r.tool ? ` · ${r.tool}` : ""}</span>}
                    </td>
                    <td className="num text-right text-muted">{duration(now - new Date(r.started_at).getTime())}</td>
                    <td className="num text-right text-muted">{usd(r.cost_usd)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>

      <Panel
        title="As it happens"
        aside={
          <div className="flex flex-wrap gap-1" role="group" aria-label="Show">
            {GROUPS.map((g) => (
              <button key={g.key} type="button" aria-pressed={group === g.key} onClick={() => setGroup(g.key)}
                className={cn("h-7 rounded-control px-2.5 text-meta transition-colors", group === g.key ? "bg-active font-medium text-ink" : "text-muted hover:text-ink")}>
                {g.label}
              </button>
            ))}
          </div>
        }
        bodyClassName="p-0"
      >
        {shown.length === 0 ? (
          <div className="p-4"><Empty title={paused ? "Paused" : "Waiting for something to happen"}>New documents, saved records, questions, finished runs and failures appear here as they happen.</Empty></div>
        ) : (
          <ul className="flex max-h-[560px] flex-col divide-y divide-rule overflow-y-auto scrollbar-thin">
            {shown.map((f, i) => {
              const s = sentence(f);
              const href = f.trace_id ? `/runs/${f.trace_id}` : f.document_id ? `/documents/${f.document_id}` : undefined;
              return (
                <li key={`${f.seq ?? i}-${f.kind}`} className="flex items-baseline gap-3 px-4 py-2">
                  <span aria-hidden className={cn("size-2 flex-none translate-y-[-1px] rounded-full", TONE_DOT[s.tone])} />
                  <span className="min-w-0 flex-1 text-body text-ink">
                    {href ? <Link href={href} className="hover:underline">{s.text}</Link> : s.text}
                    <span className="text-muted"> · {where(names, f.tenant_id, f.template_id)}</span>
                    {f.document_id && !f.trace_id && <span className="num text-meta text-faint"> · {short(f.document_id)}</span>}
                  </span>
                  <span className="num flex-none text-meta text-muted">{f.at ? new Date(f.at).toLocaleTimeString() : ""}</span>
                </li>
              );
            })}
          </ul>
        )}
      </Panel>
    </div>
  );
}

function Tile({ label, value, text, note, alert, href }: { label: string; value?: number; text?: string; note?: React.ReactNode; alert?: boolean; href?: string }) {
  const body = (
    <>
      <span className="truncate text-body text-muted">{label}</span>
      {value === undefined && text === undefined ? <Bar className="h-7 w-14" /> : (
        <span className={cn("num text-figure", alert ? "text-negative" : (value ?? 1) ? "text-ink" : "text-faint")}>{text ?? value!.toLocaleString()}</span>
      )}
      <span className="truncate text-meta text-muted">{note || " "}</span>
    </>
  );
  const cls = "flex min-w-0 flex-col gap-1 rounded-card bg-card px-4 py-3 [box-shadow:var(--shadow-card)]";
  return href ? <Link href={href} className={cn(cls, "hover:bg-raised")}>{body}</Link> : <div className={cls}>{body}</div>;
}
