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
      {manage && <InviteStaff onDone={done} />}
      <Invitations manage={manage} onDone={done} />

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

/** Inviting anyone by email: they need no account yet. The invitation is always emailed (and its link shown here too). */
function InviteStaff({ onDone }: { onDone: () => void }) {
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<StaffRole>("viewer");
  const [reason, setReason] = useState("");
  const [sent, setSent] = useState<{ email: string; ok: boolean; link: string } | null>(null);
  const invite = useMutation({
    mutationFn: () => api.staff.invite({ email: email.trim(), role, reason: reason.trim() }),
    onSuccess: (r) => {
      setSent({ email: r.email, ok: r.email_sent, link: r.link });
      setEmail("");
      setReason("");
      onDone();
    },
  });
  return (
    <Panel title="Invite to the staff">
      <form className="flex flex-col gap-3 @3xl:flex-row @3xl:items-end" onSubmit={(e) => { e.preventDefault(); if (email.trim() && reason.trim().length >= 5) invite.mutate(); }}>
        <label className="flex min-w-0 flex-1 flex-col gap-1 text-meta text-muted">
          Their email
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
          <input value={reason} onChange={(e) => setReason(e.target.value)} minLength={5} maxLength={500} required className={field} />
        </label>
        <Button type="submit" variant="primary" disabled={!email.trim() || reason.trim().length < 5 || invite.isPending}>{invite.isPending ? "Inviting…" : "Send the invitation"}</Button>
      </form>
      {invite.error && <p className="mt-2 text-meta text-negative">{invite.error.message}</p>}
      {sent && (
        <p className={cn("mt-2 text-meta", sent.ok ? "text-muted" : "text-negative")}>
          {sent.ok ? `Invitation emailed to ${sent.email}.` : `The email to ${sent.email} couldn't be sent. Send it again, or give them this link yourself:`}{" "}
          {!sent.ok && <span className="break-all font-mono text-ink">{sent.link}</span>}
        </p>
      )}
      <p className="mt-2 text-meta text-muted">
        They need no Syruss account. They sign in (or create a sign-in) with that address, with a password and a one-time code. A staff
        account works only in this app: if the address already has a customer account, they&apos;re shown what accepting would delete first.
      </p>
    </Panel>
  );
}

function Invitations({ manage, onDone }: { manage: boolean; onDone: () => void }) {
  const list = useQuery({ queryKey: ["staff", "invitations"], queryFn: api.staff.invitations });
  const resend = useMutation({ mutationFn: (id: string) => api.staff.resendInvitation(id), onSuccess: onDone });
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
            {manage && <Button size="sm" onClick={() => resend.mutate(i.id)} disabled={resend.isPending}>Send again</Button>}
            {manage && <Button size="sm" variant="danger" onClick={() => revoke.mutate(i.id)} disabled={revoke.isPending}>Withdraw</Button>}
          </li>
        ))}
      </ul>
      {(resend.error ?? revoke.error) && <p className="px-4 pb-3 text-meta text-negative">{(resend.error ?? revoke.error)!.message}</p>}
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
