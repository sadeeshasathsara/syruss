"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useState } from "react";
import { field } from "@/components/ops/Bits";
import { Panel } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Pill } from "@/components/ui/Pill";
import { Problem } from "@/components/ui/Problem";
import { api } from "@/lib/api";
import { ago, cn } from "@/lib/format";
import { loadPage } from "@/lib/navigate";
import { canSeal, sealForMail } from "@/lib/mailSeal";
import type { MailAccount, MailImapInput, MailOverview, MailProvider, MailPurpose } from "@/lib/types";
import { PROVIDER_LABEL } from "./OAuthReturn";

const KIND: Record<MailAccount["kind"], string> = { gmail: "Google", graph: "Microsoft", smtp_imap: "Mail server", console: "Log only", memory: "Memory" };

function useRefresh() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: ["mail"] });
}

/** The connected mailboxes, and ways to connect one: Google, Microsoft, or any mail server. */
export function AccountsView({ o }: { o: MailOverview }) {
  return (
    <>
      <Accounts o={o} />
      <Connect o={o} />
    </>
  );
}

function Accounts({ o }: { o: MailOverview }) {
  const refresh = useRefresh();
  const label = (key: string) => o.purposes[key]?.label ?? key;
  const [results, setResults] = useState<Record<string, { ok: boolean; detail: string }>>({});
  const test = useMutation({ mutationFn: (id: string) => api.mail.test(id), onSuccess: (r, id) => setResults((all) => ({ ...all, [id]: r })) });
  const remove = useMutation({ mutationFn: (id: string) => api.mail.remove(id), onSuccess: refresh });
  const reconnect = useMutation({ mutationFn: (a: MailAccount) => api.mail.oauthStart(a.kind === "graph" ? "microsoft" : "google", a.purposes.length ? a.purposes : ["notifications"]),
    onSuccess: (r) => loadPage(r.url) });
  return (
    <Panel title="Mailboxes">
      <ul className="flex flex-col divide-y divide-rule">
        {o.accounts.map((a) => (
          <li key={a.id} className="flex flex-col gap-1.5 py-3">
            <div className="flex flex-wrap items-center gap-2">
              <span className="min-w-0 flex-1 truncate text-body font-medium text-ink">{a.address}</span>
              <Pill tone="neutral">{KIND[a.kind] ?? a.kind}</Pill>
              {a.granted.map((g) => <Pill key={g} tone="info">{g === "send" ? "Can send" : "Can read"}</Pill>)}
              {a.status === "needs_reconnect" && <Pill tone="negative">Needs reconnecting</Pill>}
            </div>
            <p className="text-meta text-muted">
              {a.from_environment ? "From the mail service's environment (mail.env): every job's fallback. Changed there, not here." : `Used for: ${a.purposes.map(label).join(", ") || "nothing yet"}`}
              {a.checked_at && ` · checked ${ago(a.checked_at).toLowerCase()}`}
            </p>
            {a.last_error && <p className="text-meta text-negative">{a.last_error}</p>}
            {results[a.id] && <p className={cn("text-meta", results[a.id].ok ? "text-[var(--pill-positive-ink)]" : "text-negative")}>{results[a.id].detail}</p>}
            <div className="flex flex-wrap gap-2">
              <Button size="sm" disabled={test.isPending} onClick={() => test.mutate(a.id)}>Test</Button>
              {(a.kind === "gmail" || a.kind === "graph") && a.status === "needs_reconnect" && (
                <Button size="sm" variant="primary" disabled={reconnect.isPending} onClick={() => reconnect.mutate(a)}>Reconnect with {a.kind === "graph" ? "Microsoft" : "Google"}</Button>
              )}
              {!a.from_environment && <Button size="sm" variant="danger" disabled={remove.isPending} onClick={() => remove.mutate(a.id)}>Remove</Button>}
            </div>
          </li>
        ))}
      </ul>
      <Problem error={test.error ?? remove.error ?? reconnect.error} />
    </Panel>
  );
}

/** Which jobs a mailbox will serve: one checkbox each. */
function JobPicker({ o, value, onChange }: { o: MailOverview; value: MailPurpose[]; onChange: (jobs: MailPurpose[]) => void }) {
  const toggle = (k: MailPurpose) => onChange(value.includes(k) ? value.filter((x) => x !== k) : [...value, k]);
  return (
    <div className="flex flex-wrap gap-4">
      {Object.entries(o.purposes).map(([key, j]) => (
        <label key={key} className="flex items-center gap-1.5 text-body text-ink">
          <input type="checkbox" className="size-4" checked={value.includes(key)} onChange={() => toggle(key)} /> {j.label}
        </label>
      ))}
    </div>
  );
}

