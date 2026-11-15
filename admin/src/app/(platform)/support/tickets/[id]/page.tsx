"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, FileText, Lock, Mail, Paperclip, User, X } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useMemo, useRef, useState } from "react";
import { field } from "@/components/ops/Bits";
import { useStaff } from "@/components/shell/AdminFrame";
import { CATEGORY, PRIORITY, STATUS, StatusPill, SupportTabs, dueIn, fillReply } from "@/components/support/Bits";
import { Bar, ErrorNote, PageHeader, Panel } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Pill } from "@/components/ui/Pill";
import { Problem } from "@/components/ui/Problem";
import { api } from "@/lib/api";
import { ago, cn, dateTime } from "@/lib/format";
import type { TicketDetail, TicketMessage } from "@/lib/types";

const money = (cents: number, currency: string) => `${(cents / 100).toFixed(2)} ${currency}`;

export default function TicketPage() {
  const { id } = useParams<{ id: string }>();
  const ticket = useQuery({ queryKey: ["support", "ticket", id], queryFn: () => api.support.ticket(id), refetchInterval: 20_000 });
  if (ticket.isError) return <ErrorNote message={ticket.error.message} onRetry={() => ticket.refetch()} />;
  const t = ticket.data;
  return (
    <div className="flex flex-col gap-5">
      <PageHeader back={{ href: "/support", label: "← Support" }} title={t ? `${t.number} · ${t.subject}` : "Ticket"}
        subtitle={t ? `${t.requester_name} <${t.requester_email}> · opened ${ago(t.created_at).toLowerCase()} by ${t.channel === "agent" ? "the assistant, for them" : t.channel === "email" ? "email" : "the Help page"}` : undefined} />
      <SupportTabs />
      {!t ? <Bar className="h-96 w-full" /> : (
        <div className="grid grid-cols-1 gap-5 @5xl:grid-cols-[minmax(0,1fr)_320px]">
          <div className="flex min-w-0 flex-col gap-4">
            <Thread t={t} />
            <Composer t={t} />
          </div>
          <div className="flex flex-col gap-4">
            <Controls t={t} />
            <Context t={t} />
          </div>
        </div>
      )}
    </div>
  );
}

function Thread({ t }: { t: TicketDetail }) {
  return (
    <ol className="flex flex-col gap-3">
      {t.messages.map((m) => <Message key={m.id} t={t} m={m} />)}
    </ol>
  );
}

