"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Bar } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Field";
import { Pill } from "@/components/ui/Pill";
import { Select } from "@/components/ui/Select";
import { api } from "@/lib/api";
import { cn, dateTime, percent } from "@/lib/format";
import type { BudgetStatus, WorkspaceSettingsData } from "@/lib/types";
import { useWorkspace } from "@/lib/workspace";
import { NoAccess, SavedNote, Section, SettingRow, SettingsHeader, useWorkspaceSettings } from "./kit";

function Loading() {
  return <div className="flex flex-col gap-3 pt-6">{[0, 1, 2].map((i) => <Bar key={i} className="h-10 w-full" />)}</div>;
}

const STATE: Record<BudgetStatus["state"], { label: string; tone: "neutral" | "positive" | "warning" | "negative" }> = {
  unlimited: { label: "No limit", tone: "neutral" },
  ok: { label: "Within budget", tone: "positive" },
  warning: { label: "Nearly used", tone: "warning" },
  reached: { label: "Used up", tone: "negative" },
};

const day = (iso: string) => new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "long" });

function Meter({ share }: { share: number }) {
  const width = Math.min(1, share) * 100;
  return (
    <div className="h-2 w-full overflow-hidden rounded-full bg-active" role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(share * 100)}>
      <div
        className={cn("h-full rounded-full transition-[width] duration-500", share >= 1 ? "bg-negative" : share >= 0.8 ? "bg-[var(--pill-warning-ink)]" : "bg-ink")}
        style={{ width: `${width}%` }}
      />
    </div>
  );
}

