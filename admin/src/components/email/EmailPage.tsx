"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { field } from "@/components/ops/Bits";
import { Bar, Empty, ErrorNote, PageHeader, Panel } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Pill } from "@/components/ui/Pill";
import { Problem } from "@/components/ui/Problem";
import { api } from "@/lib/api";
import { ago, cn, dateTime } from "@/lib/format";
import { loadPage } from "@/lib/navigate";
import type { MailAccount, MailImapInput, MailJob, MailOverview, MailPurpose } from "@/lib/types";

const PURPOSES: { key: MailPurpose; label: string; what: string }[] = [
  { key: "notifications", label: "Notifications", what: "Invitations, sign-in codes, billing, staff notices, records sent by email. Sends." },
  { key: "accounts", label: "Sign-in emails", what: "Keycloak's own: verify an address, reset a password, a new staff member's set-up link. Sends." },
  { key: "support", label: "Support", what: "Support tickets: answers go out from it, and customers' replies are read from it. Sends and reads." },
];
const KIND: Record<MailAccount["kind"], string> = { gmail: "Google", smtp_imap: "Mail server", console: "Log only", memory: "Memory" };
const STATUS: Record<MailJob["status"], { label: string; tone: "positive" | "warning" | "negative" | "neutral" | "info" }> = {
  queued: { label: "Queued", tone: "info" }, sending: { label: "Sending", tone: "info" }, retrying: { label: "Trying again", tone: "warning" },
  sent: { label: "Sent", tone: "positive" }, dead: { label: "Not sent", tone: "negative" },
};

export type EmailView = "accounts" | "log";

/**
 * Which mailboxes Syruss sends and reads mail through and what each is for, or how recent mail went (the sidebar's
 * sub-tabs). Owners only.
 */
export function EmailPage({ view }: { view: EmailView }) {
  const overview = useQuery({ queryKey: ["mail", "overview"], queryFn: api.mail.overview, enabled: view === "accounts" });
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Email" subtitle={view === "log"
        ? "What the mail service sent, to whom and how it went. The text itself isn't kept."
        : "Every email Syruss sends goes through the mail service. Connect the mailboxes it uses, and choose which one does what."} />
      {view === "log" ? <Deliveries /> : overview.isError ? <ErrorNote message={overview.error.message} onRetry={() => overview.refetch()} />
        : !overview.data ? <Bar className="h-64 w-full" /> : (
          <>
            <Purposes o={overview.data} />
            <Accounts o={overview.data} />
            <Connect o={overview.data} />
          </>
        )}
    </div>
  );
}

function useRefresh() {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: ["mail"] });
}

function Purposes({ o }: { o: MailOverview }) {
  const refresh = useRefresh();
  const change = useMutation({ mutationFn: ({ purpose, id }: { purpose: MailPurpose; id: string | null }) => api.mail.route(purpose, id), onSuccess: refresh });
  return (
    <Panel title="What mail is for">
      <ul className="flex flex-col divide-y divide-rule">
        {PURPOSES.map((p) => {
          const now = o.purposes[p.key];
          return (
            <li key={p.key} className="flex flex-wrap items-center gap-3 py-3">
              <div className="min-w-0 flex-1">
                <p className="text-body font-medium text-ink">{p.label}</p>
                <p className="text-meta text-muted">{p.what}</p>
              </div>
              <select aria-label={`Account for ${p.label}`} className={cn(field, "w-72")} disabled={change.isPending}
                value={now.routed ? now.account_id : "env"} onChange={(e) => change.mutate({ purpose: p.key, id: e.target.value === "env" ? null : e.target.value })}>
                {o.accounts.map((a) => <option key={a.id} value={a.from_environment ? "env" : a.id}>{a.address}{a.from_environment ? " (from the environment)" : ""}</option>)}
              </select>
              {!now.routed && <Pill tone="neutral">Fallback</Pill>}
              {p.key === "support" && <Pill tone={now.reads ? "positive" : "warning"}>{now.reads ? "Reading replies" : "Not reading replies"}</Pill>}
            </li>
          );
        })}
      </ul>
      <Problem error={change.error} />
    </Panel>
  );
}

