"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pin, PinOff, Trash2 } from "lucide-react";
import Link from "next/link";
import { use, useState } from "react";
import { TrendBars } from "@/components/charts/TrendBars";
import { Fact, ReasonAction } from "@/components/ops/Bits";
import { useStaff } from "@/components/shell/AdminFrame";
import { Bar, Empty, ErrorNote, PageHeader, Panel } from "@/components/ui/Blocks";
import { Button, IconButton } from "@/components/ui/Button";
import { Pill } from "@/components/ui/Pill";
import { api } from "@/lib/api";
import { describeAction } from "@/lib/audit";
import { money } from "@/lib/billing";
import { ago, cn, dateTime, plural } from "@/lib/format";
import type { OrganizationProfile } from "@/lib/types";
import { Problem } from "@/components/ui/Problem";

const STATUS = { active: ["positive", "Open"], suspended: ["negative", "Suspended"], closed: ["neutral", "Closed by its owner"] } as const;

/** One organization on one page: its standing, plan and usage, people, workspaces, money, notes, and what staff did. Never its content. */
export default function OrganizationPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const me = useStaff();
  const queryClient = useQueryClient();
  const page = useQuery({ queryKey: ["console", "organization", id], queryFn: () => api.console.organization(id) });
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["console", "organization", id] });
    queryClient.invalidateQueries({ queryKey: ["accounts"] });
  };

  if (page.isError) return <ErrorNote message={page.error.message} onRetry={() => page.refetch()} />;
  const p = page.data;
  if (!p) return <div className="flex flex-col gap-4"><Bar className="h-10 w-72" /><Bar className="h-32 w-full" /><Bar className="h-64 w-full" /></div>;
  const o = p.organization;
  const [tone, label] = STATUS[o.status];
  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        back={{ href: "/accounts/organizations", label: "Accounts" }}
        title={o.name}
        subtitle={<span className="flex flex-wrap items-center gap-2"><Pill tone={tone}>{label}</Pill><span>{o.slug} · created {dateTime(o.created_at)}</span>{p.agent_paused && <Pill tone="warning">Agent paused</Pill>}</span>}
        actions={
          <div className="flex flex-wrap gap-2">
            <Button asChild><Link href={`/billing/${o.id}`}>Billing</Link></Button>
            {me.can("tenants.manage") && o.status === "active" && (
              <ReasonAction label="Suspend" busyLabel="Suspending…" confirmLabel="Suspend it" variant="danger" onRun={(r) => api.accounts.suspend(o.id, r).then(refresh)} />
            )}
            {me.can("tenants.manage") && o.status === "suspended" && (
              <ReasonAction label="Reactivate" busyLabel="Reactivating…" confirmLabel="Reactivate it" onRun={(r) => api.accounts.reactivate(o.id, r).then(refresh)} />
            )}
            {me.can("operations.manage") && o.status !== "closed" && (
              <ReasonAction label={p.agent_paused ? "Resume the agent" : "Pause the agent"} busyLabel="Saving…"
                confirmLabel={p.agent_paused ? "Resume it" : "Pause it"} variant={p.agent_paused ? "secondary" : "danger"}
                onRun={(r) => api.console.pauseAgent(o.id, !p.agent_paused, r).then(refresh)} />
            )}
          </div>
        }
      />
      {o.status === "suspended" && o.status_reason && <p className="rounded-card bg-[var(--pill-negative-bg)] px-4 py-3 text-body text-[var(--pill-negative-ink)]">Suspended {o.status_changed_at ? ago(o.status_changed_at).toLowerCase() : ""}: {o.status_reason}</p>}

      <Standing p={p} />
      <div className="grid grid-cols-1 gap-6 @5xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div className="flex min-w-0 flex-col gap-6">
          <People p={p} />
          <Workspaces p={p} />
        </div>
        <div className="flex min-w-0 flex-col gap-6">
          <Notes id={o.id} notes={p.notes} me={me.user_id} onDone={refresh} />
          <History p={p} />
        </div>
      </div>
    </div>
  );
}

function Standing({ p }: { p: OrganizationProfile }) {
  const u = p.usage;
  const share = u.pages_limit ? Math.min(1, u.pages_used / u.pages_limit) : 0;
  return (
    <section className="grid grid-cols-1 gap-3 @xl:grid-cols-2 @5xl:grid-cols-4" aria-label="Standing">
      <Panel title="Plan">
        <dl className="grid grid-cols-2 gap-3">
          <Fact label="Plan">{p.plan.name}</Fact>
          <Fact label="Price">{p.plan.price_cents ? `${money(p.plan.price_cents, p.plan.currency)} a month` : "Free"}</Fact>
          <Fact label="Status">{p.plan.status === "past_due" ? <Pill tone="negative">Past due</Pill> : p.plan.status === "trialing" ? `Trial until ${new Date(p.plan.trial_ends_at!).toLocaleDateString()}` : "Active"}</Fact>
          <Fact label="Period ends">{new Date(p.plan.period_end).toLocaleDateString()}</Fact>
        </dl>
      </Panel>
      <Panel title="Pages this period">
        <p className="num text-figure text-ink">{u.pages_used.toLocaleString()} <span className="text-body text-muted">/ {u.pages_limit == null ? "unlimited" : u.pages_limit.toLocaleString()}</span></p>
        {u.pages_limit != null && (
          <span className="mt-2 block h-1.5 rounded-full bg-raised" aria-hidden>
            <span className={cn("block h-full rounded-full", share >= 1 ? "bg-negative" : share >= 0.8 ? "bg-[var(--pill-warning-ink)]" : "bg-accent")} style={{ width: `${Math.max(2, share * 100)}%` }} />
          </span>
        )}
        <p className="mt-2 text-meta text-muted">{u.credits.toLocaleString()} prepaid pages left</p>
      </Panel>
      <Panel title="Money">
        <p className={cn("num text-figure", p.money.overdue ? "text-negative" : "text-ink")}>{money(p.money.owed_cents, p.money.currency)}</p>
        <p className="text-meta text-muted">owed on {plural(p.money.open_invoices, "open invoice")}{p.money.overdue ? `, ${p.money.overdue} overdue` : ""}</p>
      </Panel>
      <TrendBars title="Documents" points={u.documents_30d}
        note={`${u.runs_30d.toLocaleString()} agent runs · $${u.agent_cost_30d_usd.toFixed(2)}${u.last_run_at ? ` · last ${ago(u.last_run_at).toLowerCase()}` : ""}`} />
    </section>
  );
}

