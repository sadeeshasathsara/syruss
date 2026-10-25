"use client";

import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Plus } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { NoAccess, Section, SettingsHeader } from "@/components/settings/kit";
import { Bar, Empty, ErrorNote } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { CodeBlock } from "@/components/ui/CodeBlock";
import { CopyButton } from "@/components/ui/CopyButton";
import { Field, Input } from "@/components/ui/Field";
import { Pill } from "@/components/ui/Pill";
import { Select } from "@/components/ui/Select";
import { Dialog } from "@/components/ui/Sheet";
import { ShowOnce } from "@/components/ui/ShowOnce";
import { Switch } from "@/components/ui/Switch";
import { useAccess } from "@/lib/access";
import { api } from "@/lib/api";
import { ago, cn, dateTime, fileSize } from "@/lib/format";
import type { IntakeChannelInfo, IntakeEventInfo, IntakeKeyInfo } from "@/lib/types";
import { useWorkspace } from "@/lib/workspace";
import { ALL_TYPES, channelHealth, KEY_STATE, KIND_LABEL, TYPE_LABEL } from "./health";
import { NewKeyDialog } from "./NewKey";

function useChannels() {
  const { workspace } = useWorkspace();
  const { can } = useAccess(workspace.id);
  const allowed = can("integrations.manage");
  const channels = useQuery({ queryKey: ["channels", workspace.id], queryFn: () => api.channels.list(workspace.id), enabled: allowed });
  return { workspace, allowed, channels };
}

// ---------------------------------------------------------------------------------------------------
// The list of channels
// ---------------------------------------------------------------------------------------------------

function NewChannelDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const { workspace, href } = useWorkspace();
  const queryClient = useQueryClient();
  const router = useRouter();
  const [name, setName] = useState("");
  const [kind, setKind] = useState<"api" | "email" | "sftp">("api");
  const info = useQuery({ queryKey: ["channels", workspace.id, "developer-info"], queryFn: () => api.channels.developerInfo(workspace.id), enabled: open });
  const create = useMutation({
    mutationFn: () => api.channels.create(workspace.id, { name: name.trim(), kind }),
    onSuccess: (channel) => {
      queryClient.invalidateQueries({ queryKey: ["channels", workspace.id] });
      queryClient.invalidateQueries({ queryKey: ["billing", workspace.id] });
      onOpenChange(false);
      setName("");
      // The SFTP password is shown once, on the channel's page.
      if (channel.sftp_password) sessionStorage.setItem(`sftp-password:${channel.id}`, channel.sftp_password);
      router.push(href(`connections/receive/${channel.slug}`));
    },
  });
  const kinds: { value: "api" | "email" | "sftp"; title: string; help: string; ready: boolean }[] = [
    { value: "api", title: "API", help: "Another system sends files to a web address with a key.", ready: true },
    { value: "email", title: "Email", help: "People or systems email documents to an address of its own.", ready: info.data?.email.enabled ?? false },
    { value: "sftp", title: "SFTP", help: "Scanners and older systems drop files on a server.", ready: info.data?.sftp.enabled ?? false },
  ];
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => { onOpenChange(next); if (!next) create.reset(); }}
      title="New channel"
      description="A channel is one way documents come in, such as “Supplier invoices” or “Warehouse scanners”. It has its own keys, rules and activity."
    >
      <form className="flex flex-col gap-4" onSubmit={(e) => { e.preventDefault(); if (name.trim()) create.mutate(); }}>
        <Field label="Name">{(id) => <Input id={id} value={name} onChange={(e) => setName(e.target.value)} placeholder="Supplier invoices" required maxLength={120} autoFocus />}</Field>
        <div role="radiogroup" aria-label="How documents arrive" className="flex flex-col gap-2">
          {kinds.map((k) => (
            <label key={k.value} className={cn("flex items-start gap-3 rounded-card border border-border bg-card p-3 has-[:checked]:border-control", !k.ready && "opacity-60")}>
              <input type="radio" name="channel-kind" className="mt-1 size-4 accent-[var(--ink)]" disabled={!k.ready} checked={kind === k.value} onChange={() => setKind(k.value)} />
              <span><span className="block text-body font-medium text-ink">{k.title}</span>
                <span className="block text-body text-muted">{k.ready ? k.help : `${k.help} Not switched on for this server yet.`}</span></span>
            </label>
          ))}
        </div>
        {create.error && <p className="text-body text-negative">{create.error.message}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button type="submit" variant="primary" disabled={!name.trim() || create.isPending}>{create.isPending ? "Creating…" : "Create channel"}</Button>
        </div>
      </form>
    </Dialog>
  );
}

