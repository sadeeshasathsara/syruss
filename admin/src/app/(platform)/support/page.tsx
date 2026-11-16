"use client";

import { useQuery } from "@tanstack/react-query";
import { Mail, MessageSquare, Bot, Search } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { field } from "@/components/ops/Bits";
import { CATEGORY, PriorityPill, StatusPill, dueIn } from "@/components/support/Bits";
import { Bar, Empty, ErrorNote, PageHeader } from "@/components/ui/Blocks";
import { Pill } from "@/components/ui/Pill";
import { api } from "@/lib/api";
import { ago, cn } from "@/lib/format";
import type { Ticket } from "@/lib/types";
import { Select } from "@/components/ui/Select";

const CHANNEL = { email: Mail, app: MessageSquare, agent: Bot };

/** The support inbox: what needs an answer first (late, then urgent, then longest waiting). */
export default function SupportPage() {
  const [status, setStatus] = useState("active");
  const [assignee, setAssignee] = useState("");
  const [priority, setPriority] = useState("");
  const [category, setCategory] = useState("");
  const [late, setLate] = useState(false);
  const [q, setQ] = useState("");
  const summary = useQuery({ queryKey: ["support", "summary"], queryFn: api.support.summary, refetchInterval: 30_000 });
  const list = useQuery({
    queryKey: ["support", "tickets", status, assignee, priority, category, late, q],
    queryFn: () => api.support.tickets({ status, assignee: assignee || undefined, priority: priority || undefined, category: category || undefined,
      breached: late ? "true" : undefined, q: q.trim() || undefined }),
    refetchInterval: 30_000,
  });
  const s = summary.data;

  return (
    <div className="flex flex-col gap-5">
      <PageHeader title="Support" subtitle="Tickets from customers: from their chat with the assistant, the Help page, and the support email address." />
      {s && (
        <div className="flex flex-wrap gap-2 text-meta">
          {[["New", s.new, () => { setStatus("new"); setAssignee(""); }], ["Unassigned", s.unassigned, () => { setStatus("active"); setAssignee("none"); }],
            ["Mine", s.mine, () => { setStatus("active"); setAssignee("me"); }], ["Late", s.breached, () => { setStatus("active"); setLate(true); }]].map(([label, n, go]) => (
            <button key={label as string} type="button" onClick={go as () => void}
              className={cn("rounded-chip border px-2.5 py-1", label === "Late" && (n as number) > 0 ? "border-[var(--pill-negative-ink)] text-[var(--pill-negative-ink)]" : "border-border text-muted hover:text-ink")}>
              {label as string} <span className="num font-medium">{n as number}</span>
            </button>
          ))}
        </div>
      )}
      <div className="flex flex-wrap items-end gap-2">
        <label className="relative min-w-0 flex-1">
          <Search className="pointer-events-none absolute left-2.5 top-2 size-4 text-muted" aria-hidden />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search number, subject or email" aria-label="Search" className={cn(field, "w-full pl-8")} />
        </label>
        <Select aria-label="Status" value={status} onValueChange={(v) => setStatus(v)}>
          {[["active", "Needs work"], ["new", "New"], ["open", "Open"], ["pending", "Waiting for customer"], ["solved", "Solved"], ["closed", "Closed"], ["all", "All"]]
            .map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </Select>
        <Select aria-label="Assignee" value={assignee} onValueChange={(v) => setAssignee(v)}>
          <option value="">Anyone</option><option value="me">Me</option><option value="none">Unassigned</option>
        </Select>
        <Select aria-label="Priority" value={priority} onValueChange={(v) => setPriority(v)}>
          <option value="">Any priority</option>{["urgent", "high", "normal", "low"].map((p) => <option key={p} value={p}>{p[0].toUpperCase() + p.slice(1)}</option>)}
        </Select>
        <Select aria-label="Category" value={category} onValueChange={(v) => setCategory(v)}>
          <option value="">Any category</option>{Object.entries(CATEGORY).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
        </Select>
        <label className="flex h-8 items-center gap-1.5 text-body text-muted">
          <input type="checkbox" checked={late} onChange={(e) => setLate(e.target.checked)} className="size-4" /> Late only
        </label>
      </div>
      {list.isError ? <ErrorNote message={list.error.message} onRetry={() => list.refetch()} /> : !list.data ? <Bar className="h-48 w-full" /> : !list.data.items.length ? (
        <Empty title="Nothing here">No tickets match. New ones arrive from customers&apos; chats, the Help page and email.</Empty>
      ) : (
        <ul className="flex flex-col divide-y divide-rule overflow-hidden rounded-card border border-border bg-sheet">
          {list.data.items.map((t) => <Row key={t.id} t={t} />)}
        </ul>
      )}
    </div>
  );
}

function Row({ t }: { t: Ticket }) {
  const Icon = CHANNEL[t.channel];
  const due = t.status === "new" || t.status === "open" ? dueIn(t.first_response_due ?? t.resolve_due) : null;
  return (
    <li>
      <Link href={`/support/tickets/${t.id}`} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3 hover:bg-raised">
        <Icon className="size-4 flex-none text-muted" aria-label={t.channel} />
        <span className="num w-16 flex-none text-meta text-muted">{t.number}</span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-body font-medium text-ink">{t.subject}</span>
          <span className="block truncate text-meta text-muted">
            {t.requester_name} · {t.organization ?? (t.unverified ? "unknown sender" : "no organization")} · {CATEGORY[t.category]}
            {t.last_customer_at && ` · wrote ${ago(t.last_customer_at).toLowerCase()}`}
          </span>
        </span>
        {t.unverified && <Pill tone="warning">Unverified</Pill>}
        <PriorityPill priority={t.priority} />
        <StatusPill status={t.status} />
        {due && <span className={cn("text-meta", due.late || t.breached_at ? "text-negative" : "text-muted")}>{due.text}</span>}
        <span className="w-28 flex-none truncate text-right text-meta text-muted">{t.assignee_name ?? "Unassigned"}</span>
      </Link>
    </li>
  );
}
