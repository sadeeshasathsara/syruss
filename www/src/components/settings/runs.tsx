"use client";

import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Bar, Empty, ErrorNote } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Pill } from "@/components/ui/Pill";
import { Select } from "@/components/ui/Select";
import { Markdown } from "@/components/ui/Markdown";
import { Dialog } from "@/components/ui/Sheet";
import { api } from "@/lib/api";
import { dateTime, humanize, type Tone } from "@/lib/format";
import type { AgentRun } from "@/lib/types";
import { useWorkspace } from "@/lib/workspace";
import { SettingsHeader } from "./kit";

export const TRIGGER: Record<string, string> = {
  "document.submitted": "Reading a document",
  "template.samples_added": "Studying samples",
  "human.message": "Answering a message",
  "human.answered": "Continuing after an answer",
  "review.completed": "Learning from a review",
  "proposal.decided": "Applying a decision",
  "knowledge.source_added": "Mapping a data source",
  "schedule.reflect": "Reflecting on recent work",
};
export const SOURCE: Record<string, string> = { chat: "Chat", dashboard: "Dashboard", api: "API", schedule: "Schedule", agent: "Agent", system: "Platform" };
export const STATUS: Record<string, { label: string; tone: Tone }> = {
  COMPLETED: { label: "Completed", tone: "positive" },
  BUDGET_EXCEEDED: { label: "Ran out of steps", tone: "warning" },
  FAILED: { label: "Failed", tone: "negative" },
  DEAD_LETTERED: { label: "Gave up", tone: "negative" },
};