export function ReceivePage() {
  const { href } = useWorkspace();
  const { allowed, channels } = useChannels();
  const [creating, setCreating] = useState(false);
  const title = "Receive";
  const description = "How documents get into this workspace from your other systems. Each channel knows which workspace its documents belong to.";

  if (!allowed) return <div className="flex flex-col"><SettingsHeader title={title} description={description} /><div className="pt-5"><NoAccess what="manage connections" /></div></div>;

  return (
    <div className="flex flex-col">
      <SettingsHeader title={title} description={description} actions={<Button variant="primary" onClick={() => setCreating(true)}><Plus /> New channel</Button>} />
      <div className="flex flex-col gap-3 pt-5">
        {channels.isLoading && [0, 1].map((i) => <Bar key={i} className="h-24 w-full" />)}
        {channels.isError && <ErrorNote message="Couldn’t load the channels." onRetry={() => channels.refetch()} />}
        {channels.data?.length === 0 && (
          <Empty
            title="Nothing is coming in yet"
            action={<Button variant="primary" onClick={() => setCreating(true)}><Plus /> Create the first channel</Button>}
          >
            Create a channel, give the system that sends documents a key, and its files arrive here to be read. Every document is tied to its channel
            and sender, so you always know where it came from.
          </Empty>
        )}
        <ul className="flex flex-col gap-3">
          {(channels.data ?? []).map((c) => {
            const health = channelHealth(c);
            return (
              <li key={c.id}>
                <Link
                  href={href(`connections/receive/${c.slug}`)}
                  className="group flex flex-col gap-3 rounded-card border border-border bg-card [box-shadow:var(--shadow-card)] p-4 transition-colors hover:border-control @2xl:flex-row @2xl:items-center"
                >
                  <span className="min-w-0 flex-1">
                    <span className="flex flex-wrap items-center gap-2">
                      <span className="text-section font-semibold text-ink">{c.name}</span>
                      <Pill>{KIND_LABEL[c.kind]}</Pill>
                      <Pill tone={health.tone}>{health.label}</Pill>
                    </span>
                    <span className="mt-1 block truncate font-mono text-meta text-muted">{where(c)}</span>
                  </span>
                  <dl className="grid flex-none grid-cols-3 gap-x-6 text-meta">
                    <Stat label="Received today" value={c.stats.accepted_24h} />
                    <Stat label="Refused today" value={c.stats.rejected_24h} tone={c.stats.rejected_24h ? "warn" : undefined} />
                    <div>
                      <dt className="text-muted">Last document</dt>
                      <dd className="mt-0.5 text-body text-ink">{c.stats.last_received_at ? ago(c.stats.last_received_at) : "None yet"}</dd>
                    </div>
                  </dl>
                  <ArrowRight className="hidden size-4 flex-none text-muted transition-transform group-hover:translate-x-0.5 @2xl:block" />
                </Link>
              </li>
            );
          })}
        </ul>
      </div>
      <NewChannelDialog open={creating} onOpenChange={setCreating} />
    </div>
  );
}

