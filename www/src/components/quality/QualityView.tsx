"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, Plus, X } from "lucide-react";
import { useState } from "react";
import { Bar, Empty, PageHeader, Panel } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Pill } from "@/components/ui/Pill";
import { Select } from "@/components/ui/Select";
import { api } from "@/lib/api";
import { ago, cn, humanize, percent } from "@/lib/format";
import type { EvalItem, EvalRunInfo, LiveQuality } from "@/lib/types";
import { useWorkspace } from "@/lib/workspace";

/** "Guard[0].name" or "Guard.name" -> "Guard name": what people call it, not the path. */
function fieldLabel(path: string): string {
  const [obj, ...rest] = path.replace(/\[\d+\]/g, "").split(".");
  return rest.length ? `${humanize(obj)} ${humanize(rest.join(" ")).toLowerCase()}` : humanize(obj);
}

function Figure({ label, value, note }: { label: string; value: string; note?: string }) {
  return (
    <div className="flex min-w-0 flex-col gap-1 rounded-card border border-border bg-card p-4">
      <span className="text-meta text-muted">{label}</span>
      <span className="num text-title font-semibold text-ink">{value}</span>
      {note && <span className="text-meta text-muted">{note}</span>}
    </div>
  );
}

function Figures({ q }: { q: LiveQuality }) {
  const last = q.last_evaluation?.metrics;
  return (
    <div className="grid grid-cols-2 gap-3 @3xl:grid-cols-4">
      <Figure label="Saved without review" value={percent(q.agent.straight_through_rate)}
              note={`${q.agent.committed} of ${q.agent.decisions} documents`} />
      <Figure label="Changed by reviewers" value={percent(q.reviews.correction_rate)}
              note={`${q.reviews.corrected + q.reviews.rejected} of ${q.reviews.decided} reviews`} />
      <Figure label="Misreads it double-checked" value={String(q.agent.acknowledged_signals)}
              note="Likely misreads it looked at again and stood by" />
      <Figure label="Fields right in the last evaluation" value={percent(last?.field_accuracy ?? null)}
              note={q.last_evaluation ? ago(q.last_evaluation.completed_at) : "No evaluation yet"} />
    </div>
  );
}

function Corrections({ q }: { q: LiveQuality }) {
  if (!q.fields.length) {
    return <Empty title="No corrections in this period">When reviewers change what Syruss read, the fields they change show here.</Empty>;
  }
  return (
    <ul className="flex flex-col">
      {q.fields.map((f) => (
        <li key={f.field} className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-1 border-b border-rule py-2.5 last:border-0">
          <span className="text-body font-medium text-ink">{fieldLabel(f.field)}</span>
          <span className="num text-body text-ink">{f.corrections}×</span>
          <span className="col-span-2 text-meta text-muted [overflow-wrap:anywhere]">
            {f.examples.map((e) => `${e.from ?? "empty"} → ${e.to ?? "empty"}`).join(" · ")}
          </span>
        </li>
      ))}
    </ul>
  );
}

function Calibration({ q }: { q: LiveQuality }) {
  const rows = q.calibration.filter((c) => c.reviewed > 0);
  if (!rows.length) {
    return <Empty title="Not enough reviews yet">Once documents Syruss sent to review are decided, this shows how often its doubts were right.</Empty>;
  }
  return (
    <ul className="flex flex-col gap-3">
      {rows.map((c) => (
        <li key={c.confidence_from} className="flex flex-col gap-1.5">
          <div className="flex items-baseline justify-between gap-3 text-body">
            <span className="text-ink">When {percent(c.confidence_from)}–{percent(c.confidence_to)} sure</span>
            <span className="num text-muted">{percent(c.change_rate)} changed · {c.reviewed} reviewed</span>
          </div>
          <div className="h-1.5 overflow-hidden rounded-full bg-active">
            <div className="h-full rounded-full bg-ink" style={{ width: `${(c.change_rate ?? 0) * 100}%` }} />
          </div>
        </li>
      ))}
    </ul>
  );
}