export function BudgetSettings() {
  const { workspace } = useWorkspace();
  const queryClient = useQueryClient();
  const { settings, access, save, can } = useWorkspaceSettings();
  const status = useQuery({ queryKey: ["budget", workspace.id], queryFn: () => api.settings.budget(workspace.id) });
  const editable = can("workspace.manage");
  const [cost, setCost] = useState<string | null>(null);
  const [tokens, setTokens] = useState<string | null>(null);
  const budget = settings?.budget;
  const costValue = cost ?? (budget?.monthly_cost_usd != null ? String(budget.monthly_cost_usd) : "");
  const tokenValue = tokens ?? (budget?.monthly_tokens != null ? String(budget.monthly_tokens) : "");
  const s = status.data;

  const saveLimits = (patch: Partial<WorkspaceSettingsData["budget"]>) =>
    save.mutate({ budget: patch }, {
      onSuccess: () => {
        setCost(null);
        setTokens(null);
        queryClient.invalidateQueries({ queryKey: ["budget", workspace.id] });
      },
    });

  return (
    <div className="flex flex-col">
      <SettingsHeader title="Budget" description="Cap what Syruss uses each calendar month (UTC). Admins are told at 80% and when it's used up." />
      {!settings || !s ? <Loading /> : (
        <>
          <Section title="This month" description={`Counted from the agent's run log. Starts again on ${day(s.resets_at)}.`}>
            <div className="flex flex-col gap-4 rounded-card border border-border bg-card p-4">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <Pill tone={STATE[s.state].tone}>{STATE[s.state].label}</Pill>
                {s.share !== null && <span className="num text-body text-muted">{percent(s.share)} used</span>}
              </div>
              {s.share !== null && <Meter share={s.share} />}
              <dl className="grid grid-cols-2 gap-4">
                <div>
                  <dt className="text-meta text-muted">Spent</dt>
                  <dd className="num text-section font-semibold text-ink">
                    ${s.used.cost_usd.toFixed(2)}
                    {s.limits.monthly_cost_usd ? <span className="text-body font-normal text-muted"> of ${s.limits.monthly_cost_usd.toFixed(2)}</span> : null}
                  </dd>
                </div>
                <div>
                  <dt className="text-meta text-muted">Tokens</dt>
                  <dd className="num text-section font-semibold text-ink">
                    {s.used.tokens.toLocaleString()}
                    {s.limits.monthly_tokens ? <span className="text-body font-normal text-muted"> of {s.limits.monthly_tokens.toLocaleString()}</span> : null}
                  </dd>
                </div>
              </dl>
              {s.paused && (
                <p className="rounded-control bg-raised px-3 py-2 text-body text-ink">
                  Syruss is paused. New work waits, nothing is lost, and chats get a short notice. Raise the budget to resume now.
                </p>
              )}
              {s.used.runs_without_price > 0 && (
                <p className="text-body text-muted">
                  {s.used.runs_without_price} run{s.used.runs_without_price === 1 ? "" : "s"} this month used a model with no price set,
                  so the spend above leaves {s.used.runs_without_price === 1 ? "it" : "them"} out. Token limits count every run.
                </p>
              )}
            </div>
          </Section>

          <Section title="Limits" description="Leave a limit empty for none. With both set, whichever is reached first counts.">
            {!editable && access && <div className="mb-4"><NoAccess what="change the budget" role={access.role_label} /></div>}
            <form
              className="flex flex-col gap-4 rounded-card border border-border bg-card p-4"
              onSubmit={(e) => {
                e.preventDefault();
                saveLimits({
                  monthly_cost_usd: costValue.trim() ? Number(costValue) : null,
                  monthly_tokens: tokenValue.trim() ? Math.round(Number(tokenValue)) : null,
                });
              }}
            >
              <div className="grid grid-cols-1 gap-4 @xl:grid-cols-2">
                <label className="flex flex-col gap-2 text-body font-medium text-ink">
                  Monthly spend (USD)
                  <Input type="number" min={0.01} step={0.01} inputMode="decimal" placeholder="No limit" disabled={!editable}
                         value={costValue} onChange={(e) => setCost(e.target.value)} />
                </label>
                <label className="flex flex-col gap-2 text-body font-medium text-ink">
                  Monthly tokens
                  <Input type="number" min={1} step={1000} inputMode="numeric" placeholder="No limit" disabled={!editable}
                         value={tokenValue} onChange={(e) => setTokens(e.target.value)} />
                </label>
              </div>
              <div className="flex items-center gap-3">
                <Button type="submit" variant="primary" disabled={!editable || (cost === null && tokens === null) || save.isPending}>Save limits</Button>
                <SavedNote pending={save.isPending} error={save.error} saved={save.isSuccess && cost === null && tokens === null} />
              </div>
            </form>
          </Section>

          <Section title="When it's used up">
            <div className="rounded-card border border-border bg-card p-4">
              <SettingRow
                label="What Syruss does"
                help="Pausing holds new work until the budget is raised or the month turns. A run already under way finishes."
                control={
                  <Select
                    label="When the budget is used up"
                    className="w-48"
                    disabled={!editable}
                    value={settings.budget.when_reached}
                    onChange={(v) => saveLimits({ when_reached: v })}
                    options={[{ value: "warn", label: "Keep working, warn" }, { value: "pause", label: "Pause the agent" }]}
                  />
                }
              />
            </div>
          </Section>
        </>
      )}
    </div>
  );
}

const STATUS_TEXT: Record<string, string> = {
  woke_agent: "Syruss looked back over the work",
  nothing_new: "Nothing new to look back on, so Syruss wasn't woken",
};