/** Where a channel's documents are sent, in one line. */
function where(c: IntakeChannelInfo): string {
  if (c.kind === "email") return c.address ?? "Email isn’t switched on for this server";
  if (c.kind === "sftp") return c.sftp ? `sftp://${c.sftp.username}@${c.sftp.host}:${c.sftp.port}` : "—";
  return c.endpoint ?? "—";
}

function Stat({ label, value, tone }: { label: string; value: number; tone?: "warn" }) {
  return (
    <div>
      <dt className="text-muted">{label}</dt>
      <dd className={cn("num mt-0.5 text-body font-medium", tone === "warn" ? "text-negative" : "text-ink")}>{value}</dd>
    </div>
  );
}

// ---------------------------------------------------------------------------------------------------
// One channel
// ---------------------------------------------------------------------------------------------------

function sampleFor(c: IntakeChannelInfo) {
  const url = c.endpoint ?? "https://api.example.com/api/v1/intake/documents";
  return [
    { label: "curl", code: `curl -X POST "${url}" \\\n  -H "Authorization: Bearer $SYRUSS_KEY" \\\n  -H "Idempotency-Key: invoice-1001" \\\n  -F "file=@invoice.png" \\\n  -F "external_id=INV-1001"` },
    { label: "Python", code: `import os, requests\n\nres = requests.post(\n    "${url}",\n    headers={"Authorization": f"Bearer {os.environ['SYRUSS_KEY']}", "Idempotency-Key": "invoice-1001"},\n    files={"file": open("invoice.png", "rb")},\n    data={"external_id": "INV-1001"},\n)\nprint(res.status_code, res.json())` },
    { label: "Node", code: `const form = new FormData();\nform.append("file", new Blob([await fs.readFile("invoice.png")], { type: "image/png" }), "invoice.png");\nform.append("external_id", "INV-1001");\n\nconst res = await fetch("${url}", {\n  method: "POST",\n  headers: { Authorization: \`Bearer \${process.env.SYRUSS_KEY}\`, "Idempotency-Key": "invoice-1001" },\n  body: form,\n});\nconsole.log(res.status, await res.json());` },
  ];
}

