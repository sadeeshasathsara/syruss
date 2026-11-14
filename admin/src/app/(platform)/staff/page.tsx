"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useStaff } from "@/components/shell/AdminFrame";
import { Bar, ErrorNote, PageHeader, Panel } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Pill } from "@/components/ui/Pill";
import { api } from "@/lib/api";
import { ago, cn } from "@/lib/format";
import type { StaffMember, StaffPolicy, StaffRole } from "@/lib/types";
import { Problem } from "@/components/ui/Problem";

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
      <AddingStaff />
      <Invitations manage={manage} onDone={done} />

      <Panel title="On the staff" aside={staff.data ? <span className="num">{staff.data.items.length}{dueCount(staff.data.items) ? ` · ${dueCount(staff.data.items)} to review` : ""}</span> : undefined} bodyClassName="p-0">
        {!staff.data ? (
          <div className="flex flex-col gap-3 p-4">{[0, 1, 2].map((i) => <Bar key={i} className="h-5 w-full" />)}</div>
        ) : (
          <ul className="flex flex-col divide-y divide-rule">
            {staff.data.items.map((m) => <Row key={m.user_id} member={m} editable={manage && !m.from_config && m.user_id !== me.user_id} self={m.user_id === me.user_id} onDone={done} />)}
          </ul>
        )}
      </Panel>

      {staff.data && <AccessPolicy policy={staff.data.policy} manage={manage} onDone={done} />}

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

const dueCount = (items: StaffMember[]) => items.filter((m) => m.review_due).length;
const day = (iso: string) => new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });

/** How unused access is taken away, and how often owners confirm who still needs it. */
function AccessPolicy({ policy, manage, onDone }: { policy: StaffPolicy; manage: boolean; onDone: () => void }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(policy);
  const [reason, setReason] = useState("");
  const save = useMutation({
    mutationFn: () => api.staff.setPolicy({ ...draft, reason: reason.trim() }),
    onSuccess: () => { setEditing(false); setReason(""); onDone(); },
  });
  const number = (key: keyof StaffPolicy, label: string, max: number) => (
    <label className="flex flex-col gap-1 text-meta text-muted">
      {label}
      <input type="number" min={0} max={max} value={draft[key]} onChange={(e) => setDraft({ ...draft, [key]: Number(e.target.value) })} className={cn(field, "w-28")} />
    </label>
  );
  return (
    <Panel title="Access that isn't used or needed">
      <div className="flex flex-col gap-3">
        <p className="text-body text-muted">
          {policy.inactive_days
            ? `Someone who doesn't use this app for ${policy.inactive_days} days is taken off the staff, after a warning ${policy.warn_days} days before.`
            : "Unused access is never taken away automatically."}{" "}
          {policy.review_days
            ? `Every ${policy.review_days} days an owner confirms each person still needs their access; owners are reminded while any review is due.`
            : "Access reviews are off."}{" "}
          The deployment&apos;s own owners aren&apos;t affected, and the last owner is never removed.
        </p>
        {manage && !editing && <div><Button size="sm" onClick={() => { setDraft(policy); setEditing(true); }}>Change…</Button></div>}
        {editing && (
          <form className="flex flex-col gap-3" onSubmit={(e) => { e.preventDefault(); if (reason.trim().length >= 5) save.mutate(); }}>
            <div className="flex flex-wrap gap-3">
              {number("inactive_days", "Days without use (0: never)", 365)}
              {number("warn_days", "Warn this many days before", 30)}
              {number("review_days", "Review every (days, 0: never)", 365)}
            </div>
            <label className="flex flex-col gap-1 text-meta text-muted">
              Why (kept in the audit log)
              <input value={reason} onChange={(e) => setReason(e.target.value)} minLength={5} maxLength={500} required className={field} />
            </label>
            <div className="flex gap-2">
              <Button type="submit" size="sm" variant="primary" disabled={reason.trim().length < 5 || save.isPending}>{save.isPending ? "Saving…" : "Save"}</Button>
              <Button size="sm" onClick={() => { setEditing(false); save.reset(); }}>Cancel</Button>
            </div>
          </form>
        )}
        <Problem error={save.error} />
      </div>
    </Panel>
  );
}