function Connect({ o }: { o: MailOverview }) {
  const [jobs, setJobs] = useState<MailPurpose[]>(["notifications"]);
  const [imap, setImap] = useState(false);
  const start = useMutation({ mutationFn: (provider: MailProvider) => api.mail.oauthStart(provider, jobs), onSuccess: (r) => loadPage(r.url) });
  return (
    <Panel title="Connect a mailbox">
      <div className="flex flex-col gap-4">
        <div className="flex flex-col gap-2">
          <p className="text-body text-ink">Google or Microsoft (Gmail, Google Workspace, Outlook.com, Microsoft 365)</p>
          <p className="text-meta text-muted">
            You sign in and approve only what the chosen jobs need: sending, and reading where a job reads. Syruss can&apos;t delete or change
            anything in the mailbox. Mail goes out from the address you sign in with. No app password is involved.
          </p>
          <JobPicker o={o} value={jobs} onChange={setJobs} />
          <div className="flex flex-wrap gap-2">
            {(["google", "microsoft"] as MailProvider[]).map((p) => (
              <Button key={p} variant="primary" disabled={!jobs.length || !o.providers[p].configured || start.isPending} onClick={() => start.mutate(p)}>
                {start.isPending && start.variables === p ? `Opening ${PROVIDER_LABEL[p]}…` : `Connect with ${PROVIDER_LABEL[p]}`}
              </Button>
            ))}
          </div>
          {(["google", "microsoft"] as MailProvider[]).filter((p) => !o.providers[p].configured).map((p) => (
            <p key={p} className="text-meta text-[var(--pill-warning-ink)]">
              {PROVIDER_LABEL[p]} isn&apos;t set up yet: add its app under <Link href="/email/apps" className="underline">Provider apps</Link>.
            </p>
          ))}
          <Problem error={start.error} />
        </div>
        <div className="border-t border-rule pt-4">
          {imap ? <ImapForm o={o} onDone={() => setImap(false)} /> : <Button size="sm" onClick={() => setImap(true)}>Add a mailbox on another mail server</Button>}
        </div>
      </div>
    </Panel>
  );
}

function ImapForm({ o, onDone }: { o: MailOverview; onDone: () => void }) {
  const refresh = useRefresh();
  const [v, setV] = useState<MailImapInput>({ address: "", name: "Syruss", smtp_host: "", smtp_port: 587, smtp_security: "starttls", smtp_username: "",
    smtp_password: "", imap_host: "", imap_port: 993, imap_ssl: true, imap_folder: "INBOX", imap_username: "", imap_password: "" });
  const [jobs, setJobs] = useState<MailPurpose[]>([]);
  const [sealing, setSealing] = useState<boolean | null>(null);
  useEffect(() => {
    canSeal().then(setSealing);
  }, []);
  const publicKey = o.key.public_key;
  const save = useMutation({
    mutationFn: async () => {
      const body: MailImapInput = { ...v, smtp_host: v.smtp_host || undefined, imap_host: v.imap_host || undefined, jobs };
      if (sealing && publicKey && (v.smtp_password || v.imap_password)) {
        // The passwords are sealed here, to the mail service's key: this app's server and Kafka only ever carry the sealed value.
        body.sealed = await sealForMail(publicKey, { smtp_password: v.smtp_password, imap_password: v.imap_password });
        body.smtp_password = "";
        body.imap_password = "";
      }
      return api.mail.addImap(body);
    },
    onSuccess: () => { refresh(); onDone(); },
  });
  const text = (key: keyof MailImapInput, label: string, type = "text", width = "w-56") => (
    <label className="flex flex-col gap-1 text-meta text-muted">{label}
      <input type={type} value={String(v[key] ?? "")} autoComplete="off" className={cn(field, width)}
        onChange={(e) => setV({ ...v, [key]: type === "number" ? Number(e.target.value) : e.target.value })} />
    </label>
  );
  return (
    <form className="flex flex-col gap-3" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
      <div className="flex flex-wrap gap-3">{text("address", "Address")}{text("name", "Sender name")}</div>
      <p className="text-meta font-medium text-muted">Sending (SMTP), to send mail</p>
      <div className="flex flex-wrap gap-3">
        {text("smtp_host", "Server")}{text("smtp_port", "Port", "number", "w-24")}
        <label className="flex flex-col gap-1 text-meta text-muted">Security
          <select value={v.smtp_security} className={field} onChange={(e) => setV({ ...v, smtp_security: e.target.value as MailImapInput["smtp_security"] })}>
            <option value="starttls">STARTTLS</option><option value="ssl">SSL</option><option value="none">None</option>
          </select>
        </label>
        {text("smtp_username", "Username")}{text("smtp_password", "Password", "password")}
      </div>
      <p className="text-meta font-medium text-muted">Reading (IMAP, read-only), for the jobs that read</p>
      <div className="flex flex-wrap gap-3">
        {text("imap_host", "Server")}{text("imap_port", "Port", "number", "w-24")}{text("imap_folder", "Folder", "text", "w-32")}
        {text("imap_username", "Username")}{text("imap_password", "Password", "password")}
      </div>
      <p className="text-meta font-medium text-muted">Which jobs it serves</p>
      <JobPicker o={o} value={jobs} onChange={setJobs} />
      <p className="text-meta text-muted">
        {sealing === null ? "" : sealing && publicKey
          ? <>Passwords are sealed in this browser to the mail service&apos;s key before they are sent, so neither this app&apos;s server nor Kafka ever holds one.
            Its fingerprint is <span className="num font-medium text-ink">{o.key.fingerprint}</span>: it should match what <code>python -m mail fingerprint</code> prints on the mail server.</>
          : "This browser can't seal passwords itself, so the server seals them to the mail service's key before they go on. Use a current Chrome, Firefox or Safari to seal them here."}
      </p>
      <div className="flex gap-2">
        <Button type="submit" size="sm" variant="primary" disabled={!v.address || (!v.smtp_host && !v.imap_host) || save.isPending}>{save.isPending ? "Saving…" : "Add mailbox"}</Button>
        <Button size="sm" onClick={onDone}>Cancel</Button>
      </div>
      <Problem error={save.error} />
    </form>
  );
}