function Accounts({ o }: { o: MailOverview }) {
  const refresh = useRefresh();
  const [results, setResults] = useState<Record<string, { ok: boolean; detail: string }>>({});
  const test = useMutation({ mutationFn: (id: string) => api.mail.test(id), onSuccess: (r, id) => setResults((all) => ({ ...all, [id]: r })) });
  const remove = useMutation({ mutationFn: (id: string) => api.mail.remove(id), onSuccess: refresh });
  const reconnect = useMutation({ mutationFn: (a: MailAccount) => api.mail.googleStart(a.purposes.length ? a.purposes : ["notifications"]),
    onSuccess: (r) => loadPage(r.url) });
  return (
    <Panel title="Accounts">
      <ul className="flex flex-col divide-y divide-rule">
        {o.accounts.map((a) => (
          <li key={a.id} className="flex flex-col gap-1.5 py-3">
            <div className="flex flex-wrap items-center gap-2">
              <span className="min-w-0 flex-1 truncate text-body font-medium text-ink">{a.address}</span>
              <Pill tone="neutral">{KIND[a.kind]}</Pill>
              {a.granted.map((g) => <Pill key={g} tone="info">{g === "send" ? "Can send" : "Can read"}</Pill>)}
              {a.status === "needs_reconnect" && <Pill tone="negative">Needs reconnecting</Pill>}
            </div>
            <p className="text-meta text-muted">
              {a.from_environment ? "From the mail service's environment (mail.env): every purpose's fallback. Changed there, not here." : `Used for: ${a.purposes.join(", ") || "nothing yet"}`}
              {a.checked_at && ` · checked ${ago(a.checked_at).toLowerCase()}`}
            </p>
            {a.last_error && <p className="text-meta text-negative">{a.last_error}</p>}
            {results[a.id] && <p className={cn("text-meta", results[a.id].ok ? "text-[var(--pill-positive-ink)]" : "text-negative")}>{results[a.id].detail}</p>}
            <div className="flex flex-wrap gap-2">
              <Button size="sm" disabled={test.isPending} onClick={() => test.mutate(a.id)}>Test</Button>
              {a.kind === "gmail" && a.status === "needs_reconnect" && (
                <Button size="sm" variant="primary" disabled={reconnect.isPending} onClick={() => reconnect.mutate(a)}>Reconnect with Google</Button>
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

function Connect({ o }: { o: MailOverview }) {
  const [purposes, setPurposes] = useState<MailPurpose[]>(["notifications"]);
  const [imap, setImap] = useState(false);
  const start = useMutation({ mutationFn: () => api.mail.googleStart(purposes), onSuccess: (r) => loadPage(r.url) });
  const toggle = (p: MailPurpose) => setPurposes((all) => (all.includes(p) ? all.filter((x) => x !== p) : [...all, p]));
  return (
    <Panel title="Connect a mailbox">
      <div className="flex flex-col gap-4">
        <div className="flex flex-col gap-2">
          <p className="text-body text-ink">Google (Gmail or Google Workspace)</p>
          <p className="text-meta text-muted">
            You sign in to Google and approve only what the chosen uses need: sending, and reading for Support. Syruss can&apos;t delete or change
            anything in the mailbox. Mail goes out from the address you sign in with.
          </p>
          <div className="flex flex-wrap gap-4">
            {PURPOSES.map((p) => (
              <label key={p.key} className="flex items-center gap-1.5 text-body text-ink">
                <input type="checkbox" className="size-4" checked={purposes.includes(p.key)} onChange={() => toggle(p.key)} /> {p.label}
              </label>
            ))}
          </div>
          {o.google ? (
            <div><Button variant="primary" disabled={!purposes.length || start.isPending} onClick={() => start.mutate()}>{start.isPending ? "Opening Google…" : "Set up Google mail"}</Button></div>
          ) : (
            <p className="text-meta text-[var(--pill-warning-ink)]">Google isn&apos;t set up on this server: add GOOGLE_MAIL_CLIENT_ID and GOOGLE_MAIL_CLIENT_SECRET to mail.env, with {o.redirect_uri} as the OAuth client&apos;s redirect URI.</p>
          )}
          <Problem error={start.error} />
        </div>
        <div className="border-t border-rule pt-4">
          {imap ? <ImapForm onDone={() => setImap(false)} /> : <Button size="sm" onClick={() => setImap(true)}>Add a mailbox on another mail server</Button>}
        </div>
      </div>
    </Panel>
  );
}

function ImapForm({ onDone }: { onDone: () => void }) {
  const refresh = useRefresh();
  const [v, setV] = useState<MailImapInput>({ address: "", name: "Syruss", smtp_host: "", smtp_port: 587, smtp_security: "starttls", smtp_username: "",
    smtp_password: "", imap_host: "", imap_port: 993, imap_ssl: true, imap_folder: "INBOX", imap_username: "", imap_password: "" });
  const save = useMutation({ mutationFn: () => api.mail.addImap({ ...v, smtp_host: v.smtp_host || undefined, imap_host: v.imap_host || undefined }),
    onSuccess: () => { refresh(); onDone(); } });
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
      <p className="text-meta font-medium text-muted">Reading (IMAP, read-only), for Support</p>
      <div className="flex flex-wrap gap-3">
        {text("imap_host", "Server")}{text("imap_port", "Port", "number", "w-24")}{text("imap_folder", "Folder", "text", "w-32")}
        {text("imap_username", "Username")}{text("imap_password", "Password", "password")}
      </div>
      <div className="flex gap-2">
        <Button type="submit" size="sm" variant="primary" disabled={!v.address || (!v.smtp_host && !v.imap_host) || save.isPending}>{save.isPending ? "Saving…" : "Add mailbox"}</Button>
        <Button size="sm" onClick={onDone}>Cancel</Button>
      </div>
      <Problem error={save.error} />
    </form>
  );
}

function Deliveries() {
  const refresh = useRefresh();
  const [status, setStatus] = useState("");
  const jobs = useQuery({ queryKey: ["mail", "jobs", status], queryFn: () => api.mail.jobs(status || undefined), refetchInterval: 30_000 });
  const retry = useMutation({ mutationFn: (id: string) => api.mail.retry(id), onSuccess: refresh });
  return (
    <Panel title="Delivery log" aside={
      <select aria-label="Show" value={status} onChange={(e) => setStatus(e.target.value)} className={field}>
        <option value="">Everything</option><option value="dead">Not sent</option><option value="retrying">Trying again</option><option value="sent">Sent</option>
      </select>}>
      {jobs.isError ? <ErrorNote message={jobs.error.message} onRetry={() => jobs.refetch()} /> : !jobs.data ? <Bar className="h-32 w-full" /> : !jobs.data.length ? (
        <Empty title="Nothing yet">Mail the service sends shows here: who it went to, what about, and how it went. The text itself isn&apos;t kept.</Empty>
      ) : (
        <ul className="flex flex-col divide-y divide-rule">
          {jobs.data.map((j) => (
            <li key={j.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2">
              <Pill tone={STATUS[j.status].tone}>{STATUS[j.status].label}</Pill>
              <span className="min-w-0 flex-1 truncate text-body text-ink">{j.subject}</span>
              <span className="text-meta text-muted">{j.to} · {j.purpose}</span>
              <span className="text-meta text-muted">{dateTime(j.created_at)}</span>
              {j.retryable && <Button size="sm" disabled={retry.isPending} onClick={() => retry.mutate(j.id)}>Send again</Button>}
              {j.last_error && j.status !== "sent" && <p className="w-full text-meta text-negative">{j.last_error}{j.attempts > 1 ? ` (${j.attempts} tries)` : ""}</p>}
            </li>
          ))}
        </ul>
      )}
      <Problem error={retry.error} />
    </Panel>
  );
}
