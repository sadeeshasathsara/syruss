"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { Bar, Empty } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Field, Input } from "@/components/ui/Field";
import { Pill } from "@/components/ui/Pill";
import { Select } from "@/components/ui/Select";
import { Dialog } from "@/components/ui/Sheet";
import { Switch } from "@/components/ui/Switch";
import { api } from "@/lib/api";
import { ago, dateTime, humanize } from "@/lib/format";
import type { WebhookInfo } from "@/lib/types";
import { useWorkspace } from "@/lib/workspace";
import { NoAccess, Section, SettingsHeader, ShowOnce, useWorkspaceSettings } from "./kit";

const EXPIRY = [
  { value: "never", label: "Never" },
  { value: "30", label: "In 30 days" },
  { value: "90", label: "In 90 days" },
  { value: "365", label: "In a year" },
] as const;

function Locked({ title, description }: { title: string; description: string }) {
  const { access } = useWorkspaceSettings();
  return (
    <div className="flex flex-col">
      <SettingsHeader title={title} description={description} />
      <div className="pt-5"><NoAccess what="manage integrations" role={access?.role_label} /></div>
    </div>
  );
}

export function ApiKeySettings() {
  const { workspace, href } = useWorkspace();
  const queryClient = useQueryClient();
  const { access, can } = useWorkspaceSettings();
  const allowed = can("integrations.manage");
  const keys = useQuery({ queryKey: ["api-keys", workspace.id], queryFn: () => api.settings.apiKeys(workspace.id), enabled: allowed });
  const [name, setName] = useState("");
  const [scope, setScope] = useState<"read" | "write">("read");
  const [expiry, setExpiry] = useState<(typeof EXPIRY)[number]["value"]>("90");
  const [created, setCreated] = useState<string | null>(null);
  const refresh = () => ["api-keys", "billing"].forEach((k) => queryClient.invalidateQueries({ queryKey: [k, workspace.id] }));
  const create = useMutation({
    mutationFn: () => api.settings.createApiKey(workspace.id, { name: name.trim(), scope, expires_in_days: expiry === "never" ? null : Number(expiry) }),
    onSuccess: (res) => {
      setName("");
      setCreated(res.key);
      refresh();
    },
  });
  const revoke = useMutation({ mutationFn: (id: string) => api.settings.revokeApiKey(workspace.id, id), onSuccess: refresh });

  const title = "API keys";
  const description = "Let your own systems read records in this workspace, or work in it as an editor.";
  if (access && !allowed) return <Locked title={title} description={description} />;

  return (
    <div className="flex flex-col">
      <SettingsHeader title={title} description={description} />
      <p className="border-b border-border py-4 text-body text-muted">
        To let a system send documents in, make a channel with its own key in <Link href={href("connections/receive")} className="font-medium text-ink underline underline-offset-2">Connections</Link>.
        Those keys can only send documents and look up their own.
      </p>
      <Section title="Create a key" description="Read keys see everything; write keys can also upload and review. Send it as Authorization: Bearer <key> with X-Template-ID.">
        {created ? (
          <ShowOnce label="Your new API key" value={created} onDone={() => setCreated(null)} />
        ) : (
          <form
            className="grid grid-cols-1 gap-4 @2xl:grid-cols-[minmax(0,1fr)_140px_150px_auto] @2xl:items-end"
            onSubmit={(e) => {
              e.preventDefault();
              if (name.trim()) create.mutate();
            }}
          >
            <Field label="Name">{(id) => <Input id={id} value={name} onChange={(e) => setName(e.target.value)} placeholder="Payroll export" required maxLength={120} />}</Field>
            <Field label="Access">
              {(id) => <Select id={id} value={scope} onChange={setScope} options={[{ value: "read", label: "Read" }, { value: "write", label: "Read and write" }]} />}
            </Field>
            <Field label="Expires">{(id) => <Select id={id} value={expiry} onChange={setExpiry} options={[...EXPIRY]} />}</Field>
            <Button type="submit" variant="primary" disabled={!name.trim() || create.isPending}>Create key</Button>
          </form>
        )}
        {create.error && <p className="mt-2 text-body text-negative">{create.error.message}</p>}
      </Section>

      <Section title="Keys">
        {keys.isLoading ? (
          <Bar className="h-12 w-full" />
        ) : (keys.data ?? []).length === 0 ? (
          <Empty title="No API keys" />
        ) : (
          <ul className="flex flex-col rounded-card border border-border bg-card">
            {keys.data!.map((k) => {
              const expired = !!k.expires_at && new Date(k.expires_at) < new Date();
              return (
                <li key={k.id} className="flex flex-wrap items-center gap-3 border-b border-rule px-4 py-3 last:border-0">
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-2 text-body font-medium text-ink">
                      {k.name}
                      <code className="font-mono text-meta font-normal text-muted">{k.prefix}…</code>
                    </span>
                    <span className="block text-meta text-muted">
                      {k.scope === "write" ? "Read and write" : "Read"}, created by {k.created_by} {ago(k.created_at).toLowerCase()},{" "}
                      {k.last_used_at ? `last used ${ago(k.last_used_at).toLowerCase()}` : "never used"}
                      {k.expires_at && !k.revoked_at ? `, ${expired ? "expired" : "expires"} ${dateTime(k.expires_at)}` : ""}
                    </span>
                  </span>
                  {k.revoked_at ? <Pill tone="neutral">Revoked</Pill> : expired ? <Pill tone="warning">Expired</Pill> : (
                    <Button size="sm" variant="ghost" disabled={revoke.isPending} onClick={() => revoke.mutate(k.id)}>Revoke</Button>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </Section>
    </div>
  );
}

function WebhookRow({ hook, onChanged }: { hook: WebhookInfo; onChanged: () => void }) {
  const { workspace } = useWorkspace();
  const [secret, setSecret] = useState<string | null>(null);
  const [showLog, setShowLog] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const deliveries = useQuery({ queryKey: ["deliveries", hook.id], queryFn: () => api.settings.deliveries(workspace.id, hook.id), enabled: showLog });
  const toggle = useMutation({ mutationFn: (active: boolean) => api.settings.updateWebhook(workspace.id, hook.id, { active }), onSuccess: onChanged });
  const test = useMutation({ mutationFn: () => api.settings.testWebhook(workspace.id, hook.id), onSuccess: () => { onChanged(); deliveries.refetch(); } });
  const rotate = useMutation({ mutationFn: () => api.settings.rotateWebhookSecret(workspace.id, hook.id), onSuccess: (r) => setSecret(r.secret) });
  const remove = useMutation({ mutationFn: () => api.settings.deleteWebhook(workspace.id, hook.id), onSuccess: onChanged });
  const last = hook.last_delivery;

  return (
    <li className="flex flex-col gap-3 border-b border-rule px-4 py-3 last:border-0">
      <div className="flex flex-wrap items-center gap-3">
        <Switch label={`${hook.active ? "Pause" : "Resume"} ${hook.url}`} checked={hook.active} onChange={(v) => toggle.mutate(v)} />
        <span className="min-w-0 flex-1">
          <span className="block truncate font-mono text-meta text-ink">{hook.url}</span>
          <span className="block text-meta text-muted">
            {hook.events.includes("*") ? "All events" : hook.events.map(humanize).join(", ")}
            {last ? `, last delivery ${last.ok ? "succeeded" : "failed"} ${ago(last.at).toLowerCase()}` : ", nothing delivered yet"}
          </span>
        </span>
        {last && !last.ok && <Pill tone="negative">Failing</Pill>}
        <Button size="sm" variant="secondary" disabled={test.isPending} onClick={() => test.mutate()}>{test.isPending ? "Sending…" : "Send a test"}</Button>
        <Button size="sm" variant="ghost" onClick={() => setShowLog((s) => !s)}>{showLog ? "Hide deliveries" : "Deliveries"}</Button>
        <Button size="sm" variant="ghost" onClick={() => rotate.mutate()}>New secret</Button>
        <Button size="sm" variant="ghost" onClick={() => setConfirmDelete(true)}>Delete</Button>
      </div>
      {test.data && (
        <p className={`text-body ${test.data.ok ? "text-positive" : "text-negative"}`}>
          {test.data.ok ? `Delivered (HTTP ${test.data.status_code}) in ${Math.round(test.data.duration_ms)} ms.` : test.data.error}
        </p>
      )}
      {secret && <ShowOnce label="The new signing secret (the old one stopped working)" value={secret} onDone={() => setSecret(null)} />}
      <div className="collapsible" data-open={showLog}>
        <div>
          <ul className="flex flex-col rounded-control bg-raised px-3 py-2">
            {(deliveries.data ?? []).length === 0 && <li className="py-1 text-body text-muted">No deliveries yet.</li>}
            {(deliveries.data ?? []).map((d) => (
              <li key={d.id} className="flex flex-wrap items-center gap-3 py-1 text-body">
                <Pill tone={d.ok ? "positive" : "negative"}>{d.ok ? d.status_code ?? "OK" : d.status_code ?? "Failed"}</Pill>
                <span className="min-w-0 flex-1 truncate text-ink">{d.event_type}{d.error ? `: ${d.error}` : ""}</span>
                <span className="num text-meta text-muted">{d.duration_ms != null ? `${Math.round(d.duration_ms)} ms, ` : ""}{dateTime(d.at)}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
      <Dialog open={confirmDelete} onOpenChange={setConfirmDelete} title="Delete this webhook?" description={`Events stop going to ${hook.url}.`}>
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setConfirmDelete(false)}>Keep it</Button>
          <Button variant="danger" disabled={remove.isPending} onClick={() => remove.mutate()}>Delete</Button>
        </div>
      </Dialog>
    </li>
  );
}

export function WebhookSettings() {
  const { workspace } = useWorkspace();
  const queryClient = useQueryClient();
  const { access, can } = useWorkspaceSettings();
  const allowed = can("integrations.manage");
  const hooks = useQuery({ queryKey: ["webhooks", workspace.id], queryFn: () => api.settings.webhooks(workspace.id), enabled: allowed });
  const [url, setUrl] = useState("");
  const [events, setEvents] = useState<string[]>(["*"]);
  const [secret, setSecret] = useState<string | null>(null);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["webhooks", workspace.id] });
  const create = useMutation({
    mutationFn: () => api.settings.createWebhook(workspace.id, { url: url.trim(), description: "", events }),
    onSuccess: (res) => {
      setUrl("");
      setEvents(["*"]);
      setSecret(res.secret);
      refresh();
    },
  });

  const title = "Webhooks";
  const description = "Send this workspace's events to your own systems as they happen, signed so you can trust them.";
  if (access && !allowed) return <Locked title={title} description={description} />;
  const all = events.includes("*");

  return (
    <div className="flex flex-col">
      <SettingsHeader title={title} description={description} />
      <Section
        title="Add an endpoint"
        description="Each POST carries X-Webhook-Signature: sha256 HMAC of “timestamp.body” with your secret, and X-Webhook-Timestamp."
      >
        {secret ? (
          <ShowOnce label="The signing secret for your endpoint" value={secret} onDone={() => setSecret(null)} />
        ) : (
          <form
            className="flex flex-col gap-4"
            onSubmit={(e) => {
              e.preventDefault();
              if (url.trim()) create.mutate();
            }}
          >
            <Field label="Endpoint URL">{(id) => <Input id={id} type="url" required value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://example.com/hooks/syruss" />}</Field>
            <fieldset>
              <legend className="text-body font-medium text-ink">Events</legend>
              <div className="mt-2 flex flex-col gap-2">
                <label className="flex items-center gap-2 text-body text-ink">
                  <input type="checkbox" className="size-4 accent-[var(--ink)]" checked={all} onChange={(e) => setEvents(e.target.checked ? ["*"] : [])} />
                  All events, including ones added later
                </label>
                {!all &&
                  (hooks.data?.events ?? []).map((ev) => (
                    <label key={ev.type} className="flex items-center gap-2 pl-6 text-body text-ink">
                      <input
                        type="checkbox"
                        className="size-4 accent-[var(--ink)]"
                        checked={events.includes(ev.type)}
                        onChange={(e) => setEvents(e.target.checked ? [...events, ev.type] : events.filter((x) => x !== ev.type))}
                      />
                      {ev.label} <code className="font-mono text-meta text-muted">{ev.type}</code>
                    </label>
                  ))}
              </div>
            </fieldset>
            <div>
              <Button type="submit" variant="primary" disabled={!url.trim() || events.length === 0 || create.isPending}>Add endpoint</Button>
            </div>
          </form>
        )}
        {create.error && <p className="mt-2 text-body text-negative">{create.error.message}</p>}
      </Section>

      <Section title="Endpoints">
        {hooks.isLoading ? (
          <Bar className="h-12 w-full" />
        ) : (hooks.data?.webhooks ?? []).length === 0 ? (
          <Empty title="No endpoints" />
        ) : (
          <ul className="flex flex-col rounded-card border border-border bg-card">
            {hooks.data!.webhooks.map((h) => <WebhookRow key={h.id} hook={h} onChanged={refresh} />)}
          </ul>
        )}
      </Section>
    </div>
  );
}
