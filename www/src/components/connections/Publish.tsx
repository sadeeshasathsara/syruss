"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Plus } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
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
import { Stepper } from "@/components/ui/Stepper";
import { Switch } from "@/components/ui/Switch";
import { useAccess } from "@/lib/access";
import { api } from "@/lib/api";
import { ago, cn } from "@/lib/format";
import type { DestinationInfo, DestinationKind, DestinationPolicy, MappingSpec } from "@/lib/types";
import { useWorkspace } from "@/lib/workspace";
import { ConnectForm, missing, withDefaults } from "./ConnectForm";
import { MappingEditor } from "./MappingEditor";

const CATEGORY_TITLE: Record<DestinationKind["category"], string> = {
  custom: "Your own systems", accounting: "Accounting and ERP", storage: "Files", mail: "Mail",
};

function useDestinations() {
  const { workspace } = useWorkspace();
  const { can } = useAccess(workspace.id);
  const allowed = can("integrations.manage");
  const list = useQuery({ queryKey: ["destinations", workspace.id], queryFn: () => api.destinations.list(workspace.id), enabled: allowed });
  const catalogue = useQuery({ queryKey: ["destinations", workspace.id, "catalogue"], queryFn: () => api.destinations.catalogue(workspace.id), enabled: allowed, staleTime: 5 * 60_000 });
  return { workspace, allowed, list, catalogue };
}

function Counts({ d }: { d: DestinationInfo }) {
  return (
    <dl className="grid flex-none grid-cols-4 gap-x-5 text-meta">
      {[["Delivered today", d.counts.delivered_24h, false], ["Waiting", d.counts.waiting, false], ["Held back", d.counts.held, false], ["Failed", d.counts.dead, d.counts.dead > 0]].map(([label, n, bad]) => (
        <div key={String(label)}><dt className="text-muted">{label}</dt><dd className={cn("num mt-0.5 text-body font-medium", bad ? "text-negative" : "text-ink")}>{n}</dd></div>
      ))}
    </dl>
  );
}

// ---------------------------------------------------------------------------------------------------
// The list
// ---------------------------------------------------------------------------------------------------