function KeyRow({ channel, k }: { channel: IntakeChannelInfo; k: IntakeKeyInfo }) {
  const { workspace } = useWorkspace();
  const queryClient = useQueryClient();
  const [confirm, setConfirm] = useState<"revoke" | "rotate" | null>(null);
  const [overlap, setOverlap] = useState("24");
  const [secret, setSecret] = useState<string | null>(null);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["channels", workspace.id] });
  const revoke = useMutation({ mutationFn: () => api.channels.revokeKey(workspace.id, channel.id, k.id), onSuccess: () => { setConfirm(null); refresh(); } });
  const rotate = useMutation({
    mutationFn: () => api.channels.rotateKey(workspace.id, channel.id, k.id, Number(overlap)),
    onSuccess: (r) => { setConfirm(null); setSecret(r.secret); refresh(); },
  });
  const state = KEY_STATE[k.state];
  const live = k.state === "active" || k.state === "retiring";
  const limits = [`${k.rate_limit_per_minute} requests a minute`, k.daily_quota ? `${k.daily_quota} documents a day` : null,
    k.ip_allowlist.length ? `only from ${k.ip_allowlist.join(", ")}` : null].filter(Boolean).join(" · ");
  return (
    <li className="flex flex-col gap-3 border-b border-rule px-4 py-3 last:border-0">
      <div className="flex flex-wrap items-center gap-3">
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-2 text-body font-medium text-ink">
            {k.name}
            <code className="font-mono text-meta font-normal text-muted">{k.prefix}…</code>
            <Pill tone={k.mode === "test" ? "info" : "neutral"}>{k.mode === "test" ? "Test" : "Live"}</Pill>
            <Pill tone={state.tone}>{state.label}</Pill>
          </span>
          <span className="mt-0.5 block text-meta text-muted">
            {limits} · created by {k.created_by} {ago(k.created_at).toLowerCase()} · {k.last_used_at ? `last used ${ago(k.last_used_at).toLowerCase()}` : "never used"}
            {k.expires_at && live ? ` · ${k.state === "retiring" ? "stops" : "expires"} ${dateTime(k.expires_at)}` : ""}
          </span>
        </span>
        {live && (
          <>
            <Button size="sm" variant="ghost" onClick={() => setConfirm("rotate")}>Replace</Button>
            <Button size="sm" variant="ghost" onClick={() => setConfirm("revoke")}>Revoke</Button>
          </>
        )}
      </div>
      {secret && <ShowOnce label={`The new key for ${k.name}`} value={secret} onDone={() => setSecret(null)} />}
      <Dialog open={confirm === "revoke"} onOpenChange={(o) => !o && setConfirm(null)} title={`Revoke “${k.name}”?`} description="It stops working at once. Anything still using it will be refused until it gets a new key.">
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setConfirm(null)}>Keep it</Button>
          <Button variant="danger" disabled={revoke.isPending} onClick={() => revoke.mutate()}>Revoke</Button>
        </div>
      </Dialog>
      <Dialog open={confirm === "rotate"} onOpenChange={(o) => !o && setConfirm(null)} title={`Replace “${k.name}”?`} description="You get a new key with the same limits. Keep the old one working for a while so the sending system can be switched without a gap.">
        <div className="flex flex-col gap-4">
          <Field label="The old key keeps working for">
            {(id) => <Select id={id} value={overlap} onChange={setOverlap} options={[
              { value: "0", label: "Not at all: stop it now" }, { value: "1", label: "1 hour" }, { value: "24", label: "24 hours" }, { value: "168", label: "7 days" },
            ]} />}
          </Field>
          {rotate.error && <p className="text-body text-negative">{rotate.error.message}</p>}
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setConfirm(null)}>Cancel</Button>
            <Button variant="primary" disabled={rotate.isPending} onClick={() => rotate.mutate()}>Create the new key</Button>
          </div>
        </div>
      </Dialog>
    </li>
  );
}

function Rules({ channel, workspaceMax }: { channel: IntakeChannelInfo; workspaceMax?: number }) {
  const { workspace } = useWorkspace();
  const queryClient = useQueryClient();
  const initial = channel.config.allowed_types ?? ALL_TYPES;
  const [types, setTypes] = useState<string[]>(initial);
  const [maxMb, setMaxMb] = useState(channel.config.max_mb ? String(channel.config.max_mb) : "");
  const [process, setProcess] = useState<"workspace" | "yes" | "no">(channel.config.process == null ? "workspace" : channel.config.process ? "yes" : "no");
  const save = useMutation({
    mutationFn: () => api.channels.update(workspace.id, channel.id, { config: {
      allowed_types: types.length === ALL_TYPES.length ? null : types, max_mb: maxMb ? Number(maxMb) : null,
      process: process === "workspace" ? null : process === "yes",
    } }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["channels", workspace.id] }),
  });
  return (
    <form className="flex flex-col gap-4" onSubmit={(e) => { e.preventDefault(); if (types.length) save.mutate(); }}>
      <fieldset>
        <legend className="text-body font-medium text-ink">File types this channel accepts</legend>
        <div className="mt-2 flex flex-wrap gap-x-5 gap-y-2">
          {ALL_TYPES.map((t) => (
            <label key={t} className="flex items-center gap-2 text-body text-ink">
              <input type="checkbox" className="size-4 accent-[var(--ink)]" checked={types.includes(t)}
                onChange={(e) => setTypes(e.target.checked ? [...types, t] : types.filter((x) => x !== t))} />
              {TYPE_LABEL[t]}
            </label>
          ))}
        </div>
        <p className="mt-1.5 text-meta text-muted">A file’s type is read from the file itself, not from its name or what the sender says it is. A channel can only narrow what the workspace accepts.</p>
      </fieldset>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Field label="Largest file (MB)" help={`Empty: the workspace’s limit${workspaceMax ? ` (${workspaceMax} MB)` : ""}`}>
          {(id) => <Input id={id} inputMode="numeric" value={maxMb} onChange={(e) => setMaxMb(e.target.value.replace(/\D/g, ""))} placeholder="Workspace limit" />}
        </Field>
        <Field label="Start reading on arrival">
          {(id) => <Select id={id} value={process} onChange={setProcess} options={[
            { value: "workspace", label: "As the workspace is set" }, { value: "yes", label: "Always" }, { value: "no", label: "Never: only store them" },
          ]} />}
        </Field>
      </div>
      <div className="flex items-center gap-3">
        <Button type="submit" variant="primary" disabled={!types.length || save.isPending}>{save.isPending ? "Saving…" : "Save rules"}</Button>
        {save.isSuccess && !save.isPending && <span className="text-body text-muted">Saved</span>}
        {save.error && <span className="text-body text-negative">{save.error.message}</span>}
      </div>
    </form>
  );
}

