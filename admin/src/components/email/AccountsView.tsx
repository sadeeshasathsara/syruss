"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useEffect, useState } from "react";
import { field } from "@/components/ops/Bits";
import { Panel } from "@/components/ui/Blocks";
import { Modal } from "@/components/ui/Modal";
import { Button } from "@/components/ui/Button";
import { Pill } from "@/components/ui/Pill";
import { Problem } from "@/components/ui/Problem";
import { api } from "@/lib/api";
import { ago, cn } from "@/lib/format";
import { loadPage } from "@/lib/navigate";
import { canSeal, sealForMail } from "@/lib/mailSeal";
import type { MailAccount, MailImapInput, MailOverview, MailProvider, MailPurpose } from "@/lib/types";
import { PROVIDER_LABEL } from "./OAuthReturn";
import { Select } from "@/components/ui/Select";

const KIND: Record<MailAccount["kind"], string> = { gmail: "Google", graph: "Microsoft", smtp_imap: "Mail server", console: "Log only", memory: "Memory" };

function useRefresh() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: ["mail"] });
}

/** The connected mailboxes, and ways to connect one: Google, Microsoft, or any mail server. */
export function AccountsView({ o, connecting, onClose }: { o: MailOverview; connecting: boolean; onClose: () => void }) {
  return (
    <>
      <Accounts o={o} />
      <Connect o={o} open={connecting} onClose={onClose} />
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

/** The job a mailbox is for: one dropdown. (Other jobs can be pointed at it afterwards, on the Jobs tab.) */
function JobSelect({ o, value, onChange, optional }: { o: MailOverview; value: MailPurpose; onChange: (job: MailPurpose) => void; optional?: boolean }) {
  return (
    <Select aria-label="Job" value={value} onValueChange={onChange} className="w-full">
      {optional && <option value="">No job yet</option>}
      {Object.entries(o.purposes).map(([key, j]) => <option key={key} value={key}>{j.label}</option>)}
    </Select>
  );
}

function Connect({ o, open, onClose }: { o: MailOverview; open: boolean; onClose: () => void }) {
  const [job, setJob] = useState<MailPurpose>("notifications");
  const [imap, setImap] = useState(false);
  const start = useMutation({ mutationFn: (provider: MailProvider) => api.mail.oauthStart(provider, [job]), onSuccess: (r) => loadPage(r.url) });
  const close = () => { setImap(false); onClose(); };
  return (
    <Modal open={open} onClose={close} title={imap ? "Add a mailbox on another mail server" : "Connect a mailbox"} wide={imap}>
      {imap ? <ImapForm o={o} onDone={close} onBack={() => setImap(false)} /> : (
        <div className="flex flex-col gap-5">
          <div className="flex flex-col gap-1.5">
            <p className="text-meta text-muted">What is this mailbox for?</p>
            <JobSelect o={o} value={job} onChange={setJob} />
            <p className="text-meta text-muted">You approve only what that job needs: sending, and reading where it reads. Syruss can&apos;t delete or change anything in the mailbox.</p>
          </div>
          <div className="flex flex-col gap-2">
            {(["google", "microsoft"] as MailProvider[]).map((p) => (
              <div key={p} className="flex flex-col gap-1">
                <Button variant="primary" className="w-full justify-center" disabled={!o.providers[p].configured || start.isPending} onClick={() => start.mutate(p)}>
                  {start.isPending && start.variables === p ? `Opening ${PROVIDER_LABEL[p]}…` : `Connect with ${PROVIDER_LABEL[p]}`}
                </Button>
                {!o.providers[p].configured && (
                  <p className="text-meta text-[var(--pill-warning-ink)]">Not set up yet: add its app under <Link href="/email/apps" className="underline">Provider apps</Link>.</p>
                )}
              </div>
            ))}
          </div>
          <Problem error={start.error} />
          <div className="border-t border-rule pt-4">
            <Button size="sm" onClick={() => setImap(true)}>Use another mail server instead</Button>
          </div>
        </div>
      )}
    </Modal>
  );
}

function ImapForm({ o, onDone, onBack }: { o: MailOverview; onDone: () => void; onBack: () => void }) {
  const refresh = useRefresh();
  const [v, setV] = useState<MailImapInput>({ address: "", name: "Syruss", smtp_host: "", smtp_port: 587, smtp_security: "starttls", smtp_username: "",
    smtp_password: "", imap_host: "", imap_port: 993, imap_ssl: true, imap_folder: "INBOX", imap_username: "", imap_password: "" });
  const [job, setJob] = useState<MailPurpose>("");
  const [sealing, setSealing] = useState<boolean | null>(null);
  useEffect(() => {
    canSeal().then(setSealing);
  }, []);
  const publicKey = o.key.public_key;
  const save = useMutation({
    mutationFn: async () => {
      const body: MailImapInput = { ...v, smtp_host: v.smtp_host || undefined, imap_host: v.imap_host || undefined, jobs: job ? [job] : [] };
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
          <Select value={v.smtp_security} onValueChange={(s) => setV({ ...v, smtp_security: s as MailImapInput["smtp_security"] })}>
            <option value="starttls">STARTTLS</option><option value="ssl">SSL</option><option value="none">None</option>
          </Select>
        </label>
        {text("smtp_username", "Username")}{text("smtp_password", "Password", "password")}
      </div>
      <p className="text-meta font-medium text-muted">Reading (IMAP, read-only), for the jobs that read</p>
      <div className="flex flex-wrap gap-3">
        {text("imap_host", "Server")}{text("imap_port", "Port", "number", "w-24")}{text("imap_folder", "Folder", "text", "w-32")}
        {text("imap_username", "Username")}{text("imap_password", "Password", "password")}
      </div>
      <p className="text-meta font-medium text-muted">What is this mailbox for?</p>
      <JobSelect o={o} value={job} onChange={setJob} optional />
      <p className="text-meta text-muted">
        {sealing === null ? "" : sealing && publicKey
          ? <>Passwords are sealed in this browser to the mail service&apos;s key before they are sent, so neither this app&apos;s server nor Kafka ever holds one.
            Its fingerprint is <span className="num font-medium text-ink">{o.key.fingerprint}</span>: it should match what <code>python -m mail fingerprint</code> prints on the mail server.</>
          : "This browser can't seal passwords itself, so the server seals them to the mail service's key before they go on. Use a current Chrome, Firefox or Safari to seal them here."}
      </p>
      <div className="flex gap-2">
        <Button type="submit" size="sm" variant="primary" disabled={!v.address || (!v.smtp_host && !v.imap_host) || save.isPending}>{save.isPending ? "Saving…" : "Add mailbox"}</Button>
        <Button size="sm" onClick={onBack}>Back</Button>
      </div>
      <Problem error={save.error} />
    </form>
  );
}