export function PublishPage() {
  const { href } = useWorkspace();
  const { allowed, list } = useDestinations();
  const title = "Publish";
  const description = "Where approved data goes: your own systems, accounting and ERP. Nothing is sent until a document is approved and nothing about it is in doubt.";
  if (!allowed) return <div className="flex flex-col"><SettingsHeader title={title} description={description} /><div className="pt-5"><NoAccess what="manage connections" /></div></div>;
  return (
    <div className="flex flex-col">
      <SettingsHeader title={title} description={description} actions={<Button asChild variant="primary"><Link href={href("connections/publish/new")}><Plus /> Connect a system</Link></Button>} />
      <div className="flex flex-col gap-3 pt-5">
        {list.isLoading && [0, 1].map((i) => <Bar key={i} className="h-24 w-full" />)}
        {list.isError && <ErrorNote message="Couldn’t load the destinations." onRetry={() => list.refetch()} />}
        {list.data?.length === 0 && (
          <Empty title="Nothing is connected yet" action={<Button asChild variant="primary"><Link href={href("connections/publish/new")}><Plus /> Connect the first system</Link></Button>}>
            Connect a system and every approved record is sent to it by itself: signed, retried when the other side is down, and kept in a log. Documents with a doubt about
            them are held back until it is settled.
          </Empty>
        )}
        <ul className="flex flex-col gap-3">
          {(list.data ?? []).map((d) => (
            <li key={d.id}>
              <Link href={href(`connections/publish/${d.id}`)} className="group flex flex-col gap-3 rounded-card border border-border bg-card [box-shadow:var(--shadow-card)] p-4 transition-colors hover:border-control @2xl:flex-row @2xl:items-center">
                <span className="min-w-0 flex-1">
                  <span className="flex flex-wrap items-center gap-2">
                    <span className="text-section font-semibold text-ink">{d.name}</span>
                    <Pill>{d.label}</Pill>
                    <Pill tone={d.health.tone}>{d.health.label}</Pill>
                  </span>
                  <span className="mt-1 block truncate text-meta text-muted">
                    {d.status === "paused" ? d.paused_reason : d.last_success_at ? `Last delivered ${ago(d.last_success_at).toLowerCase()}` : "Nothing delivered yet"}
                  </span>
                </span>
                <Counts d={d} />
                <ArrowRight className="hidden size-4 flex-none text-muted transition-transform group-hover:translate-x-0.5 @2xl:block" />
              </Link>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------------------------------
// Connecting a new system
// ---------------------------------------------------------------------------------------------------

const STEPS = ["System", "Connect", "What to send", "Rules", "Check and turn on"];

export function NewDestinationPage() {
  const { workspace, href } = useWorkspace();
  const router = useRouter();
  const queryClient = useQueryClient();
  const params = useSearchParams();
  const { allowed, catalogue } = useDestinations();
  const sources = useQuery({ queryKey: ["destinations", workspace.id, "sources"], queryFn: () => api.destinations.sources(workspace.id), enabled: allowed });
  const [step, setStep] = useState(0);
  const [kind, setKind] = useState<DestinationKind | null>(null);
  const [name, setName] = useState("");
  const [config, setConfig] = useState<Record<string, unknown>>({});
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [mapping, setMapping] = useState<MappingSpec>({ mode: "record" });
  const [policy, setPolicy] = useState<DestinationPolicy>({ send_updates: true, include_test: false, max_attempts: 8 });
  const [docId, setDocId] = useState("none");
  const [created, setCreated] = useState<{ id: string; secret?: string } | null>(null);

  const choose = (k: DestinationKind) => {
    setKind(k);
    setName(k.label);
    setConfig(withDefaults(k, {}));
    setSecrets({});
    setPolicy({ send_updates: k.corrections, include_test: false, max_attempts: 8 });
    setMapping({ mode: "record" });
    if (k.targets.length) api.destinations.suggestMapping(workspace.id, k.kind).then(setMapping).catch(() => setMapping({ mode: "mapped", fields: [] }));
    setStep(1);
  };
  useEffect(() => {
    const wanted = params.get("kind");
    if (wanted && catalogue.data && !kind) { const k = catalogue.data.find((c) => c.kind === wanted); if (k) choose(k); }
  }, [catalogue.data]); // eslint-disable-line react-hooks/exhaustive-deps

  const lacking = kind ? missing(kind, config, secrets) : [];
  const test = useMutation({ mutationFn: () => api.destinations.tryConnection(workspace.id, { kind: kind!.kind, config: withDefaults(kind!, config), secrets }) });
  const preview = useMutation({ mutationFn: () => api.destinations.preview(workspace.id, { kind: kind!.kind, mapping, document_id: docId }) });
  const create = useMutation({
    mutationFn: () => api.destinations.create(workspace.id, { kind: kind!.kind, name: name.trim() || kind!.label, config: withDefaults(kind!, config), secrets, mapping, policy }),
    onSuccess: (d) => {
      queryClient.invalidateQueries({ queryKey: ["destinations", workspace.id] });
      queryClient.invalidateQueries({ queryKey: ["connections", workspace.id] });
      if (d.signing_secret) setCreated({ id: d.id, secret: d.signing_secret });
      else router.push(href(`connections/publish/${d.id}${kind?.uses_oauth ? "?setup=1" : ""}`));
    },
  });
  useEffect(() => { preview.reset(); }, [docId, mapping]); // eslint-disable-line react-hooks/exhaustive-deps

  const groups = useMemo(() => {
    const order: DestinationKind["category"][] = ["custom", "accounting", "storage", "mail"];
    return order.map((c) => ({ category: c, kinds: (catalogue.data ?? []).filter((k) => k.category === c) })).filter((g) => g.kinds.length);
  }, [catalogue.data]);

  if (!allowed) return <div className="flex flex-col"><SettingsHeader title="Connect a system" description="" /><div className="pt-5"><NoAccess what="manage connections" /></div></div>;
  if (created) {
    return (
      <div className="flex max-w-2xl flex-col gap-4">
        <h1 className="text-title font-semibold text-ink">{name} is connected</h1>
        <p className="text-body text-muted">Every request carries a signature made with this secret, so your system can tell it really came from Syruss. Put it where your receiving code can read it.</p>
        <ShowOnce label="Signing secret" value={created.secret!} onDone={() => router.push(href(`connections/publish/${created.id}`))} />
        <Link href={href("connections/developers")} className="text-body text-muted underline-offset-2 hover:text-ink hover:underline">How to check the signature</Link>
      </div>
    );
  }

  const canNext = step === 1 ? lacking.length === 0 && !!name.trim() : step === 2 && kind?.mapping_required ? (mapping.fields ?? []).some((f) => f.value) : true;

  return (
    <div className="flex max-w-3xl flex-col gap-6">
      <header>
        <Link href={href("connections/publish")} className="mb-1 inline-block text-body text-muted hover:text-ink">All destinations</Link>
        <h1 className="text-title font-semibold tracking-[-0.01em] text-ink">Connect a system</h1>
      </header>
      <Stepper steps={STEPS} current={step} />

      {step === 0 && (
        <div className="flex flex-col gap-6">
          {catalogue.isLoading && <Bar className="h-32 w-full" />}
          {groups.map((g) => (
            <section key={g.category} className="flex flex-col gap-2">
              <h2 className="text-body font-medium text-ink">{CATEGORY_TITLE[g.category]}</h2>
              <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                {g.kinds.map((k) => (
                  <li key={k.kind}>
                    <button type="button" onClick={() => choose(k)} className="flex h-full w-full flex-col items-start gap-1 rounded-card border border-border bg-card [box-shadow:var(--shadow-card)] p-4 text-left transition-colors hover:border-control">
                      <span className="text-section font-semibold text-ink">{k.label}</span>
                      <span className="text-body text-muted">{k.blurb}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}

      {kind && step === 1 && (
        <div className="flex flex-col gap-5">
          <p className="text-body text-muted">{kind.blurb}</p>
          <Field label="Name" help="How you will recognise it in lists.">{(id) => <Input id={id} value={name} onChange={(e) => setName(e.target.value)} maxLength={120} />}</Field>
          <RedirectNote kind={kind} />
          <ConnectForm kind={kind} config={config} secrets={secrets} onConfig={setConfig} onSecrets={setSecrets} />
        </div>
      )}

      {kind && step === 2 && sources.data && (
        <div className="flex flex-col gap-3">
          <p className="text-body text-muted">{kind.mapping_required ? `${kind.label} needs specific fields. Syruss guessed where each comes from; check them.` : "Choose what the other system receives."}</p>
          <MappingEditor kind={kind} sources={sources.data} value={mapping} onChange={setMapping} />
        </div>
      )}

      {kind && step === 3 && (
        <div className="flex flex-col gap-4">
          {kind.corrections && (
            <Toggle label="Send corrections" help="When someone corrects a record after it was sent, send the new version too, marked as a correction." checked={policy.send_updates} onChange={(v) => setPolicy({ ...policy, send_updates: v })} />
          )}
          <Toggle label="Include test documents" help="Documents sent with a test key are never published unless you turn this on." checked={policy.include_test} onChange={(v) => setPolicy({ ...policy, include_test: v })} />
          <Field label="Tries before giving up" help="A failed delivery is tried again after 1 minute, 5 minutes, 30 minutes, 2 hours and so on, then set aside so you can replay it.">
            {(id) => <Select id={id} value={String(policy.max_attempts)} onChange={(v) => setPolicy({ ...policy, max_attempts: Number(v) })} options={[3, 5, 8, 12].map((n) => ({ value: String(n), label: `${n} tries` }))} />}
          </Field>
          <p className="rounded-card bg-raised px-4 py-3 text-body text-muted">
            Whatever you choose here, a document is never sent until it is approved, and is held back while Syruss has a question or a conversation open about it or a person still has to review it.
          </p>
        </div>
      )}

      {kind && step === 4 && (
        <div className="flex flex-col gap-5">
          {kind.uses_oauth && (
            <p className="rounded-card bg-raised px-4 py-3 text-body text-ink">
              The next step is to sign in to {kind.label} and approve access. It happens on {kind.label}’s own page, so Syruss never sees your password. Until then the
              destination waits, and approved records are kept, not lost.
            </p>
          )}
          {!kind.uses_oauth && <section className="flex flex-col gap-2">
            <h2 className="text-body font-medium text-ink">1. Check the connection</h2>
            <div className="flex flex-wrap items-center gap-3">
              <Button variant="secondary" disabled={test.isPending || lacking.length > 0} onClick={() => test.mutate()}>{test.isPending ? "Checking…" : "Test the connection"}</Button>
              {test.data && <span className={cn("text-body", test.data.ok ? "text-positive" : "text-negative")}>{test.data.message}</span>}
              {test.error && <span className="text-body text-negative">{test.error.message}</span>}
            </div>
            {test.data?.detail && <pre className="scrollbar-thin max-h-32 overflow-auto rounded-control bg-raised p-3 font-mono text-meta text-ink">{test.data.detail}</pre>}
          </section>}
          {(sources.data?.documents.length ?? 0) > 0 && (
            <section className="flex flex-col gap-2">
              <h2 className="text-body font-medium text-ink">2. See what would be sent</h2>
              <div className="flex flex-wrap items-center gap-3">
                <div className="w-64">
                  <Select label="Document to try" value={docId} onChange={setDocId} options={[{ value: "none", label: "Choose an approved document…" }, ...sources.data!.documents.map((d) => ({ value: d.id, label: d.filename }))]} />
                </div>
                <Button variant="secondary" disabled={docId === "none" || preview.isPending} onClick={() => preview.mutate()}>Show it</Button>
              </div>
              {preview.data && (
                <>
                  {preview.data.problems.length > 0 && <ul className="list-disc rounded-card bg-raised py-3 pl-9 pr-4 text-body text-negative">{preview.data.problems.map((p) => <li key={p}>{p}</li>)}</ul>}
                  <CodeBlock samples={[{ label: "What the other system receives", code: JSON.stringify(preview.data.envelope, null, 2) }]} />
                </>
              )}
              {preview.error && <p className="text-body text-negative">{preview.error.message}</p>}
            </section>
          )}
          {create.error && <p className="rounded-card bg-raised px-4 py-3 text-body text-negative">{create.error.message}</p>}
        </div>
      )}

      {step > 0 && (
        <div className="flex items-center justify-between gap-2 border-t border-border pt-4">
          <Button variant="ghost" onClick={() => setStep(step - 1)}>Back</Button>
          <div className="flex items-center gap-3">
            {step === 1 && lacking.length > 0 && <span className="hidden text-meta text-muted sm:inline">Still needed: {lacking.join(", ")}</span>}
            {step < STEPS.length - 1 ? (
              <Button variant="primary" disabled={!canNext} onClick={() => setStep(step + 1)}>Continue</Button>
            ) : (
              <Button variant="primary" disabled={create.isPending || lacking.length > 0} onClick={() => create.mutate()}>{create.isPending ? "Creating…" : kind?.uses_oauth ? "Create and connect" : "Turn it on"}</Button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/** Where the vendor sends the person back after they approve access: it must be registered in the vendor's app first. */
function RedirectNote({ kind }: { kind: DestinationKind }) {
  if (!kind.uses_oauth || !kind.redirect_uri) return null;
  return (
    <div className="flex flex-col gap-2 rounded-card bg-raised px-4 py-3">
      <p className="text-body text-ink">In your {kind.label} app, add this as an allowed redirect address. {kind.label} sends the person back here after they approve access.</p>
      <div className="flex min-w-0 items-center gap-2">
        <code className="min-w-0 flex-1 truncate rounded-control bg-raised px-3 py-2 font-mono text-meta text-ink">{kind.redirect_uri}</code>
        <CopyButton value={kind.redirect_uri} />
      </div>
    </div>
  );
}

function Toggle({ label, help, checked, onChange }: { label: string; help: string; checked: boolean; onChange: (v: boolean) => void }) {
  return (
    <div className="flex items-start gap-3">
      <Switch label={label} checked={checked} onChange={onChange} />
      <div><p className="text-body font-medium text-ink">{label}</p><p className="text-body text-muted">{help}</p></div>
    </div>
  );
}

// ---------------------------------------------------------------------------------------------------
// One destination
// ---------------------------------------------------------------------------------------------------

export function DestinationPage({ id }: { id: string }) {
  const { workspace, href } = useWorkspace();
  const queryClient = useQueryClient();
  const router = useRouter();
  const { allowed, catalogue } = useDestinations();
  const params = useSearchParams();
  const dest = useQuery({ queryKey: ["destinations", workspace.id, id], queryFn: () => api.destinations.get(workspace.id, id), enabled: allowed });
  const sources = useQuery({ queryKey: ["destinations", workspace.id, "sources"], queryFn: () => api.destinations.sources(workspace.id), enabled: allowed });
  const kind = catalogue.data?.find((k) => k.kind === dest.data?.kind);
  const refresh = () => ["destinations", "connections", "deliveries"].forEach((k) => queryClient.invalidateQueries({ queryKey: [k, workspace.id] }));

  const [name, setName] = useState("");
  const [config, setConfig] = useState<Record<string, unknown>>({});
  const [secrets, setSecrets] = useState<Record<string, string>>({});
  const [mapping, setMapping] = useState<MappingSpec>({});
  const [policy, setPolicy] = useState<DestinationPolicy | null>(null);
  const [docId, setDocId] = useState("none");
  const [newSecret, setNewSecret] = useState<string | null>(null);
  const [removing, setRemoving] = useState(false);
  useEffect(() => {
    if (dest.data) { setName(dest.data.name); setConfig(dest.data.config); setMapping(dest.data.mapping); setPolicy(dest.data.policy); setSecrets({}); }
  }, [dest.data?.id, dest.dataUpdatedAt]); // eslint-disable-line react-hooks/exhaustive-deps

  const save = useMutation({
    mutationFn: (what: "connection" | "mapping" | "rules") => api.destinations.update(workspace.id, id, what === "connection" ? { name: name.trim(), config: withDefaults(kind!, config), secrets } : what === "mapping" ? { mapping } : { policy: policy! }),
    onSuccess: refresh,
  });
  const toggle = useMutation({ mutationFn: (pause: boolean) => (pause ? api.destinations.pause(workspace.id, id) : api.destinations.resume(workspace.id, id)), onSuccess: refresh });
  const test = useMutation({ mutationFn: () => api.destinations.tryConnection(workspace.id, { kind: kind!.kind, config: withDefaults(kind!, config), secrets, destination_id: id }) });
  const preview = useMutation({ mutationFn: () => api.destinations.preview(workspace.id, { kind: kind!.kind, mapping, document_id: docId }) });
  const rotate = useMutation({ mutationFn: (hours: number) => api.destinations.rotateSecret(workspace.id, id, hours), onSuccess: (r) => { setNewSecret(r.signing_secret); refresh(); } });
  const remove = useMutation({ mutationFn: () => api.destinations.remove(workspace.id, id), onSuccess: () => { refresh(); router.replace(href("connections/publish")); } });
  const [overlap, setOverlap] = useState("24");
  const connect = useMutation({
    mutationFn: () => api.destinations.oauthStart(workspace.id, id),
    onSuccess: (r) => window.location.assign(r.url),
  });

  if (!allowed) return <div className="flex flex-col"><SettingsHeader title="Publish" description="" /><div className="pt-5"><NoAccess what="manage connections" /></div></div>;
  if (dest.isLoading || catalogue.isLoading) return <div className="flex flex-col gap-3"><Bar className="h-10 w-72" /><Bar className="h-40 w-full" /></div>;
  if (dest.isError || !dest.data || !kind || !policy) {
    return <div className="flex flex-col gap-3"><Link href={href("connections/publish")} className="text-body text-muted hover:text-ink">All destinations</Link><Empty title="No such destination">It may have been removed.</Empty></div>;
  }
  const d = dest.data;
  const lacking = missing(kind, config, secrets, d.secrets_set);

  return (
    <div className="flex flex-col">
      <header className="flex flex-col gap-3 border-b border-border pb-5 @3xl:flex-row @3xl:items-end @3xl:justify-between">
        <div className="min-w-0">
          <Link href={href("connections/publish")} className="mb-1 inline-block text-body text-muted hover:text-ink">All destinations</Link>
          <h1 className="flex flex-wrap items-center gap-2 text-title font-semibold tracking-[-0.01em] text-ink">{d.name} <Pill>{d.label}</Pill> <Pill tone={d.health.tone}>{d.health.label}</Pill></h1>
          <p className="mt-1 text-body text-muted">
            {d.status === "paused" ? d.paused_reason : d.last_success_at ? `Last delivered ${ago(d.last_success_at).toLowerCase()}.` : "Nothing delivered yet."}
            {d.last_error && d.status === "active" && d.consecutive_failures > 0 ? ` Last problem: ${d.last_error}` : ""}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button asChild variant="secondary"><Link href={`${href("connections/deliveries")}?destination=${d.id}`}>See its deliveries</Link></Button>
          {d.status === "active"
            ? <Button variant="secondary" disabled={toggle.isPending} onClick={() => toggle.mutate(true)}>Pause</Button>
            : <Button variant="primary" disabled={toggle.isPending} onClick={() => toggle.mutate(false)}>Resume</Button>}
        </div>
      </header>

      {params.get("connected") && <p role="status" className="mt-4 rounded-card bg-raised px-4 py-3 text-body text-positive">Connected. {d.label} will now receive approved records.</p>}
      {params.get("connect_error") && <p role="alert" className="mt-4 rounded-card bg-raised px-4 py-3 text-body text-negative">{params.get("connect_error")}</p>}

      {kind.uses_oauth && (
        <Section title="Account" description={`Sign in to ${kind.label} and approve access. It happens on their page; Syruss never sees your password.`}>
          <div className="flex flex-col gap-3">
            <p className="text-body text-ink">{d.connected ? `Connected to ${kind.label}.` : `Not connected to ${kind.label} yet. Nothing is sent until it is, and approved records wait.`}</p>
            {!d.connected && <RedirectNote kind={kind} />}
            <div className="flex flex-wrap items-center gap-3">
              <Button variant={d.connected ? "secondary" : "primary"} disabled={connect.isPending || !d.secrets_set.includes("client_secret")} onClick={() => connect.mutate()}>
                {d.connected ? "Connect again" : "Connect your account"}
              </Button>
              {!d.secrets_set.includes("client_secret") && <span className="text-meta text-muted">Save the client ID and secret below first.</span>}
              {connect.error && <span className="text-body text-negative">{connect.error.message}</span>}
            </div>
          </div>
        </Section>
      )}

      <Section title="Connection" description="Passwords and tokens are stored encrypted and never shown again.">
        <div className="flex flex-col gap-4">
          <Field label="Name">{(fid) => <Input id={fid} value={name} onChange={(e) => setName(e.target.value)} maxLength={120} />}</Field>
          <ConnectForm kind={kind} config={config} secrets={secrets} stored={d.secrets_set} onConfig={setConfig} onSecrets={setSecrets} />
          <div className="flex flex-wrap items-center gap-3">
            <Button variant="primary" disabled={save.isPending || lacking.length > 0 || !name.trim()} onClick={() => save.mutate("connection")}>Save</Button>
            <Button variant="secondary" disabled={test.isPending || lacking.length > 0} onClick={() => test.mutate()}>{test.isPending ? "Checking…" : "Test the connection"}</Button>
            {test.data && <span className={cn("text-body", test.data.ok ? "text-positive" : "text-negative")}>{test.data.message}</span>}
            {save.error && save.variables === "connection" && <span className="text-body text-negative">{save.error.message}</span>}
          </div>
        </div>
      </Section>

      {sources.data && (
        <Section title="What is sent" description="Try it on a real document before you save: nothing is sent.">
          <div className="flex flex-col gap-4">
            <MappingEditor kind={kind} sources={sources.data} value={mapping} onChange={setMapping} />
            <div className="flex flex-wrap items-center gap-3">
              <Button variant="primary" disabled={save.isPending} onClick={() => save.mutate("mapping")}>Save</Button>
              {sources.data.documents.length > 0 && (
                <>
                  <div className="w-56"><Select label="Document to try" value={docId} onChange={setDocId} options={[{ value: "none", label: "Try it on…" }, ...sources.data.documents.map((x) => ({ value: x.id, label: x.filename }))]} /></div>
                  <Button variant="secondary" disabled={docId === "none" || preview.isPending} onClick={() => preview.mutate()}>Show what is sent</Button>
                </>
              )}
              {save.error && save.variables === "mapping" && <span className="text-body text-negative">{save.error.message}</span>}
            </div>
            {preview.data && (
              <>
                {preview.data.problems.length > 0 && <ul className="list-disc rounded-card bg-raised py-3 pl-9 pr-4 text-body text-negative">{preview.data.problems.map((p) => <li key={p}>{p}</li>)}</ul>}
                <CodeBlock samples={[{ label: "What the other system receives", code: JSON.stringify(preview.data.envelope, null, 2) }]} />
              </>
            )}
          </div>
        </Section>
      )}

      <Section title="Rules">
        <div className="flex flex-col gap-4">
          {kind.corrections && <Toggle label="Send corrections" help="Send the new version when a saved record is corrected after it was sent." checked={policy.send_updates} onChange={(v) => setPolicy({ ...policy, send_updates: v })} />}
          <Toggle label="Include test documents" help="Documents sent with a test key are normally never published." checked={policy.include_test} onChange={(v) => setPolicy({ ...policy, include_test: v })} />
          <Field label="Tries before giving up">
            {(fid) => <Select id={fid} value={String(policy.max_attempts)} onChange={(v) => setPolicy({ ...policy, max_attempts: Number(v) })} options={[3, 5, 8, 12].map((n) => ({ value: String(n), label: `${n} tries` }))} />}
          </Field>
          <div className="flex items-center gap-3">
            <Button variant="primary" disabled={save.isPending} onClick={() => save.mutate("rules")}>Save rules</Button>
            {save.isSuccess && save.variables === "rules" && <span className="text-body text-muted">Saved</span>}
          </div>
        </div>
      </Section>

      {d.kind === "webhook" && (
        <Section title="Signing secret" description="Receivers check the signature of each request with it. A new secret keeps the old one working for a while so you can switch without a gap.">
          <div className="flex flex-col gap-3">
            {newSecret ? <ShowOnce label="The new signing secret" value={newSecret} onDone={() => setNewSecret(null)} /> : (
              <div className="flex flex-wrap items-end gap-3">
                <div className="w-56"><Field label="Old secret keeps working for">{(fid) => <Select id={fid} value={overlap} onChange={setOverlap} options={[{ value: "0", label: "Not at all" }, { value: "24", label: "24 hours" }, { value: "72", label: "3 days" }]} />}</Field></div>
                <Button variant="secondary" disabled={rotate.isPending} onClick={() => rotate.mutate(Number(overlap))}>Create a new secret</Button>
              </div>
            )}
            <Link href={href("connections/developers")} className="text-body text-muted underline-offset-2 hover:text-ink hover:underline">How to check the signature</Link>
          </div>
        </Section>
      )}

      <Section title="Remove">
        <Button variant="danger" onClick={() => setRemoving(true)}>Remove this destination</Button>
        <p className="mt-1.5 text-meta text-muted">Nothing more is sent to it, and its delivery history is deleted.</p>
      </Section>
      <Dialog open={removing} onOpenChange={setRemoving} title={`Remove “${d.name}”?`} description="Nothing more is sent to it, and its delivery history is deleted. Records already delivered stay where they were sent.">
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setRemoving(false)}>Keep it</Button>
          <Button variant="danger" disabled={remove.isPending} onClick={() => remove.mutate()}>Remove</Button>
        </div>
      </Dialog>
    </div>
  );
}