function Activity({ channel }: { channel: IntakeChannelInfo }) {
  const { workspace, href } = useWorkspace();
  const [filter, setFilter] = useState<"all" | "rejected">("all");
  const page = useInfiniteQuery({
    queryKey: ["channels", workspace.id, "activity", channel.id, filter],
    queryFn: ({ pageParam }) => api.channels.activity(workspace.id, channel.id, { outcome: filter === "rejected" ? "rejected" : undefined, before: pageParam }),
    initialPageParam: null as number | null,
    getNextPageParam: (last) => last.next,
  });
  const events: IntakeEventInfo[] = page.data?.pages.flatMap((p) => p.items) ?? [];
  return (
    <div className="flex flex-col gap-3">
      <div role="tablist" aria-label="Show" className="flex gap-1">
        {([["all", "Everything"], ["rejected", "Refused only"]] as const).map(([value, label]) => (
          <button key={value} role="tab" type="button" aria-selected={filter === value} onClick={() => setFilter(value)}
            className={cn("h-7 rounded-control px-2.5 text-meta font-medium pointer-coarse:h-9", filter === value ? "bg-active text-ink" : "text-muted hover:text-ink")}>
            {label}
          </button>
        ))}
      </div>
      {page.isLoading ? <Bar className="h-14 w-full" /> : events.length === 0 ? (
        <Empty title={filter === "rejected" ? "Nothing has been refused" : "Nothing has been sent yet"}>
          {filter === "rejected" ? "Documents that don’t pass a check show here with the reason, so you can tell the sender what to fix." : "When a system sends a document, or tries to, it shows here with who sent it."}
        </Empty>
      ) : (
        <ul className="flex flex-col rounded-card border border-border bg-card">
          {events.map((e) => (
            <li key={e.id} className="flex flex-wrap items-start gap-x-3 gap-y-1 border-b border-rule px-4 py-2.5 last:border-0">
              <Pill tone={e.outcome === "accepted" ? "positive" : "negative"}>{e.outcome === "accepted" ? "Received" : "Refused"}</Pill>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-body text-ink">
                  {e.document_id ? <Link href={href(`documents/${e.document_id}`)} className="hover:underline">{e.filename || "Document"}</Link> : (e.filename || "No file")}
                  {e.external_id && <span className="ml-2 font-mono text-meta text-muted">{e.external_id}</span>}
                </span>
                {e.reason && <span className="block text-meta text-negative">{e.reason}</span>}
                <span className="block text-meta text-muted">{e.sender}{e.ip ? ` · ${e.ip}` : ""}{e.size_bytes ? ` · ${fileSize(e.size_bytes)}` : ""}</span>
              </span>
              <span className="num text-meta text-muted">{dateTime(e.at)}</span>
            </li>
          ))}
        </ul>
      )}
      {page.hasNextPage && <div><Button variant="secondary" disabled={page.isFetchingNextPage} onClick={() => page.fetchNextPage()}>{page.isFetchingNextPage ? "Loading…" : "Show older"}</Button></div>}
    </div>
  );
}