function People({ p }: { p: OrganizationProfile }) {
  return (
    <Panel title="People" aside={<span className="num">{p.members.length}</span>} bodyClassName="p-0">
      {!p.members.length ? <div className="p-4"><Empty title="Nobody is in it" /></div> : (
        <ul className="flex flex-col divide-y divide-rule">
          {p.members.map((m) => (
            <li key={m.user_id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5">
              <Link href={`/accounts/people/${m.user_id}`} className="min-w-0 flex-1 truncate text-body text-ink hover:underline">{m.name} <span className="text-muted">· {m.email}</span></Link>
              <Pill tone={m.role === "owner" ? "accent" : "neutral"}>{m.role}</Pill>
              {m.status !== "active" && <Pill tone="negative">{m.status}</Pill>}
              <span className="num text-meta text-muted">{m.last_seen_at ? `seen ${ago(m.last_seen_at).toLowerCase()}` : "never seen"}</span>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function Workspaces({ p }: { p: OrganizationProfile }) {
  return (
    <Panel title="Workspaces" aside={<span className="num">{p.workspaces.length}</span>} bodyClassName="p-0">
      {!p.workspaces.length ? <div className="p-4"><Empty title="No workspaces yet" /></div> : (
        <ul className="flex flex-col divide-y divide-rule">
          {p.workspaces.map((w) => (
            <li key={w.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5">
              <span className="min-w-0 flex-1 truncate text-body text-ink">{w.name}</span>
              <span className="num text-meta text-muted">{plural(w.documents, "document")} · {plural(w.members, "member")}</span>
              <span className="num text-meta text-muted">{w.last_document_at ? `last ${ago(w.last_document_at).toLowerCase()}` : "no documents"}</span>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

/** Staff's own notes about the customer: pinned first. The organization never sees them. */
function Notes({ id, notes, me, onDone }: { id: string; notes: OrganizationProfile["notes"]; me: string; onDone: () => void }) {
  const [body, setBody] = useState("");
  const add = useMutation({ mutationFn: () => api.console.addNote(id, body.trim()), onSuccess: () => { setBody(""); onDone(); } });
  const pin = useMutation({ mutationFn: (noteId: string) => api.console.pinNote(id, noteId), onSuccess: onDone });
  const remove = useMutation({ mutationFn: (noteId: string) => api.console.removeNote(id, noteId), onSuccess: onDone });
  return (
    <Panel title="Staff notes" aside={<span className="text-meta text-muted">Only staff see these</span>}>
      <form className="flex flex-col gap-2" onSubmit={(e) => { e.preventDefault(); if (body.trim().length >= 2) add.mutate(); }}>
        <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={3} maxLength={4000} aria-label="A note for the staff"
          placeholder="A call, a promise, a warning…" className="w-full rounded-control border border-control bg-sheet px-2.5 py-2 text-body text-ink outline-none focus-visible:border-accent" />
        {add.error && <Problem error={add.error} />}
        <div><Button type="submit" size="sm" variant="primary" disabled={body.trim().length < 2 || add.isPending}>Add the note</Button></div>
      </form>
      {notes.length > 0 && (
        <ul className="mt-3 flex flex-col divide-y divide-rule">
          {notes.map((n) => (
            <li key={n.id} className="flex gap-2 py-2.5">
              <div className="min-w-0 flex-1">
                <p className="whitespace-pre-wrap break-words text-body text-ink">{n.body}</p>
                <p className="mt-0.5 text-meta text-muted">{n.pinned && "Pinned · "}{n.author_name} · {ago(n.created_at)}</p>
              </div>
              <IconButton aria-label={n.pinned ? "Unpin" : "Pin to the top"} onClick={() => pin.mutate(n.id)}>{n.pinned ? <PinOff /> : <Pin />}</IconButton>
              {n.author_id === me && <IconButton aria-label="Remove the note" onClick={() => remove.mutate(n.id)}><Trash2 /></IconButton>}
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function History({ p }: { p: OrganizationProfile }) {
  return (
    <Panel title="What staff did" aside={<Link href="/audit" className="hover:text-ink">Audit log</Link>}>
      {!p.staff_actions.length ? <p className="text-body text-muted">Nothing yet.</p> : (
        <ul className="flex flex-col divide-y divide-rule">
          {p.staff_actions.map((a) => (
            <li key={a.id} className="py-2 text-body">
              <span className="font-medium text-ink">{a.actor_name}</span> <span className="text-ink">{describeAction(a)}</span>
              <span className="text-muted">{a.reason ? ` · ${a.reason}` : ""} · {ago(a.created_at)}</span>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
