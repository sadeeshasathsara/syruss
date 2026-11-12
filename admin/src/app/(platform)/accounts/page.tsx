"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { ReasonAction, field } from "@/components/ops/Bits";
import { useStaff } from "@/components/shell/AdminFrame";
import { Bar, Empty, ErrorNote, PageHeader, Panel } from "@/components/ui/Blocks";
import { Pill } from "@/components/ui/Pill";
import { api } from "@/lib/api";
import { ago, cn } from "@/lib/format";
import type { OrganizationAccount, PersonAccount } from "@/lib/types";

const ORG_TONE = { active: "positive", suspended: "negative", closed: "neutral" } as const;

/**
 * Organizations and people: finding them, and the two switches that stop harm quickly. Suspending shuts an organization
 * for everyone in it; disabling stops one person signing in to anything. Neither removes or opens any content.
 */
export default function AccountsPage() {
  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="Accounts" subtitle="Find an organization or a person. Suspending and disabling ask for a reason and a recent sign-in, and are written to the audit log." />
      <Organizations />
      <People />
    </div>
  );
}

function Organizations() {
  const me = useStaff();
  const [q, setQ] = useState("");
  const orgs = useQuery({ queryKey: ["accounts", "organizations", q], queryFn: () => api.accounts.organizations(q || undefined) });
  const queryClient = useQueryClient();
  const done = () => queryClient.invalidateQueries({ queryKey: ["accounts", "organizations"] });
  return (
    <Panel title="Organizations" bodyClassName="p-0">
      <div className="p-4 pb-2">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Find an organization by name or address" aria-label="Find an organization" className={cn(field, "w-full @2xl:w-80")} />
      </div>
      {orgs.isError ? <div className="p-4"><ErrorNote message="Couldn't load organizations." onRetry={() => orgs.refetch()} /></div>
        : !orgs.data ? <div className="flex flex-col gap-3 p-4">{[0, 1, 2].map((i) => <Bar key={i} className="h-5 w-full" />)}</div>
        : !orgs.data.length ? <div className="p-4"><Empty title="No organization matches" /></div>
        : <ul className="flex flex-col divide-y divide-rule">{orgs.data.map((o) => <OrganizationRow key={o.id} o={o} manage={me.can("tenants.manage")} onDone={done} />)}</ul>}
    </Panel>
  );
}

function OrganizationRow({ o, manage, onDone }: { o: OrganizationAccount; manage: boolean; onDone: () => void }) {
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-3">
      <div className="flex min-w-0 flex-1 flex-col">
        <Link href={`/billing/${o.id}`} className="truncate text-body font-medium text-ink hover:underline">{o.name}</Link>
        <span className="truncate text-meta text-muted">
          {o.slug} · {o.members} {o.members === 1 ? "person" : "people"} · {o.workspaces} workspace{o.workspaces === 1 ? "" : "s"}
          {o.status !== "active" && o.status_changed_at ? ` · ${o.status} ${ago(o.status_changed_at).toLowerCase()}` : ""}
          {o.purged_at ? " · its data was removed" : ""}
        </span>
        {o.status === "suspended" && o.status_reason && <span className="text-meta text-muted">Why: {o.status_reason}</span>}
      </div>
      <Pill tone={ORG_TONE[o.status]}>{o.status === "active" ? "Open" : o.status === "suspended" ? "Suspended" : "Closed by its owner"}</Pill>
      {manage && o.status === "active" && (
        <ReasonAction label="Suspend" busyLabel="Suspending…" confirmLabel="Suspend it" variant="danger"
          onRun={(reason) => api.accounts.suspend(o.id, reason).then(onDone)} />
      )}
      {manage && o.status === "suspended" && (
        <ReasonAction label="Reactivate" busyLabel="Reactivating…" confirmLabel="Reactivate it"
          onRun={(reason) => api.accounts.reactivate(o.id, reason).then(onDone)} />
      )}
    </li>
  );
}

function People() {
  const me = useStaff();
  const [q, setQ] = useState("");
  const ready = q.trim().length >= 2;
  const people = useQuery({ queryKey: ["accounts", "people", q.trim()], queryFn: () => api.accounts.people(q.trim()), enabled: ready });
  const queryClient = useQueryClient();
  const done = () => queryClient.invalidateQueries({ queryKey: ["accounts", "people"] });
  return (
    <Panel title="People" bodyClassName="p-0">
      <div className="p-4 pb-2">
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Find a person by name or email" aria-label="Find a person" className={cn(field, "w-full @2xl:w-80")} />
      </div>
      {!ready ? <p className="px-4 pb-4 text-body text-muted">Type at least two letters of a name or an email address.</p>
        : people.isError ? <div className="p-4"><ErrorNote message="Couldn't look that up." onRetry={() => people.refetch()} /></div>
        : !people.data ? <div className="flex flex-col gap-3 p-4">{[0, 1].map((i) => <Bar key={i} className="h-5 w-full" />)}</div>
        : !people.data.length ? <div className="p-4"><Empty title="Nobody matches" /></div>
        : <ul className="flex flex-col divide-y divide-rule">{people.data.map((p) => <PersonRow key={p.id} p={p} manage={me.can("users.manage") && p.id !== me.user_id} onDone={done} />)}</ul>}
    </Panel>
  );
}

function PersonRow({ p, manage, onDone }: { p: PersonAccount; manage: boolean; onDone: () => void }) {
  return (
    <li className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-3">
      <div className="flex min-w-0 flex-1 flex-col">
        <span className="truncate text-body font-medium text-ink">{p.name}</span>
        <span className="truncate text-meta text-muted">{p.email}{p.last_seen_at ? ` · seen ${ago(p.last_seen_at).toLowerCase()}` : ""}</span>
        <span className="truncate text-meta text-muted">
          {p.organizations.length ? p.organizations.map((o) => `${o.name} (${o.role})`).join(", ") : "In no organization"}
        </span>
      </div>
      {p.is_staff && <Pill tone="accent">Platform staff</Pill>}
      <Pill tone={p.status === "active" ? "positive" : "negative"}>{p.status === "active" ? "Active" : p.status === "disabled" ? "Disabled" : "Deleted"}</Pill>
      {manage && p.status === "active" && (
        <ReasonAction label="Disable" busyLabel="Disabling…" confirmLabel="Disable the account" variant="danger"
          onRun={(reason) => api.accounts.disable(p.id, reason).then(onDone)} />
      )}
      {manage && p.status === "disabled" && (
        <ReasonAction label="Enable" busyLabel="Enabling…" confirmLabel="Enable the account"
          onRun={(reason) => api.accounts.enable(p.id, reason).then(onDone)} />
      )}
    </li>
  );
}