export function ChannelPage({ slug }: { slug: string }) {
  const { workspace, href } = useWorkspace();
  const queryClient = useQueryClient();
  const router = useRouter();
  const { allowed, channels } = useChannels();
  const info = useQuery({ queryKey: ["channels", workspace.id, "developer-info"], queryFn: () => api.channels.developerInfo(workspace.id), enabled: allowed });
  const channel = channels.data?.find((c) => c.slug === slug);
  const [newKey, setNewKey] = useState(false);
  const [removing, setRemoving] = useState(false);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["channels", workspace.id] });
  const toggle = useMutation({ mutationFn: (active: boolean) => api.channels.update(workspace.id, channel!.id, { status: active ? "active" : "paused" }), onSuccess: refresh });
  const test = useMutation({ mutationFn: () => api.channels.testDocument(workspace.id, channel!.id), onSuccess: refresh });
  const remove = useMutation({
    mutationFn: () => api.channels.remove(workspace.id, channel!.id),
    onSuccess: () => { refresh(); router.replace(href("connections/receive")); },
  });
  const [name, setName] = useState("");
  useEffect(() => { if (channel) setName(channel.name); }, [channel?.name]); // eslint-disable-line react-hooks/exhaustive-deps
  const rename = useMutation({ mutationFn: () => api.channels.update(workspace.id, channel!.id, { name: name.trim() }), onSuccess: refresh });

  const back = <Link href={href("connections/receive")} className="mb-1 inline-block text-body text-muted hover:text-ink">All channels</Link>;
  if (!allowed) return <div className="flex flex-col"><SettingsHeader title="Receive" description="" /><div className="pt-5"><NoAccess what="manage connections" /></div></div>;
  if (channels.isLoading) return <div className="flex flex-col gap-3"><Bar className="h-10 w-72" /><Bar className="h-40 w-full" /></div>;
  if (channels.isError) return <ErrorNote message="Couldn’t load this channel." onRetry={() => channels.refetch()} />;
  if (!channel) {
    return (
      <div className="flex flex-col gap-3">
        {back}
        <Empty title="No such channel">It may have been removed. <Link href={href("connections/receive")} className="underline">See all channels</Link>.</Empty>
      </div>
    );
  }
  const health = channelHealth(channel);
  const openKeys = channel.keys.filter((k) => k.state === "active" || k.state === "retiring");
  const oldKeys = channel.keys.filter((k) => !(k.state === "active" || k.state === "retiring"));

  return (
    <div className="flex flex-col">
      <header className="flex flex-col gap-3 border-b border-border pb-5 @3xl:flex-row @3xl:items-end @3xl:justify-between">
        <div className="min-w-0">
          {back}
          <h1 className="flex flex-wrap items-center gap-2 text-title font-semibold tracking-[-0.01em] text-ink">
            {channel.name} <Pill>{KIND_LABEL[channel.kind]}</Pill> <Pill tone={health.tone}>{health.label}</Pill>
          </h1>
          <p className="mt-1 text-body text-muted">
            {channel.stats.accepted_24h} received and {channel.stats.rejected_24h} refused in the last 24 hours
            {channel.stats.last_received_at ? `, last document ${ago(channel.stats.last_received_at).toLowerCase()}` : ""}.
          </p>
        </div>
        <label className="flex items-center gap-2 text-body text-ink">
          <Switch label={channel.status === "active" ? "Pause this channel" : "Resume this channel"} checked={channel.status === "active"} onChange={(v) => toggle.mutate(v)} />
          {channel.status === "active" ? "Accepting documents" : "Paused: documents are refused"}
        </label>
      </header>

      <Section title="Send a document" description="Other systems send files to this address with a key. The key, not the request, decides which workspace and channel a file belongs to.">
        <div className="flex flex-col gap-3">
          <div className="flex min-w-0 items-center gap-2">
            <code className="min-w-0 flex-1 truncate rounded-control bg-raised px-3 py-2 font-mono text-meta text-ink">POST {channel.endpoint}</code>
            <CopyButton value={channel.endpoint ?? ""} />
          </div>
          <CodeBlock samples={sampleFor(channel)} />
          <div className="flex flex-wrap items-center gap-3">
            <Button variant="secondary" disabled={test.isPending || channel.status !== "active"} onClick={() => test.mutate()}>
              {test.isPending ? "Sending…" : "Send a test document"}
            </Button>
            <Link href={href("connections/developers")} className="text-body text-muted underline-offset-2 hover:text-ink hover:underline">Full guide for developers</Link>
          </div>
          {test.isSuccess && <p className="text-body text-positive">Sent. It went through this channel like a real one, is marked as a test and is never published. See it in the activity below.</p>}
          {test.error && <p className="text-body text-negative">{test.error.message}</p>}
        </div>
      </Section>

      <Section title="Keys" description="One key for each system that sends. Revoke one and the others keep working.">
        <div className="flex flex-col gap-3">
          {openKeys.length === 0 && <Empty title="No key yet" action={<Button variant="primary" onClick={() => setNewKey(true)}><Plus /> Create a key</Button>}>Nothing can be sent until this channel has a key.</Empty>}
          {openKeys.length > 0 && (
            <ul className="flex flex-col rounded-card border border-border bg-card">{openKeys.map((k) => <KeyRow key={k.id} channel={channel} k={k} />)}</ul>
          )}
          {openKeys.length > 0 && <div><Button variant="secondary" onClick={() => setNewKey(true)}><Plus /> New key</Button></div>}
          {oldKeys.length > 0 && (
            <details className="rounded-card border border-border bg-card px-4 py-2.5">
              <summary className="cursor-pointer text-body text-muted">{oldKeys.length} revoked or expired</summary>
              <ul className="mt-2 flex flex-col">{oldKeys.map((k) => <KeyRow key={k.id} channel={channel} k={k} />)}</ul>
            </details>
          )}
        </div>
      </Section>

      <Section title="Rules" description="What this channel lets in, on top of the workspace’s own limits.">
        <Rules key={channel.id + JSON.stringify(channel.config)} channel={channel} workspaceMax={info.data?.max_file_mb} />
      </Section>

      <Section title="Activity" description="Every attempt to send a document, with the reason when one was refused.">
        <Activity channel={channel} />
      </Section>

      <Section title="Name and removal">
        <div className="flex flex-col gap-4">
          <form className="flex items-end gap-2" onSubmit={(e) => { e.preventDefault(); if (name.trim() && name.trim() !== channel.name) rename.mutate(); }}>
            <div className="min-w-0 flex-1"><Field label="Channel name">{(id) => <Input id={id} value={name} onChange={(e) => setName(e.target.value)} maxLength={120} />}</Field></div>
            <Button type="submit" variant="secondary" disabled={!name.trim() || name.trim() === channel.name || rename.isPending}>Rename</Button>
          </form>
          <div>
            <Button variant="danger" onClick={() => setRemoving(true)}>Remove this channel</Button>
            <p className="mt-1.5 text-meta text-muted">Its keys stop working. Documents that came through it stay in the workspace.</p>
          </div>
        </div>
      </Section>

      <NewKeyDialog channel={channel} open={newKey} onOpenChange={setNewKey} />
      <Dialog open={removing} onOpenChange={setRemoving} title={`Remove “${channel.name}”?`} description="Its keys stop working at once. Documents that came through it stay.">
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setRemoving(false)}>Keep it</Button>
          <Button variant="danger" disabled={remove.isPending} onClick={() => remove.mutate()}>Remove</Button>
        </div>
      </Dialog>
    </div>
  );
}