const all = (label: string) => ({ value: "all", label });
const usd = (n: number | null) => (n == null ? "—" : n < 0.01 ? `$${n.toFixed(4)}` : `$${n.toFixed(2)}`);
const tokens = (n: number) => (n >= 10_000 ? `${(n / 1000).toFixed(1)}k` : n.toLocaleString());
const seconds = (ms: number | null) => (ms == null ? "—" : ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`);

export function who(r: Pick<AgentRun, "origin" | "actor_type" | "actor_name" | "actor_id">) {
  if (r.origin === "system") return "The platform";
  if (r.actor_type === "api_key") return r.actor_name ?? "An API key";
  return r.actor_name ?? r.actor_id ?? "Someone";
}

export function RunDetail({ id, onClose }: { id: string | null; onClose: () => void }) {
  const { workspace } = useWorkspace();
  const run = useQuery({ queryKey: ["run", id], queryFn: () => api.runs.get(workspace.id, id!), enabled: !!id });
  const r = run.data;
  return (
    <Dialog open={!!id} onOpenChange={(o) => !o && onClose()} title={r ? TRIGGER[r.trigger] ?? humanize(r.trigger) : "Run"}>
      {!r ? (
        <div className="flex flex-col gap-2"><Bar className="h-4 w-2/3" /><Bar className="h-4 w-full" /></div>
      ) : (
        <div className="flex max-h-[70dvh] flex-col gap-4 overflow-y-auto pr-1 scrollbar-thin">
          <dl className="grid grid-cols-[minmax(96px,max-content)_minmax(0,1fr)] gap-x-3 gap-y-1 text-body">
            {([
              ["When", dateTime(r.started_at)],
              ["Asked by", `${who(r)} (${SOURCE[r.source] ?? r.source})`],
              ["Document", r.document_name],
              ["Model", `${r.model}, ${r.model_calls} call${r.model_calls === 1 ? "" : "s"}`],
              ["Tokens", `${r.input_tokens.toLocaleString()} in, ${r.output_tokens.toLocaleString()} out`],
              ["Cost", usd(r.cost_usd)],
              ["Took", seconds(r.duration_ms)],
            ] as const).filter(([, v]) => v).map(([k, v]) => (
              <div key={k} className="contents">
                <dt className="text-meta text-muted">{k}</dt>
                <dd className="min-w-0 break-words text-ink">{v}</dd>
              </div>
            ))}
          </dl>
          {r.error && <p className="rounded-card bg-[var(--pill-negative-bg)] px-3 py-2 text-body text-[var(--pill-negative-ink)]">{r.error}</p>}
          {r.final_thought && <Markdown>{r.final_thought}</Markdown>}
          {r.steps.length > 0 && (
            <ol className="flex flex-col gap-2 border-t border-border pt-3">
              {r.steps.map((s, i) => (
                <li key={i} className="flex flex-col gap-1">
                  {s.thought && <Markdown>{s.thought}</Markdown>}
                  {(s.tool_calls ?? []).map((t, j) => (
                    <div key={j} className="rounded-control bg-raised px-3 py-2">
                      <p className="flex items-center gap-2 text-meta text-muted">
                        <span className="font-medium text-ink">{humanize(t.tool_name)}</span>
                        {t.status && (
                          ["ok", "success"].includes(t.status)
                            ? <Pill tone="positive">Success</Pill>
                            : <Pill tone="negative">{t.observation?.startsWith("REFUSED") ? "Refused" : "Failed"}</Pill>
                        )}
                        {t.duration_ms != null && <span className="num ml-auto">{seconds(t.duration_ms)}</span>}
                      </p>
                      {t.observation && <p className="mt-1 line-clamp-4 whitespace-pre-wrap break-words text-meta text-muted">{t.observation}</p>}
                    </div>
                  ))}
                </li>
              ))}
            </ol>
          )}
        </div>
      )}
    </Dialog>
  );
}

/** Every time the agent woke up in this workspace: why, for whom, and what it cost. */
export function RunsSettings() {
  const { workspace } = useWorkspace();
  const [origin, setOrigin] = useState("all");
  const [source, setSource] = useState("all");
  const [status, setStatus] = useState("all");
  const [open, setOpen] = useState<string | null>(null);
  const filters = { origin: origin === "all" ? "" : origin, source: source === "all" ? "" : source, status: status === "all" ? "" : status };
  const runs = useInfiniteQuery({
    queryKey: ["runs", workspace.id, filters],
    queryFn: ({ pageParam }) => api.runs.list(workspace.id, { ...filters, before: pageParam }),
    initialPageParam: "",
    getNextPageParam: (last) => (last.has_more ? last.items.at(-1)?.started_at : undefined),
  });
  const rows = runs.data?.pages.flatMap((p) => p.items) ?? [];
  const mine = runs.data?.pages[0]?.scope === "mine";

  return (
    <div className="flex flex-col gap-5">
      <SettingsHeader
        title="Agent runs"
        description={
          mine
            ? "The runs you started: what Syruss did for you, step by step, and what it cost."
            : "Every time Syruss worked in this workspace: why, for whom, what it did step by step, and what it cost."
        }
      />
      <div className="flex flex-wrap gap-3">
        <Select label="Started by" value={origin} onChange={setOrigin} className="w-auto min-w-44"
          options={[all("People and the platform"), { value: "user", label: "People" }, { value: "system", label: "The platform" }]} />
        <Select label="Source" value={source} onChange={setSource} className="w-auto min-w-36"
          options={[all("Any source"), ...Object.entries(SOURCE).map(([value, label]) => ({ value, label }))]} />
        <Select label="Status" value={status} onChange={setStatus} className="w-auto min-w-36"
          options={[all("Any status"), ...Object.entries(STATUS).map(([value, s]) => ({ value, label: s.label }))]} />
      </div>
      {runs.isError ? (
        <ErrorNote message="Couldn't load the runs." onRetry={() => runs.refetch()} />
      ) : runs.isLoading ? (
        <div className="flex flex-col gap-3">{[0, 1, 2, 3].map((i) => <Bar key={i} className="h-10 w-full" />)}</div>
      ) : rows.length === 0 ? (
        <Empty title="No runs match">Runs appear here as Syruss works: reading documents, answering messages, mapping data.</Empty>
      ) : (
        <>
          <div className="overflow-x-auto rounded-card border border-border">
            <table className="w-full min-w-[820px] border-collapse text-body">
              <thead>
                <tr className="bg-raised text-left text-meta text-muted">
                  <th className="px-4 py-2 font-normal">When</th>
                  <th className="px-3 py-2 font-normal">Run</th>
                  <th className="px-3 py-2 font-normal">Started by</th>
                  <th className="px-3 py-2 font-normal">Status</th>
                  <th className="px-3 py-2 text-right font-normal">Calls</th>
                  <th className="px-3 py-2 text-right font-normal">Tokens</th>
                  <th className="px-3 py-2 text-right font-normal">Cost</th>
                  <th className="px-4 py-2 text-right font-normal">Took</th>
                </tr>
              </thead>
              <tbody className="num">
                {rows.map((r) => (
                  <tr key={r.id} onClick={() => setOpen(r.id)} className="cursor-pointer border-t border-rule align-top transition-colors hover:bg-raised/60">
                    <td className="whitespace-nowrap px-4 py-2 text-muted">{dateTime(r.started_at)}</td>
                    <td className="max-w-64 px-3 py-2">
                      <button type="button" className="block text-left font-medium text-ink hover:underline" onClick={() => setOpen(r.id)}>
                        {TRIGGER[r.trigger] ?? humanize(r.trigger)}
                      </button>
                      {r.document_name && <span className="block truncate text-meta text-muted">{r.document_name}</span>}
                    </td>
                    <td className="px-3 py-2">
                      <span className="block text-ink">{who(r)}</span>
                      <span className="text-meta text-muted">{SOURCE[r.source] ?? r.source}</span>
                    </td>
                    <td className="px-3 py-2">{STATUS[r.status] ? <Pill tone={STATUS[r.status].tone}>{STATUS[r.status].label}</Pill> : r.status}</td>
                    <td className="px-3 py-2 text-right text-ink">{r.model_calls}</td>
                    <td className="px-3 py-2 text-right text-ink">{tokens(r.input_tokens + r.output_tokens)}</td>
                    <td className="px-3 py-2 text-right text-ink">{usd(r.cost_usd)}</td>
                    <td className="px-4 py-2 text-right text-muted">{seconds(r.duration_ms)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {runs.hasNextPage && (
            <div>
              <Button variant="secondary" disabled={runs.isFetchingNextPage} onClick={() => runs.fetchNextPage()}>
                {runs.isFetchingNextPage ? "Loading…" : "Load older"}
              </Button>
            </div>
          )}
        </>
      )}
      <RunDetail id={open} onClose={() => setOpen(null)} />
    </div>
  );
}