export function ReflectionSettings() {
  const { workspace } = useWorkspace();
  const queryClient = useQueryClient();
  const { settings, access, save, can } = useWorkspaceSettings();
  const editable = can("workspace.manage");
  const state = useQuery({ queryKey: ["reflection", workspace.id], queryFn: () => api.settings.reflection(workspace.id) });
  const now = useMutation({
    mutationFn: () => api.settings.reflectNow(workspace.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["reflection", workspace.id] }),
  });
  const reflect = state.data?.reflect;
  const graph = state.data?.graph;
  const counts = (reflect?.state ?? {}) as { retired?: number; merged?: number; requested_by?: string | null };
  const graphState = (graph?.state ?? {}) as { pairs?: number; edges?: number; age_synced?: boolean; built?: boolean };

  return (
    <div className="flex flex-col">
      <SettingsHeader
        title="Reflection"
        description="Syruss regularly looks back over recent work and your team's corrections, turns them into lessons, and tidies its memory."
        actions={editable ? (
          <Button variant="secondary" disabled={now.isPending} onClick={() => now.mutate()}>
            {now.isPending ? "Starting…" : "Reflect now"}
          </Button>
        ) : undefined}
      />
      {!settings || !state.data ? <Loading /> : (
        <>
          {now.error && <p className="pt-4 text-body text-negative">{now.error.message}</p>}
          <Section title="Schedule" description="A quiet workspace costs nothing: if nothing happened since last time, only the memory upkeep runs.">
            {!editable && access && <div className="mb-4"><NoAccess what="change the schedule" role={access.role_label} /></div>}
            <div className="rounded-card border border-border bg-card p-4">
              <SettingRow
                label="How often"
                control={
                  <Select
                    label="How often Syruss reflects"
                    className="w-40"
                    disabled={!editable}
                    value={settings.agent.reflection}
                    onChange={(v) => save.mutate({ agent: { reflection: v } }, { onSuccess: () => queryClient.invalidateQueries({ queryKey: ["reflection", workspace.id] }) })}
                    options={[{ value: "daily", label: "Every day" }, { value: "weekly", label: "Every week" }, { value: "off", label: "Off" }]}
                  />
                }
              />
            </div>
            <div className="mt-2"><SavedNote pending={save.isPending} error={save.error} saved={save.isSuccess} /></div>
          </Section>

          <Section title="Screen suggestions" description="Syruss can look through your saved records now and then and suggest analytics screens worth having. Nothing is added until someone keeps it.">
            <div className="rounded-card border border-border bg-card p-4">
              <SettingRow
                label="How often"
                control={
                  <Select
                    label="How often Syruss looks for useful analytics screens"
                    className="w-40"
                    disabled={!editable}
                    value={settings.agent.insights ?? "off"}
                    onChange={(v) => save.mutate({ agent: { insights: v } })}
                    options={[{ value: "weekly", label: "Every week" }, { value: "off", label: "Off" }]}
                  />
                }
              />
            </div>
          </Section>

          <Section title="Last time" description="Memory upkeep is done by the platform, not the model: it retires unused low-importance observations after 30 days and merges memories that say the same thing.">
            {!reflect?.last_run_at ? (
              <p className="text-body text-muted">Syruss hasn&rsquo;t reflected in this workspace yet.</p>
            ) : (
              <dl className="grid grid-cols-1 gap-x-6 gap-y-3 rounded-card border border-border bg-card p-4 @xl:grid-cols-2">
                <div>
                  <dt className="text-meta text-muted">When</dt>
                  <dd className="text-body text-ink">{dateTime(reflect.last_run_at)}{counts.requested_by ? `, asked by ${counts.requested_by}` : ""}</dd>
                </div>
                <div>
                  <dt className="text-meta text-muted">Next</dt>
                  <dd className="text-body text-ink">{reflect.next_run_at ? dateTime(reflect.next_run_at) : "Not scheduled"}</dd>
                </div>
                <div className="@xl:col-span-2">
                  <dt className="text-meta text-muted">What happened</dt>
                  <dd className="text-body text-ink">
                    {STATUS_TEXT[reflect.last_status ?? ""] ?? reflect.last_status}. Retired {counts.retired ?? 0} and merged {counts.merged ?? 0} memories.
                  </dd>
                </div>
              </dl>
            )}
          </Section>

          <Section title="Workspace graph" description="Who and what appear together in committed records. It lets the platform stop likely misreads, like an ID one digit away from someone who's always on that site.">
            <div className="rounded-card border border-border bg-card p-4">
              {!graphState.built ? (
                <p className="text-body text-muted">Built automatically once the template is published.</p>
              ) : (
                <p className="text-body text-ink">
                  {(graphState.pairs ?? 0).toLocaleString()} connections from past records
                  {graphState.age_synced
                    ? `, queried as a graph with Apache AGE (${(graphState.edges ?? 0).toLocaleString()} edges).`
                    : ". The database doesn't have Apache AGE, so the same checks run in plain SQL."}
                </p>
              )}
            </div>
          </Section>
        </>
      )}
    </div>
  );
}
