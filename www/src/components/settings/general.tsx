"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { orgSettingsHref } from "@/components/org/sections";
import { Bar } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Field, Input, Textarea } from "@/components/ui/Field";
import { Meter } from "@/components/ui/Figures";
import { Pill } from "@/components/ui/Pill";
import { Select } from "@/components/ui/Select";
import { Dialog } from "@/components/ui/Sheet";
import { useMe } from "@/lib/access";
import { api } from "@/lib/api";
import { dateTime, fileSize } from "@/lib/format";
import type { WorkspaceSettingsData } from "@/lib/types";
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

/** The plan is the organization's: this workspace's share of its pages, and the limits each workspace has. */
export function BillingSettings() {
  const { workspace, org } = useWorkspace();
  const { canOrg } = useMe();
  const billing = useQuery({ queryKey: ["billing", workspace.id], queryFn: () => api.settings.billing(workspace.id) });
  const b = billing.data;

  return (
    <div className="flex flex-col">
      <SettingsHeader title="Plan and usage" description="The organization's plan, shared by all its workspaces, and what this workspace has used."
        actions={canOrg("billing.manage") ? <Button variant="secondary" asChild><Link href={orgSettingsHref(org, "billing")}>Organization billing</Link></Button> : undefined} />
      {!b ? (
        <div className="flex flex-col gap-3 pt-6">{[0, 1, 2].map((i) => <Bar key={i} className="h-10 w-full" />)}</div>
      ) : (
        <>
          <Section title="Plan" description={`Pages renew on ${new Date(b.pages.period_end).toLocaleDateString(undefined, { day: "numeric", month: "long" })}. This period started ${dateTime(b.pages.period_start)}.`}>
            <div className="flex flex-col gap-5 rounded-card border border-border bg-card p-4">
              <p className="flex items-center gap-2 text-section font-semibold text-ink">{b.pages.plan} <Pill tone="accent">The organization&apos;s plan</Pill></p>
              <Meter label="Pages read this period, all workspaces" used={b.pages.pages_used} limit={b.pages.pages_limit} />
              <p className="text-body text-muted">
                This workspace read {b.workspace_pages.toLocaleString()} of them.
                {b.pages.credits > 0 && ` ${b.pages.credits.toLocaleString()} prepaid pages are left for when the plan's run out.`}
              </p>
              <Meter label="Members in this workspace" used={b.usage.members} limit={b.limits.members_per_workspace ?? null} />
              <Meter label="API keys in this workspace" used={b.usage.api_keys} limit={b.limits.api_keys_per_workspace ?? null} />
            </div>
            {!canOrg("billing.manage") && <p className="mt-2 text-meta text-muted">The organization&apos;s owners manage the plan and billing.</p>}
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
        </>
      )}
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