const ITEM_STATUS: Record<EvalItem["status"], { label: string; tone: "neutral" | "positive" | "warning" | "negative" }> = {
  QUEUED: { label: "Reading", tone: "neutral" },
  ANSWERED: { label: "Answered", tone: "positive" },
  FAILED: { label: "Couldn't read", tone: "warning" },
  NO_ANSWER: { label: "No answer", tone: "warning" },
};

function RunItems({ runId }: { runId: string }) {
  const { workspace } = useWorkspace();
  const run = useQuery({
    queryKey: ["evaluation", workspace.id, runId],
    queryFn: () => api.quality.evaluation(workspace.id, runId),
    refetchInterval: (query) => (query.state.data?.status === "RUNNING" ? 4000 : false),
  });
  if (!run.data?.items) return <Bar className="h-10 w-full" />;
  return (
    <ul className="flex flex-col">
      {run.data.items.map((item) => {
        const wrong = [...(item.scores.wrong_fields ?? []), ...(item.scores.missing_fields ?? [])];
        const status = ITEM_STATUS[item.status];
        return (
          <li key={item.id} className="flex flex-col gap-1 border-b border-rule py-2.5 last:border-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="min-w-0 flex-1 truncate text-body text-ink">{item.name}</span>
              {item.status === "ANSWERED" ? (
                item.scores.exact_match ? <Pill tone="positive">All right</Pill>
                  : <Pill tone={item.scores.would_commit ? "negative" : "warning"}>
                      {item.scores.would_commit ? "Would have saved it wrong" : "Would have sent to review"}
                    </Pill>
              ) : <Pill tone={status.tone}>{status.label}</Pill>}
            </div>
            {wrong.length > 0 && (
              <span className="text-meta text-muted">Different from the verified record: {[...new Set(wrong.map(fieldLabel))].join(", ")}</span>
            )}
            {item.note && item.status !== "ANSWERED" && <span className="text-meta text-muted">{item.note}</span>}
          </li>
        );
      })}
    </ul>
  );
}

function RunRow({ run, canManage }: { run: EvalRunInfo; canManage: boolean }) {
  const { workspace } = useWorkspace();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(run.status === "RUNNING");
  const m = run.metrics;
  const cancel = useMutation({
    mutationFn: () => api.quality.cancelEvaluation(workspace.id, run.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["evals", workspace.id] }),
  });
  const done = (m.answered ?? 0) + (m.failed ?? 0) + (m.no_answer ?? 0);
  return (
    <li className="border-b border-rule last:border-0">
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
              className="flex w-full cursor-pointer flex-wrap items-center gap-x-4 gap-y-1 py-3 text-left">
        <ChevronDown className={cn("size-4 flex-none text-muted transition-transform duration-200", !open && "-rotate-90")} />
        <span className="min-w-0 flex-1 text-body text-ink">
          {ago(run.created_at)}, by {run.requested_by}
        </span>
        {run.status === "RUNNING" ? (
          <Pill tone="info">{done} of {run.item_count} read</Pill>
        ) : (
          <span className="num flex flex-wrap gap-x-4 text-body text-muted">
            <span><span className="text-ink">{percent(m.field_accuracy ?? null)}</span> fields right</span>
            <span><span className="text-ink">{percent(m.exact_match_rate ?? null)}</span> fully right</span>
            <span className={cn((m.confidently_wrong ?? 0) > 0 && "text-negative")}>
              <span className={cn((m.confidently_wrong ?? 0) === 0 && "text-ink")}>{m.confidently_wrong ?? 0}</span> saved wrong
            </span>
          </span>
        )}
      </button>
      {open && (
        <div className="animate-rise pb-3 pl-8">
          {(m.most_missed_fields?.length ?? 0) > 0 && (
            <p className="mb-2 text-body text-muted">
              Most often different: {m.most_missed_fields!.map((f) => `${fieldLabel(f.field)} (${f.documents})`).join(", ")}
            </p>
          )}
          <RunItems runId={run.id} />
          {run.status === "RUNNING" && canManage && (
            <Button variant="ghost" className="mt-2" disabled={cancel.isPending} onClick={() => cancel.mutate()}>Cancel</Button>
          )}
        </div>
      )}
    </li>
  );
}

