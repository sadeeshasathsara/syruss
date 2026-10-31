"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Bar } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Field, Input, Textarea } from "@/components/ui/Field";
import { Pill } from "@/components/ui/Pill";
import { Select } from "@/components/ui/Select";
import { Dialog } from "@/components/ui/Sheet";
import { api } from "@/lib/api";
import { cn, dateTime, fileSize } from "@/lib/format";
import type { Plan, WorkspaceSettingsData } from "@/lib/types";
import { useWorkspace } from "@/lib/workspace";
import { NoAccess, SavedNote, Section, SettingsHeader, useWorkspaceSettings } from "./kit";

const DATE_FORMATS: WorkspaceSettingsData["general"]["date_format"][] = ["YYYY-MM-DD", "DD/MM/YYYY", "MM/DD/YYYY"];

function timeZones(): string[] {
  try {
    return Intl.supportedValuesOf("timeZone");
  } catch {
    return ["UTC"];
  }
}

export function ProfileSettings() {
  const { workspace, org } = useWorkspace();
  const queryClient = useQueryClient();
  const { settings, access, save, can } = useWorkspaceSettings();
  const [name, setName] = useState(workspace.name);
  const [description, setDescription] = useState(workspace.description ?? "");
  const [copied, setCopied] = useState(false);
  const editable = can("workspace.manage");
  const rename = useMutation({
    mutationFn: () => api.workspaces.update(workspace.slug, { name: name.trim(), description: description.trim() }),
    onSuccess: () => ["workspace", "workspaces"].forEach((k) => queryClient.invalidateQueries({ queryKey: [k] })),
  });
  const dirty = name.trim() !== workspace.name || description.trim() !== (workspace.description ?? "");
  const address = typeof window === "undefined" ? `/${org}/w/${workspace.slug}` : `${window.location.origin}/${org}/w/${workspace.slug}`;

  return (
    <div className="flex flex-col">
      <SettingsHeader title="Profile" description="How this workspace appears to your team, and the formats it uses." />
      {!editable && access && <div className="pt-5"><NoAccess what="change the profile" role={access.role_label} /></div>}

      <Section title="Workspace" description="Its name and what it's for. The agent reads the description too.">
        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (dirty && name.trim()) rename.mutate();
          }}
        >
          <Field label="Name">{(id) => <Input id={id} value={name} onChange={(e) => setName(e.target.value)} required maxLength={120} disabled={!editable} />}</Field>
          <Field label="Description">
            {(id) => <Textarea id={id} value={description} onChange={(e) => setDescription(e.target.value)} maxLength={500} disabled={!editable} />}
          </Field>
          <div className="flex items-center gap-3">
            <Button type="submit" variant="primary" disabled={!editable || !dirty || !name.trim() || rename.isPending}>Save changes</Button>
            <SavedNote pending={rename.isPending} error={rename.error} saved={rename.isSuccess && !dirty} />
          </div>
        </form>
      </Section>

      <Section title="Address" description="Share it with people who are members of this workspace.">
        <div className="flex min-w-0 items-center gap-2">
          <code className="min-w-0 flex-1 truncate rounded-control bg-raised px-3 py-2 font-mono text-meta text-ink">{address}</code>
          <Button
            variant="secondary"
            onClick={async () => {
              await navigator.clipboard.writeText(address);
              setCopied(true);
            }}
          >
            {copied ? <Check /> : <Copy />} {copied ? "Copied" : "Copy"}
          </Button>
        </div>
      </Section>

      <Section title="Regional" description="Used for dates and times in this workspace and in what the agent writes.">
        {!settings ? (
          <Bar className="h-9 w-full" />
        ) : (
          <div className="grid grid-cols-1 gap-4 @2xl:grid-cols-2">
            <Field label="Time zone">
              {(id) => (
                <Select
                  id={id}
                  disabled={!editable}
                  value={settings.general.timezone}
                  onChange={(timezone) => save.mutate({ general: { timezone } })}
                  options={timeZones().map((z) => ({ value: z, label: z.replaceAll("_", " ") }))}
                />
              )}
            </Field>
            <Field label="Date format">
              {(id) => (
                <Select
                  id={id}
                  disabled={!editable}
                  value={settings.general.date_format}
                  onChange={(date_format) => save.mutate({ general: { date_format } })}
                  options={DATE_FORMATS.map((f) => ({ value: f, label: f }))}
                />
              )}
            </Field>
            <div className="@2xl:col-span-2">
              <SavedNote pending={save.isPending} error={save.error} saved={save.isSuccess} />
            </div>
          </div>
        )}
      </Section>
    </div>
  );
}