function Message({ t, m }: { t: TicketDetail; m: TicketMessage }) {
  if (m.author_kind === "system") {
    return <li className="text-center text-meta text-muted">{m.body} · {ago(m.created_at).toLowerCase()}</li>;
  }
  const Icon = m.author_kind === "agent" ? Bot : m.via === "email" ? Mail : User;
  return (
    <li className={cn("rounded-card border px-4 py-3",
      m.internal ? "border-[var(--pill-warning-ink)]/30 bg-[var(--pill-warning-bg)]" : m.author_kind === "staff" ? "border-border bg-raised" : "border-border bg-sheet")}>
      <p className="mb-1.5 flex flex-wrap items-center gap-2 text-meta text-muted">
        <Icon className="size-3.5" aria-hidden />
        <span className="font-medium text-ink">{m.author_name}</span>
        {m.internal && <span className="inline-flex items-center gap-1"><Lock className="size-3" aria-hidden />Internal note</span>}
        {m.author_kind === "staff" && !m.internal && <span>to the customer</span>}
        <span title={dateTime(m.created_at)}>{ago(m.created_at)}</span>
      </p>
      <p className="whitespace-pre-wrap break-words text-body text-ink">{m.body}</p>
      {!!m.files.length && (
        <ul className="mt-2 flex flex-wrap gap-2">
          {m.files.map((f) => (
            <li key={f.id}>
              <a href={api.support.fileUrl(t.id, f.id)} target="_blank" rel="noreferrer noopener"
                className="inline-flex items-center gap-1.5 rounded-control border border-border px-2 py-1 text-meta text-ink hover:bg-raised">
                <FileText className="size-3.5" aria-hidden />{f.filename}
              </a>
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

function Composer({ t }: { t: TicketDetail }) {
  const me = useStaff();
  const queryClient = useQueryClient();
  const [body, setBody] = useState("");
  const [internal, setInternal] = useState(false);
  const [files, setFiles] = useState<File[]>([]);
  const fileInput = useRef<HTMLInputElement>(null);
  const saved = useQuery({ queryKey: ["support", "saved"], queryFn: api.support.savedReplies });
  const canAnswer = me.can("support.respond");
  const values = { first_name: t.requester_name.split(" ")[0] || t.requester_name, ticket: t.number, name: me.name };
  const slash = body.match(/(?:^|\s)\/([a-z0-9-]*)$/);
  const matches = useMemo(() => (slash ? (saved.data ?? []).filter((r) => r.shortcut?.startsWith(slash[1]) || r.title.toLowerCase().includes(slash[1])) : []),
    [slash, saved.data]);
  const send = useMutation({
    mutationFn: (then?: "pending" | "solved") => api.support.answer(t.id, { body: body.trim(), internal, then, files }),
    onSuccess: () => { setBody(""); setFiles([]); queryClient.invalidateQueries({ queryKey: ["support"] }); },
  });
  const insert = (text: string) => setBody((now) => (slash ? now.slice(0, now.length - slash[0].trimStart().length) : now) + fillReply(text, values));

  if (!canAnswer) return <p className="text-meta text-muted">Your role reads tickets; answering them takes the Support or Billing role.</p>;
  if (t.status === "closed") return <p className="text-meta text-muted">This ticket is closed. A customer&apos;s reply opens a new one.</p>;
  return (
    <form className={cn("flex flex-col gap-2 rounded-card border p-3", internal ? "border-[var(--pill-warning-ink)]/40 bg-[var(--pill-warning-bg)]" : "border-border bg-sheet")}
      onSubmit={(e) => { e.preventDefault(); if (body.trim()) send.mutate(undefined); }}>
      <div className="flex flex-wrap items-center gap-2 text-meta">
        <button type="button" onClick={() => setInternal(false)} className={cn("rounded-chip px-2 py-0.5", !internal ? "bg-active font-medium text-ink" : "text-muted")}>Answer the customer</button>
        <button type="button" onClick={() => setInternal(true)} className={cn("rounded-chip px-2 py-0.5", internal ? "bg-active font-medium text-ink" : "text-muted")}>Internal note</button>
        <select aria-label="Insert a saved reply" value="" onChange={(e) => { const r = saved.data?.find((x) => x.id === e.target.value); if (r) insert(r.body); }}
          className={cn(field, "ml-auto h-7")}>
          <option value="">Saved reply…</option>
          {(saved.data ?? []).map((r) => <option key={r.id} value={r.id}>{r.title}{r.shortcut ? `  /${r.shortcut}` : ""}</option>)}
        </select>
      </div>
      <div className="relative">
        <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={6} maxLength={20000}
          placeholder={internal ? "Only staff see notes." : `Write to ${t.requester_name}. It's emailed from the support address and shown in their app. Type / for a saved reply.`}
          className="w-full resize-y rounded-control border border-control bg-sheet px-3 py-2 text-body text-ink outline-none focus-visible:border-accent" />
        {!!matches.length && (
          <ul className="absolute bottom-full left-0 z-10 mb-1 max-h-56 w-72 overflow-y-auto rounded-card border border-border bg-sheet py-1 [box-shadow:var(--shadow-card)]">
            {matches.map((r) => (
              <li key={r.id}><button type="button" onClick={() => insert(r.body)} className="w-full px-3 py-1.5 text-left text-body hover:bg-raised">
                {r.title} {r.shortcut && <span className="text-meta text-muted">/{r.shortcut}</span>}
              </button></li>
            ))}
          </ul>
        )}
      </div>
      {!!files.length && (
        <ul className="flex flex-wrap gap-2">
          {files.map((f, n) => (
            <li key={`${f.name}-${n}`} className="inline-flex items-center gap-1 rounded-control border border-border px-2 py-0.5 text-meta">
              {f.name}<button type="button" aria-label={`Remove ${f.name}`} onClick={() => setFiles(files.filter((_, k) => k !== n))}><X className="size-3" /></button>
            </li>
          ))}
        </ul>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <input ref={fileInput} type="file" multiple className="sr-only" accept="application/pdf,image/jpeg,image/png,image/webp"
          onChange={(e) => { setFiles([...files, ...Array.from(e.target.files ?? [])].slice(0, 5)); e.target.value = ""; }} />
        <Button type="button" size="sm" onClick={() => fileInput.current?.click()}><Paperclip className="size-3.5" />Attach</Button>
        <span className="flex-1" />
        {!internal && <Button type="button" size="sm" disabled={!body.trim() || send.isPending} onClick={() => send.mutate("solved")}>Send and mark solved</Button>}
        <Button type="submit" size="sm" variant="primary" disabled={!body.trim() || send.isPending}>{send.isPending ? "Sending…" : internal ? "Add note" : "Send"}</Button>
      </div>
      <Problem error={send.error} />
    </form>
  );
}

function Controls({ t }: { t: TicketDetail }) {
  const me = useStaff();
  const queryClient = useQueryClient();
  const router = useRouter();
  const people = useQuery({ queryKey: ["support", "staff"], queryFn: api.support.staff });
  const [into, setInto] = useState("");
  const change = useMutation({ mutationFn: (body: Record<string, string>) => api.support.change(t.id, body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["support"] }) });
  const merge = useMutation({ mutationFn: () => api.support.merge(t.id, into.trim().toUpperCase()),
    onSuccess: (kept) => { queryClient.invalidateQueries({ queryKey: ["support"] }); router.push(`/support/tickets/${kept.id}`); } });
  const can = me.can("support.respond");
  const due = t.status === "new" || t.status === "open" ? dueIn(t.first_response_due ?? t.resolve_due) : null;
  return (
    <Panel title="Ticket">
      <div className="flex flex-col gap-2.5">
        <div className="flex flex-wrap items-center gap-2">
          <StatusPill status={t.status} />
          {t.unverified && <Pill tone="warning">Unverified sender</Pill>}
          {due && <span className={cn("text-meta", due.late ? "text-negative" : "text-muted")}>{due.text}</span>}
        </div>
        {([
          ["Status", "status", t.status, Object.entries(STATUS).map(([v, s]) => [v, s.label])],
          ["Priority", "priority", t.priority, Object.entries(PRIORITY).map(([v, s]) => [v, s.label])],
          ["Category", "category", t.category, Object.entries(CATEGORY)],
        ] as [string, string, string, string[][]][]).map(([label, key, value, options]) => (
          <label key={key} className="flex items-center justify-between gap-2 text-meta text-muted">
            {label}
            <select value={value} disabled={!can || change.isPending} onChange={(e) => change.mutate({ [key]: e.target.value })} className={cn(field, "w-44")}>
              {options.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
          </label>
        ))}
        <label className="flex items-center justify-between gap-2 text-meta text-muted">
          Assigned to
          <select value={t.assignee_id ?? "none"} disabled={!can || change.isPending} onChange={(e) => change.mutate({ assignee_id: e.target.value })} className={cn(field, "w-44")}>
            <option value="none">Nobody</option>
            {t.assignee_id && !(people.data ?? []).some((p) => p.id === t.assignee_id) && <option value={t.assignee_id}>{t.assignee_name}</option>}
            {(people.data ?? []).map((p) => <option key={p.id} value={p.id}>{p.id === me.user_id ? `${p.name} (me)` : p.name}</option>)}
          </select>
        </label>
        {can && t.status !== "closed" && (
          <form className="flex items-center gap-2 border-t border-rule pt-2.5" onSubmit={(e) => { e.preventDefault(); if (into.trim()) merge.mutate(); }}>
            <input value={into} onChange={(e) => setInto(e.target.value)} placeholder="T-1042" aria-label="Merge into ticket" className={cn(field, "w-24")} />
            <Button type="submit" size="sm" disabled={!into.trim() || merge.isPending}>Merge into it</Button>
          </form>
        )}
        <Problem error={change.error ?? merge.error} />
      </div>
    </Panel>
  );
}

function Context({ t }: { t: TicketDetail }) {
  const c = t.context;
  return (
    <Panel title="Customer">
      <div className="flex flex-col gap-3 text-body">
        {c.person ? (
          <div>
            <Link href={`/accounts/people/${c.person.id}`} className="font-medium text-ink hover:underline">{c.person.name}</Link>
            <p className="text-meta text-muted">{c.person.email}{c.person.last_seen_at && ` · seen ${ago(c.person.last_seen_at).toLowerCase()}`}</p>
          </div>
        ) : <p className="text-meta text-muted">{t.requester_email}: no Syruss account{t.unverified ? " (unverified sender)" : ""}.</p>}
        {c.organization && (
          <div>
            <Link href={`/accounts/organizations/${c.organization.id}`} className="font-medium text-ink hover:underline">{c.organization.name}</Link>
            <p className="text-meta text-muted">{c.organization.plan ?? "No plan"} · {c.organization.status}</p>
          </div>
        )}
        {!!c.bills.length && (
          <div>
            <p className="mb-1 text-meta font-medium text-muted">Recent bills</p>
            <ul className="flex flex-col gap-1">
              {c.bills.map((b) => (
                <li key={b.id} className="flex items-center justify-between gap-2 text-meta">
                  <span className="num text-ink">{b.number}</span>
                  <span className="text-muted">{b.status}{b.due_cents > 0 ? ` · ${money(b.due_cents, b.currency)} due` : ""}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
        {!!c.other_tickets.length && (
          <div>
            <p className="mb-1 text-meta font-medium text-muted">Other tickets</p>
            <ul className="flex flex-col gap-1">
              {c.other_tickets.map((o) => (
                <li key={o.id} className="flex items-center justify-between gap-2 text-meta">
                  <Link href={`/support/tickets/${o.id}`} className="min-w-0 truncate text-ink hover:underline">{o.number} {o.subject}</Link>
                  <StatusPill status={o.status} />
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </Panel>
  );
}