function AddGolden({ existing, onClose }: { existing: Set<string>; onClose: () => void }) {
  const { workspace } = useWorkspace();
  const queryClient = useQueryClient();
  const docs = useQuery({ queryKey: ["documents", workspace.id, "COMPLETED"], queryFn: () => api.documents.list(workspace.id, "COMPLETED", 200) });
  const add = useMutation({
    mutationFn: (id: string) => api.quality.addGolden(workspace.id, id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["golden", workspace.id] }),
  });
  const choices = (docs.data ?? []).filter((d) => d.role === "DOCUMENT" && !existing.has(d.id));
  return (
    <div className="animate-rise rounded-card border border-control bg-card p-4">
      <div className="mb-2 flex items-center justify-between gap-3">
        <p className="text-body font-medium text-ink">Saved documents</p>
        <Button variant="ghost" onClick={onClose} aria-label="Close"><X /></Button>
      </div>
      <p className="mb-3 text-body text-muted">
        The record saved for a document becomes its expected answer. Choose ones a person checked, or check them first.
      </p>
      {add.error && <p className="mb-2 text-body text-negative">{add.error.message}</p>}
      {docs.isLoading ? <Bar className="h-10 w-full" /> : choices.length === 0 ? (
        <p className="text-body text-muted">No other saved documents.</p>
      ) : (
        <ul className="flex max-h-72 flex-col overflow-y-auto">
          {choices.map((d) => (
            <li key={d.id} className="flex items-center gap-3 border-b border-rule py-2 last:border-0">
              <span className="min-w-0 flex-1 truncate text-body text-ink">{d.original_filename}</span>
              <span className="text-meta text-muted">{ago(d.updated_at)}</span>
              <Button variant="secondary" size="sm" disabled={add.isPending} onClick={() => add.mutate(d.id)}>Add</Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Evaluations({ canManage }: { canManage: boolean }) {
  const { workspace } = useWorkspace();
  const queryClient = useQueryClient();
  const [adding, setAdding] = useState(false);
  const golden = useQuery({ queryKey: ["golden", workspace.id], queryFn: () => api.quality.golden(workspace.id) });
  const evals = useQuery({
    queryKey: ["evals", workspace.id],
    queryFn: () => api.quality.evals(workspace.id),
    refetchInterval: (query) => (query.state.data?.some((r) => r.status === "RUNNING") ? 4000 : false),
  });
  const start = useMutation({
    mutationFn: () => api.quality.startEvaluation(workspace.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["evals", workspace.id] }),
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.quality.removeGolden(workspace.id, id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["golden", workspace.id] }),
  });
  const items = golden.data ?? [];
  const running = evals.data?.some((r) => r.status === "RUNNING");

  return (
    <section className="flex flex-col gap-4">
      <div className="flex flex-col gap-3 @3xl:flex-row @3xl:items-end @3xl:justify-between">
        <div className="max-w-2xl">
          <h2 className="text-section font-semibold text-ink">Evaluations</h2>
          <p className="mt-1 text-body text-muted">
            Syruss re-reads documents your team verified, as if they were new, and is scored against their records.
            Nothing in the workspace changes. Run one after changing the template or instructions.
          </p>
        </div>
        {canManage && (
          <div className="flex flex-none gap-2">
            <Button variant="secondary" onClick={() => setAdding(!adding)}><Plus /> Add documents</Button>
            <Button variant="primary" disabled={!items.some((g) => g.can_run) || running || start.isPending} onClick={() => start.mutate()}>
              {running ? "Evaluating…" : "Run evaluation"}
            </Button>
          </div>
        )}
      </div>
      {start.error && <p className="text-body text-negative">{start.error.message}</p>}
      {adding && <AddGolden existing={new Set(items.map((g) => g.document_id ?? ""))} onClose={() => setAdding(false)} />}

      {!golden.isLoading && !evals.isLoading && items.length === 0 && (evals.data ?? []).length === 0 && (
        <Empty title="No evaluations yet">
          Add saved documents whose records a person has checked (five to twenty varied ones are a good start), then run an evaluation.
        </Empty>
      )}
      <div className="grid grid-cols-1 items-start gap-5 @4xl:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        {(golden.isLoading || items.length > 0) && (
        <Panel title="Verified documents" aside={items.length ? String(items.length) : undefined}>
          {golden.isLoading ? <Bar className="h-10 w-full" /> : (
            <ul className="flex flex-col">
              {items.map((g) => (
                <li key={g.id} className="flex items-center gap-2 border-b border-rule py-2 last:border-0">
                  <span className="min-w-0 flex-1 truncate text-body text-ink">{g.filename ?? g.name}</span>
                  {g.verified_in_review && <Pill tone="positive">Reviewed</Pill>}
                  {canManage && (
                    <Button variant="ghost" size="sm" aria-label={`Remove ${g.name}`} disabled={remove.isPending} onClick={() => remove.mutate(g.id)}>
                      <X />
                    </Button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Panel>
        )}
        {(evals.isLoading || (evals.data ?? []).length > 0) && (
          <Panel title="Runs">
            {evals.isLoading ? <Bar className="h-10 w-full" /> : (
              <ul className="flex flex-col">
                {evals.data!.map((r) => <RunRow key={r.id} run={r} canManage={canManage} />)}
              </ul>
            )}
          </Panel>
        )}
      </div>
    </section>
  );
}

export function QualityView() {
  const { workspace } = useWorkspace();
  const [days, setDays] = useState<"7" | "30" | "90">("30");
  const live = useQuery({ queryKey: ["quality", workspace.id, days], queryFn: () => api.quality.live(workspace.id, Number(days)) });
  const settings = useQuery({ queryKey: ["settings", workspace.id], queryFn: () => api.settings.get(workspace.id) });
  const canManage = !!settings.data?.access.permissions.includes("template.publish");

  return (
    <div className="flex flex-col gap-8">
      <PageHeader
        title="Quality"
        subtitle="How well Syruss reads: from your team's reviews, and from re-reading documents people verified."
        actions={
          <Select label="Period" className="w-40" value={days} onChange={setDays}
                  options={[{ value: "7", label: "Last 7 days" }, { value: "30", label: "Last 30 days" }, { value: "90", label: "Last 90 days" }]} />
        }
      />
      {!live.data ? (
        <div className="grid grid-cols-2 gap-3 @3xl:grid-cols-4">{[0, 1, 2, 3].map((i) => <Bar key={i} className="h-24 w-full" />)}</div>
      ) : (
        <>
          <Figures q={live.data} />
          {(live.data.fields.length > 0 || live.data.calibration.some((c) => c.reviewed > 0)) && (
            <div className="grid grid-cols-1 items-start gap-5 @4xl:grid-cols-2">
              {live.data.fields.length > 0 && <Panel title="What reviewers correct most"><Corrections q={live.data} /></Panel>}
              {live.data.calibration.some((c) => c.reviewed > 0) && (
                <Panel title="Is its confidence honest?">
                  <p className="mb-3 text-body text-muted">Of the documents Syruss sent to review, how often people changed them.</p>
                  <Calibration q={live.data} />
                </Panel>
              )}
            </div>
          )}
        </>
      )}
      <Evaluations canManage={canManage} />
    </div>
  );
}