/** Nobody is added from this app: an operator adds staff on the server, which also makes their sign-in. */
function AddingStaff() {
  return (
    <Panel title="Adding someone">
      <p className="text-body text-muted">
        People are added to the staff from the server, not from this app, and this app&apos;s sign-in page creates no accounts. On the
        server, run:
      </p>
      <pre className="mt-2 overflow-x-auto rounded-control bg-raised px-3 py-2 font-mono text-meta text-ink">syruss staff add name@company.com --role support</pre>
      <p className="mt-2 text-meta text-muted">
        In production: <span className="font-mono">docker compose exec -e KEYCLOAK_URL=http://keycloak:8080 -e KEYCLOAK_ADMIN -e KEYCLOAK_ADMIN_PASSWORD server python -m app.invite_staff name@company.com support</span>.
        It makes their sign-in if they have none (Keycloak emails them a link to choose a password and set up a one-time code) and emails the
        invitation, which they accept here. A staff account works only in this app: if the address already has a customer account, they&apos;re
        shown what accepting would delete first. Running it again sends both again.
      </p>
    </Panel>
  );
}

function Invitations({ manage, onDone }: { manage: boolean; onDone: () => void }) {
  const list = useQuery({ queryKey: ["staff", "invitations"], queryFn: api.staff.invitations });
  const revoke = useMutation({ mutationFn: (id: string) => api.staff.revokeInvitation(id), onSuccess: onDone });
  if (!list.data?.length) return null;
  return (
    <Panel title="Invitations waiting" aside={<span className="num">{list.data.length}</span>} bodyClassName="p-0">
      <ul className="flex flex-col divide-y divide-rule">
        {list.data.map((i) => (
          <li key={i.id} className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-4 py-2.5">
            <div className="min-w-0 flex-1">
              <p className="truncate text-body text-ink">{i.email} <span className="text-muted">as {title(i.role)}</span></p>
              <p className="truncate text-meta text-muted">
                by {i.invited_by_name} · {i.email_sent_at ? `emailed ${ago(i.email_sent_at).toLowerCase()}` : "not emailed yet"}
                {i.status === "expired" ? " · expired" : ` · valid until ${new Date(i.expires_at).toLocaleDateString()}`}
              </p>
            </div>
            {i.status === "expired" && <Pill tone="warning">Expired</Pill>}
            {manage && <Button size="sm" variant="danger" onClick={() => revoke.mutate(i.id)} disabled={revoke.isPending}>Withdraw</Button>}
          </li>
        ))}
      </ul>
      {revoke.error && <Problem error={revoke.error} className="px-4 pb-3" />}
    </Panel>
  );
}

function Row({ member: m, editable, self, onDone }: { member: StaffMember; editable: boolean; self: boolean; onDone: () => void }) {
  const [removing, setRemoving] = useState(false);
  const [reason, setReason] = useState("");
  const change = useMutation({ mutationFn: (role: StaffRole) => api.staff.changeRole(m.user_id, { role }), onSuccess: onDone });
  const remove = useMutation({ mutationFn: () => api.staff.remove(m.user_id, reason.trim() || undefined), onSuccess: onDone });
  const review = useMutation({ mutationFn: () => api.staff.review(m.user_id), onSuccess: onDone });
  const error = change.error ?? remove.error ?? review.error;

  return (
    <li className="flex flex-col gap-2 px-4 py-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <div className="min-w-0 flex-1">
          <p className="flex items-center gap-2 text-body text-ink">
            <span className="truncate font-medium">{m.name}</span>
            {self && <Pill>You</Pill>}
            {m.from_config && <Pill tone="accent">Set by the deployment</Pill>}
            {m.status !== "active" && <Pill tone="negative">{m.status === "disabled" ? "Account disabled" : "No account yet"}</Pill>}
            {m.review_due && <Pill tone="warning">Review due</Pill>}
          </p>
          <p className="truncate text-meta text-muted">
            {m.email ?? m.user_id}
            {m.added_at && ` · added ${ago(m.added_at).toLowerCase()}`}
            {m.last_active_at ? ` · used this app ${ago(m.last_active_at).toLowerCase()}` : m.last_seen_at ? ` · last seen ${ago(m.last_seen_at).toLowerCase()}` : ""}
          </p>
          {!m.from_config && (m.reviewed_at || m.removal_at) && (
            <p className="truncate text-meta text-muted">
              {m.reviewed_at ? `Access confirmed ${day(m.reviewed_at)} by ${m.reviewed_by_name}` : "Access not confirmed yet"}
              {m.review_due_at && !m.review_due ? ` · next review ${day(m.review_due_at)}` : ""}
              {m.removal_at ? ` · removed ${day(m.removal_at)} unless used` : ""}
            </p>
          )}
        </div>
        {editable && m.review_due && (
          <Button size="sm" onClick={() => review.mutate()} disabled={review.isPending}>{review.isPending ? "Confirming…" : "Still needs access"}</Button>
        )}
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
      {error && <Problem error={error} />}
    </li>
  );
}
