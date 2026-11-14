"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useStaff } from "@/components/shell/AdminFrame";
import { Bar, ErrorNote, PageHeader, Panel } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Pill } from "@/components/ui/Pill";
import { api } from "@/lib/api";
import { ago, cn } from "@/lib/format";
import type { StaffMember, StaffRole } from "@/lib/types";

const ROLES: StaffRole[] = ["owner", "operator", "support", "billing", "viewer"];
const title = (r: string) => r.charAt(0).toUpperCase() + r.slice(1);
const field = "h-8 rounded-control border border-control bg-sheet px-2.5 text-body text-ink outline-none focus-visible:border-accent disabled:opacity-60";

export default function StaffPage() {
  const me = useStaff();
  const manage = me.can("staff.manage");
  const staff = useQuery({ queryKey: ["staff"], queryFn: api.staff.list });
  const queryClient = useQueryClient();
  const done = () => {
    queryClient.invalidateQueries({ queryKey: ["staff"] });
    queryClient.invalidateQueries({ queryKey: ["audit"] });
  };

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Platform staff"
        subtitle={manage ? "Who can use these tools, and what each role may do. Changes ask you to sign in again first." : "Who can use these tools. Only an owner changes this list."}
      />
      {staff.isError && <ErrorNote message="Couldn't load the staff." onRetry={() => staff.refetch()} />}
      {manage && <AddStaff onDone={done} />}

      <Panel title="On the staff" aside={staff.data ? <span className="num">{staff.data.items.length}</span> : undefined} bodyClassName="p-0">
        {!staff.data ? (
          <div className="flex flex-col gap-3 p-4">{[0, 1, 2].map((i) => <Bar key={i} className="h-5 w-full" />)}</div>
        ) : (
          <ul className="flex flex-col divide-y divide-rule">
            {staff.data.items.map((m) => <Row key={m.user_id} member={m} editable={manage && !m.from_config && m.user_id !== me.user_id} self={m.user_id === me.user_id} onDone={done} />)}
          </ul>
        )}
      </Panel>

      <Panel title="Roles">
        <dl className="grid grid-cols-1 gap-3 @2xl:grid-cols-2">
          {ROLES.map((r) => (
            <div key={r}>
              <dt className="text-body font-medium text-ink">{title(r)}</dt>
              <dd className="text-meta text-muted">{me.roles[r]}</dd>
            </div>
          ))}
        </dl>
      </Panel>
    </div>
  );
}

function AddStaff({ onDone }: { onDone: () => void }) {
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<StaffRole>("viewer");
  const [reason, setReason] = useState("");
  const add = useMutation({
    mutationFn: () => api.staff.add({ email: email.trim(), role, reason: reason.trim() || undefined }),
    onSuccess: () => {
      setEmail("");
      setReason("");
      onDone();
    },
  });
  return (
    <Panel title="Add someone">
      <form className="flex flex-col gap-3 @3xl:flex-row @3xl:items-end" onSubmit={(e) => { e.preventDefault(); if (email.trim()) add.mutate(); }}>
        <label className="flex min-w-0 flex-1 flex-col gap-1 text-meta text-muted">
          Email of their Syruss account
          <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} className={field} placeholder="name@company.com" autoComplete="off" />
        </label>
        <label className="flex flex-col gap-1 text-meta text-muted">
          Role
          <select value={role} onChange={(e) => setRole(e.target.value as StaffRole)} className={field}>
            {ROLES.map((r) => <option key={r} value={r}>{title(r)}</option>)}
          </select>
        </label>
        <label className="flex min-w-0 flex-1 flex-col gap-1 text-meta text-muted">
          Why (kept in the audit log)
          <input value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500} className={field} />
        </label>
        <Button type="submit" variant="primary" disabled={!email.trim() || add.isPending}>{add.isPending ? "Adding…" : "Add to staff"}</Button>
      </form>
      {add.error && <p className="mt-2 text-meta text-negative">{add.error.message}</p>}
      <p className="mt-2 text-meta text-muted">They need a Syruss account already. They sign in to this app with their password and a one-time code.</p>
    </Panel>
  );
}

function Row({ member: m, editable, self, onDone }: { member: StaffMember; editable: boolean; self: boolean; onDone: () => void }) {
  const [removing, setRemoving] = useState(false);
  const [reason, setReason] = useState("");
  const change = useMutation({ mutationFn: (role: StaffRole) => api.staff.changeRole(m.user_id, { role }), onSuccess: onDone });
  const remove = useMutation({ mutationFn: () => api.staff.remove(m.user_id, reason.trim() || undefined), onSuccess: onDone });
  const error = change.error ?? remove.error;

  return (
    <li className="flex flex-col gap-2 px-4 py-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <div className="min-w-0 flex-1">
          <p className="flex items-center gap-2 text-body text-ink">
            <span className="truncate font-medium">{m.name}</span>
            {self && <Pill>You</Pill>}
            {m.from_config && <Pill tone="accent">Set by the deployment</Pill>}
            {m.status !== "active" && <Pill tone="negative">{m.status === "disabled" ? "Account disabled" : "No account yet"}</Pill>}
          </p>
          <p className="truncate text-meta text-muted">
            {m.email ?? m.user_id}
            {m.added_at && ` · added ${ago(m.added_at).toLowerCase()}`}
            {m.last_seen_at && ` · last seen ${ago(m.last_seen_at).toLowerCase()}`}
          </p>
        </div>
        {editable ? (
          <select aria-label={`Role of ${m.name}`} value={m.role} disabled={change.isPending} onChange={(e) => change.mutate(e.target.value as StaffRole)} className={field}>
            {ROLES.map((r) => <option key={r} value={r}>{title(r)}</option>)}
          </select>
        ) : (
          <Pill tone={m.role === "owner" ? "accent" : "neutral"}>{title(m.role)}</Pill>
        )}
        {editable && !removing && <Button variant="danger" size="sm" onClick={() => setRemoving(true)}>Remove</Button>}
      </div>
      {removing && (
        <form className={cn("flex flex-col gap-2 rounded-control bg-raised p-3 @2xl:flex-row @2xl:items-center")} onSubmit={(e) => { e.preventDefault(); remove.mutate(); }}>
          <input value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500} placeholder="Why (kept in the audit log)" aria-label="Why" className={cn(field, "min-w-0 flex-1")} autoFocus />
          <div className="flex gap-2">
            <Button type="submit" variant="danger" size="sm" disabled={remove.isPending}>{remove.isPending ? "Removing…" : `Remove ${m.name}`}</Button>
            <Button size="sm" onClick={() => setRemoving(false)}>Cancel</Button>
          </div>
        </form>
      )}
      {error && <p className="text-meta text-negative">{error.message}</p>}
    </li>
  );
}