/** A limit as a bar at its true ratio, with both ends labelled. */
function Meter({ label, used, limit, format = (n: number) => n.toLocaleString() }: { label: string; used: number; limit: number; format?: (n: number) => string }) {
  const ratio = Math.min(1, limit ? used / limit : 0);
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-body text-ink">{label}</span>
        <span className="num text-body text-muted">
          <span className={cn(ratio >= 1 ? "text-negative" : "text-ink")}>{format(used)}</span> of {format(limit)}
        </span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-active">
        <div
          className={cn("h-full rounded-full transition-[width] duration-500", ratio >= 1 ? "bg-negative" : ratio >= 0.8 ? "bg-[var(--pill-warning-ink)]" : "bg-accent")}
          style={{ width: `${ratio * 100}%` }}
        />
      </div>
    </div>
  );
}

export function BillingSettings() {
  const { workspace } = useWorkspace();
  const { access } = useWorkspaceSettings();
  const billing = useQuery({ queryKey: ["billing", workspace.id], queryFn: () => api.settings.billing(workspace.id) });
  const [comparing, setComparing] = useState(false);
  const b = billing.data;
  const plan: Plan | undefined = b ? b.plans[b.plan] : undefined;

  return (
    <div className="flex flex-col">
      <SettingsHeader title="Plan and billing" description="What this workspace's plan allows, what it has used this month, and how it's paid for." />
      {!b || !plan ? (
        <div className="flex flex-col gap-3 pt-6">{[0, 1, 2].map((i) => <Bar key={i} className="h-10 w-full" />)}</div>
      ) : (
        <>
          <Section title="Current plan" description={`Usage resets on the first of each month. This month started ${dateTime(b.usage.period_start)}.`}>
            <div className="flex flex-col gap-5 rounded-card border border-border bg-card p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div>
                  <p className="flex items-center gap-2 text-section font-semibold text-ink">{plan.name} <Pill tone="accent">Current</Pill></p>
                  <p className="num text-body text-muted">{plan.price_per_month ? `$${plan.price_per_month} per month` : "Free"}</p>
                </div>
                <Button variant="secondary" onClick={() => setComparing(true)}>Compare plans</Button>
              </div>
              <Meter label="Documents this month" used={b.usage.documents_this_month} limit={plan.documents_per_month} />
              <Meter label="Members" used={b.usage.members} limit={plan.members} />
              <Meter label="API keys" used={b.usage.api_keys} limit={plan.api_keys} />
            </div>
          </Section>

          <Section title="Usage" description="Counted from this workspace's own records.">
            <dl className="grid grid-cols-2 gap-px overflow-hidden rounded-card border border-border bg-border @2xl:grid-cols-3">
              {[
                ["Documents, all time", b.usage.documents_total.toLocaleString()],
                ["Agent runs this month", b.usage.agent_runs_this_month.toLocaleString()],
                ["Stored files", fileSize(b.usage.storage_bytes)],
                ["Known people and things", b.usage.entities.toLocaleString()],
                [
                  "Agent tokens this month",
                  (b.usage.agent_input_tokens_this_month + b.usage.agent_output_tokens_this_month).toLocaleString(),
                ],
                [
                  "Agent cost this month",
                  b.usage.agent_cost_usd_this_month == null ? "Price not set" : `$${b.usage.agent_cost_usd_this_month.toFixed(2)}`,
                ],
              ].map(([k, v]) => (
                <div key={k} className="flex flex-col gap-1 bg-card px-4 py-3">
                  <dt className="text-meta text-muted">{k}</dt>
                  <dd className="num text-section font-semibold text-ink">{v}</dd>
                </div>
              ))}
            </dl>
          </Section>

          <Section title="Payment method" description="Charged at the start of each month for paid plans.">
            <p className="rounded-card border border-dashed border-control px-4 py-3 text-body text-muted">
              No payment method on file. {b.payments_enabled ? "" : "Online payments aren't set up for this workspace yet."}
            </p>
            {!access?.permissions.includes("billing.manage") && access && (
              <p className="mt-2 text-meta text-muted">Only an owner can manage billing.</p>
            )}
          </Section>

          <Section title="Invoices">
            <p className="text-body text-muted">No invoices yet.</p>
          </Section>
        </>
      )}

      <Dialog open={comparing} onOpenChange={setComparing} title="Plans" description="Limits are per workspace.">
        {b && (
          <div className="flex flex-col gap-4">
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-body">
                <thead>
                  <tr className="text-left text-meta text-muted">
                    <th className="py-2 pr-3 font-normal" />
                    {Object.entries(b.plans).map(([k, p]) => <th key={k} className="py-2 pr-3 font-medium text-ink">{p.name}{k === b.plan ? " (current)" : ""}</th>)}
                  </tr>
                </thead>
                <tbody className="num">
                  {([
                    ["Price per month", (p: Plan) => (p.price_per_month ? `$${p.price_per_month}` : "Free")],
                    ["Documents a month", (p: Plan) => p.documents_per_month.toLocaleString()],
                    ["Members", (p: Plan) => p.members.toLocaleString()],
                    ["API keys", (p: Plan) => p.api_keys.toLocaleString()],
                    ["Webhooks", (p: Plan) => p.webhooks.toLocaleString()],
                  ] as const).map(([label, value]) => (
                    <tr key={label} className="border-t border-rule">
                      <td className="py-2 pr-3 text-muted">{label}</td>
                      {Object.entries(b.plans).map(([k, p]) => <td key={k} className="py-2 pr-3 text-ink">{value(p)}</td>)}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="text-body text-muted">Changing plans needs online payments, which aren’t set up yet. Contact your account manager to change plans.</p>
            <div className="flex justify-end">
              <Button variant="secondary" onClick={() => setComparing(false)}>Close</Button>
            </div>
          </div>
        )}
      </Dialog>
    </div>
  );
}

export function DangerZone() {
  const { workspace, org } = useWorkspace();
  const router = useRouter();
  const queryClient = useQueryClient();
  const { access, can } = useWorkspaceSettings();
  const [confirming, setConfirming] = useState(false);
  const [typed, setTyped] = useState("");
  const remove = useMutation({
    mutationFn: () => api.workspaces.removeOwn(workspace.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["workspaces"] });
      router.replace(`/${org}/workspaces`);
    },
  });

  return (
    <div className="flex flex-col">
      <SettingsHeader title="Danger zone" description="Actions that can't be undone." />
      <Section title="Delete this workspace" description="Deletes its documents, records, knowledge, data sources, integrations and the agent's work in it.">
        {can("workspace.delete") ? (
          <Button variant="danger" onClick={() => setConfirming(true)}>Delete {workspace.name}</Button>
        ) : (
          access && <NoAccess what="delete the workspace" role={access.role_label} />
        )}
      </Section>

      <Dialog
        open={confirming}
        onOpenChange={(o) => {
          setConfirming(o);
          setTyped("");
        }}
        title={`Delete ${workspace.name}?`}
        description="Everything in this workspace is deleted for good. Type the workspace name to confirm."
      >
        <form
          className="flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (typed === workspace.name) remove.mutate();
          }}
        >
          <Input value={typed} onChange={(e) => setTyped(e.target.value)} aria-label="Workspace name" autoFocus />
          {remove.error && <p className="text-body text-negative">{remove.error.message}</p>}
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setConfirming(false)}>Keep it</Button>
            <Button type="submit" variant="danger" disabled={typed !== workspace.name || remove.isPending}>Delete workspace</Button>
          </div>
        </form>
      </Dialog>
    </div>
  );
}
