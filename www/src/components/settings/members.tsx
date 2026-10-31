"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Lock } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { Bar } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Field, Input, Textarea } from "@/components/ui/Field";
import { Pill } from "@/components/ui/Pill";
import { Select } from "@/components/ui/Select";
import { Dialog } from "@/components/ui/Sheet";
import { api } from "@/lib/api";
import { ago, cn } from "@/lib/format";
import type { Member, Permission, Role, RoleCatalogue, RoleInfo } from "@/lib/types";
import { initials } from "@/components/shell/UserMenu";
import { useWorkspace } from "@/lib/workspace";
import { InviteForm, PendingInvitations } from "./invitations";
import { NoAccess, Section, SettingsHeader, useWorkspaceSettings } from "./kit";

export function Avatar({ name, className = "size-8" }: { name: string; className?: string }) {
  return (
    <span aria-hidden className={`flex flex-none items-center justify-center rounded-full bg-active text-meta font-medium text-ink ${className}`}>
      {initials(name)}
    </span>
  );
}

/** The roles you can give people here (built-in and this workspace's own). */
function useRoles() {
  const { workspace } = useWorkspace();
  return useQuery({ queryKey: ["roles", workspace.id], queryFn: () => api.settings.roles(workspace.id) });
}

/** A role you may hand out: it must not grant anything you don't hold yourself. */
function assignable(roles: RoleInfo[], mine: Permission[] | undefined, mineRole: Role | undefined): RoleInfo[] {
  return roles.filter((r) => (r.id !== "owner" || mineRole === "owner") && r.permissions.every((p) => mine?.includes(p)));
}

export function PeopleSettings() {
  const { workspace } = useWorkspace();
  const queryClient = useQueryClient();
  const { access, can } = useWorkspaceSettings();
  const members = useQuery({ queryKey: ["members", workspace.id], queryFn: () => api.settings.members(workspace.id) });
  const roles = useRoles();
  const [removing, setRemoving] = useState<Member | null>(null);
  const refresh = () => ["members", "billing", "roles"].forEach((k) => queryClient.invalidateQueries({ queryKey: [k, workspace.id] }));
  const change = useMutation({ mutationFn: ({ id, role }: { id: string; role: Role }) => api.settings.changeRole(workspace.id, id, role), onSuccess: refresh });
  const remove = useMutation({
    mutationFn: (id: string) => api.settings.removeMember(workspace.id, id),
    onSuccess: () => {
      setRemoving(null);
      refresh();
    },
  });
  const manage = can("members.manage");
  const options = assignable(roles.data?.roles ?? [], access?.permissions, access?.role);

  return (
    <div className="flex flex-col">
      <SettingsHeader title="People" description="Everyone with access to this workspace, and what their role lets them do." />
      <Section title="Members" description={members.data ? `${members.data.length} ${members.data.length === 1 ? "person" : "people"}.` : undefined}>
        {members.isLoading ? (
          <div className="flex flex-col gap-3">{[0, 1].map((i) => <Bar key={i} className="h-12 w-full" />)}</div>
        ) : (
          <ul className="flex flex-col rounded-card border border-border bg-card">
            {(members.data ?? []).map((m) => {
              // A person you can't manage (their role is above yours) still shows their role; you just can't change it.
              const changeable = manage && !m.is_you && (m.role !== "owner" || access?.role === "owner");
              const choices = options.some((r) => r.id === m.role) ? options : [...options, ...(roles.data?.roles ?? []).filter((r) => r.id === m.role)];
              return (
                <li key={m.id} className="flex flex-wrap items-center gap-3 border-b border-rule px-4 py-3 last:border-0">
                  <Avatar name={m.name} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-body font-medium text-ink">
                      {m.name} {m.is_you && <span className="font-normal text-muted">(you)</span>}
                    </span>
                    <span className="block truncate text-meta text-muted">{m.email ?? "No email on file"}, joined {ago(m.created_at).toLowerCase()}</span>
                  </span>
                  {changeable && roles.data ? (
                    <Select
                      label={`Role of ${m.name}`}
                      value={m.role}
                      onChange={(role) => change.mutate({ id: m.id, role })}
                      options={choices.map((r) => ({ value: r.id, label: r.name }))}
                      className="w-40"
                    />
                  ) : (
                    <Pill tone={m.role === "owner" ? "accent" : "neutral"}>{m.role_label}</Pill>
                  )}
                  {(manage || m.is_you) && (m.is_you || m.role !== "owner" || access?.role === "owner") && (
                    <Button size="sm" variant="ghost" onClick={() => setRemoving(m)}>{m.is_you ? "Leave" : "Remove"}</Button>
                  )}
                </li>
              );
            })}
          </ul>
        )}
        {change.error && <p className="mt-2 text-body text-negative">{change.error.message}</p>}
        {!manage && access && <div className="mt-3"><NoAccess what="change roles" role={access.role_label} /></div>}
      </Section>

      <Dialog
        open={!!removing}
        onOpenChange={(o) => !o && setRemoving(null)}
        title={removing?.is_you ? "Leave this workspace?" : `Remove ${removing?.name}?`}
        description={removing?.is_you ? "You lose access until someone invites you again." : "They lose access to this workspace straight away."}
      >
        {remove.error && <p className="mb-3 text-body text-negative">{remove.error.message}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setRemoving(null)}>Cancel</Button>
          <Button variant="danger" disabled={remove.isPending} onClick={() => removing && remove.mutate(removing.id)}>
            {removing?.is_you ? "Leave" : "Remove"}
          </Button>
        </div>
      </Dialog>
    </div>
  );
}

export function InvitationSettings() {
  const { workspace } = useWorkspace();
  const queryClient = useQueryClient();
  const { org, ws } = useParams<{ org: string; ws: string }>();
  const { access, can } = useWorkspaceSettings();
  const manage = can("members.manage");
  const roles = useRoles();
  const invitations = useQuery({
    queryKey: ["invitations", workspace.id],
    queryFn: () => api.settings.invitations(workspace.id),
    enabled: manage,
    // While an email is on its way, keep looking until it reports back.
    refetchInterval: (q) => (q.state.data?.some((i) => i.email_status === "SENDING") ? 2500 : false),
  });
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["invitations", workspace.id] });
  const options = assignable(roles.data?.roles ?? [], access?.permissions, access?.role);

  if (access && !manage) {
    return (
      <div className="flex flex-col">
        <SettingsHeader title="Invitations" description="Invite people to this workspace." />
        <div className="pt-5"><NoAccess what="invite people" role={access.role_label} /></div>
      </div>
    );
  }

  return (
    <div className="flex flex-col">
      <SettingsHeader title="Invitations" description="Invite people by email. Each invitation is a link for that address, valid for 7 days." />
      <Section title="Invite people" description="They get an email with the role you pick. Anything that goes wrong shows below, and you can always copy the link instead.">
        {roles.data ? (
          <InviteForm
            roles={options.map((r) => ({ value: r.id, label: r.name, help: r.description || `${r.permissions.length} of ${roles.data!.permissions.length} privileges` }))}
            defaultRole={options.some((r) => r.id === "reviewer") ? "reviewer" : options[0]?.id ?? "viewer"}
            roleLabel="Role"
            send={(email, role, note) => api.settings.invite(workspace.id, email, role, note)}
            onSent={refresh}
            footer={
              <span className="text-meta text-muted">
                Need a different job title?{" "}
                <Link href={`/${org}/w/${ws}/settings/roles`} className="font-medium text-ink underline underline-offset-2">Create a role</Link>.
              </span>
            }
          />
        ) : (
          <Bar className="h-24 w-full" />
        )}
      </Section>

      <Section title="Pending" description="Not accepted yet. Resending or getting a new link replaces the old link.">
        <PendingInvitations
          invitations={invitations.data}
          loading={invitations.isLoading}
          resend={async (id) => {
            const res = await api.settings.resendInvitation(workspace.id, id);
            await refresh();
            return res;
          }}
          renew={async (id) => {
            const res = await api.settings.renewInvitation(workspace.id, id);
            await refresh();
            return res;
          }}
          revoke={async (id) => {
            await api.settings.revokeInvitation(workspace.id, id);
            await refresh();
          }}
        />
      </Section>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Roles: a role is a job title, and every privilege it carries is a tick you can read.
// ---------------------------------------------------------------------------

type Draft = { id?: string; name: string; description: string; permissions: Permission[]; system: boolean };
const BLANK: Draft = { name: "", description: "", permissions: ["documents.view"], system: false };

/** Everything a set of ticks includes: doing something always includes seeing it. */
function withImplied(selected: Permission[], implies: RoleCatalogue["implies"]): Set<Permission> {
  const all = new Set<Permission>(selected);
  for (const p of selected) (implies[p] ?? []).forEach((q) => all.add(q));
  return all;
}

function RoleEditor({ catalogue, role, mine, workspaceId, onDone }: {
  catalogue: RoleCatalogue; role: Draft; mine: Permission[]; workspaceId: string; onDone: () => void;
}) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<Draft>(role);
  useEffect(() => setDraft(role), [role]);
  const editable = !draft.system && catalogue.can_manage;
  const included = useMemo(() => withImplied(draft.permissions, catalogue.implies), [draft.permissions, catalogue.implies]);
  const refresh = () => ["roles", "members", "invitations"].forEach((k) => queryClient.invalidateQueries({ queryKey: [k, workspaceId] }));
  const save = useMutation({
    mutationFn: () => {
      const body = { name: draft.name.trim(), description: draft.description.trim(), permissions: [...included] as Permission[] };
      return draft.id ? api.settings.updateRole(workspaceId, draft.id, body) : api.settings.createRole(workspaceId, body);
    },
    onSuccess: () => {
      refresh();
      onDone();
    },
  });
  const remove = useMutation({
    mutationFn: () => api.settings.deleteRole(workspaceId, draft.id!),
    onSuccess: () => {
      refresh();
      onDone();
    },
  });
  const toggle = (p: Permission) =>
    setDraft((d) => ({ ...d, permissions: d.permissions.includes(p) ? d.permissions.filter((x) => x !== p) : [...d.permissions, p] }));
  // What a ticked item forces on: it can't be unticked while something that needs it is ticked.
  const neededBy = (p: Permission) => draft.permissions.find((q) => (catalogue.implies[q] ?? []).includes(p));

  return (
    <div className="flex flex-col gap-5 rounded-card border border-border bg-card p-5">
      {editable ? (
        <div className="grid grid-cols-1 gap-4 @2xl:grid-cols-2">
          <Field label="Role name" help="The job title: for example Intake clerk or Payroll lead.">
            {(id) => <Input id={id} value={draft.name} maxLength={64} required onChange={(e) => setDraft({ ...draft, name: e.target.value })} />}
          </Field>
          <Field label="What this person does (optional)">
            {(id) => <Textarea id={id} value={draft.description} maxLength={500} onChange={(e) => setDraft({ ...draft, description: e.target.value })} />}
          </Field>
        </div>
      ) : (
        <div>
          <h3 className="text-section font-semibold text-ink">{draft.name}</h3>
          <p className="mt-0.5 text-body text-muted">{draft.description || "No description."}</p>
          {draft.system && (
            <p className="mt-2 flex items-center gap-1.5 text-meta text-muted"><Lock className="size-3.5" aria-hidden /> Built-in roles are the same in every workspace and can’t be changed.</p>
          )}
        </div>
      )}

      <div className="flex flex-col gap-5">
        {catalogue.groups.map((g) => (
          <fieldset key={g.key} className="min-w-0">
            <legend className="mb-2 text-body font-medium text-ink">{g.label}</legend>
            <ul className="flex flex-col gap-2">
              {g.permissions.map((p) => {
                const on = included.has(p.key);
                const forcedBy = neededBy(p.key);
                const youLack = !mine.includes(p.key);
                const disabled = !editable || !!forcedBy || (youLack && !on);
                const id = `perm-${draft.id ?? "new"}-${p.key}`;
                return (
                  <li key={p.key}>
                    <label htmlFor={id} className={cn("flex items-start gap-3 rounded-control px-2 py-1.5", editable && !disabled && "hover:bg-raised")}>
                      <input
                        id={id}
                        type="checkbox"
                        className="mt-1 size-4 flex-none accent-[var(--accent)]"
                        checked={on}
                        disabled={disabled}
                        onChange={() => toggle(p.key)}
                      />
                      <span className="min-w-0">
                        <span className="block text-body text-ink">{p.label}</span>
                        <span className="block text-meta text-muted">
                          {p.description}
                          {forcedBy && editable ? ` Included because “${catalogue.permissions.find((x) => x.key === forcedBy)?.label}” is ticked.` : ""}
                          {editable && youLack && !on ? " You don’t have this privilege, so you can’t give it." : ""}
                        </span>
                      </span>
                    </label>
                  </li>
                );
              })}
            </ul>
          </fieldset>
        ))}
      </div>

      {(save.error || remove.error) && <p className="text-body text-negative">{(save.error ?? remove.error)!.message}</p>}
      <div className="flex flex-wrap items-center gap-2">
        {editable && (
          <Button variant="primary" disabled={!draft.name.trim() || save.isPending} onClick={() => save.mutate()}>
            {save.isPending ? "Saving…" : draft.id ? "Save role" : "Create role"}
          </Button>
        )}
        {editable && draft.id && <Button variant="danger" disabled={remove.isPending} onClick={() => remove.mutate()}>Delete role</Button>}
        <Button variant="ghost" onClick={onDone}>{editable ? "Cancel" : "Close"}</Button>
      </div>
    </div>
  );
}

export function RoleSettings() {
  const { workspace } = useWorkspace();
  const { access } = useWorkspaceSettings();
  const roles = useRoles();
  const [open, setOpen] = useState<Draft | null>(null);
  const catalogue = roles.data;

  return (
    <div className="flex flex-col">
      <SettingsHeader
        title="Roles and permissions"
        description="A role is a job title: it decides what a person can see and do in this workspace. Start from a built-in role or make your own."
        actions={
          catalogue?.can_manage ? <Button variant="primary" onClick={() => setOpen(BLANK)}>New role</Button> : undefined
        }
      />
      {!catalogue ? (
        <div className="flex flex-col gap-3 pt-6">{[0, 1, 2].map((i) => <Bar key={i} className="h-10 w-full" />)}</div>
      ) : (
        <div className="flex flex-col gap-6 pt-6">
          <ul className="grid grid-cols-1 gap-3 @3xl:grid-cols-2">
            {catalogue.roles.map((r) => (
              <li key={r.id}>
                <button
                  type="button"
                  onClick={() => setOpen({ id: r.id, name: r.name, description: r.description, permissions: r.permissions, system: r.system })}
                  aria-pressed={open?.id === r.id}
                  className={cn(
                    "flex h-full w-full flex-col items-start gap-1 rounded-card border bg-card px-4 py-3 text-left transition-colors hover:border-control",
                    open?.id === r.id ? "border-ink" : "border-border",
                  )}
                >
                  <span className="flex w-full items-center gap-2">
                    <span className="min-w-0 flex-1 truncate text-body font-medium text-ink">{r.name}</span>
                    <Pill tone={r.system ? "neutral" : "info"}>{r.system ? "Built in" : "Custom"}</Pill>
                  </span>
                  <span className="text-body text-muted">{r.description || "No description."}</span>
                  <span className="text-meta text-faint">
                    {r.permissions.length} of {catalogue.permissions.length} privileges · {r.members} {r.members === 1 ? "person" : "people"}
                  </span>
                </button>
              </li>
            ))}
          </ul>
          {open && (
            <RoleEditor
              key={open.id ?? "new"}
              catalogue={catalogue}
              role={open}
              mine={access?.permissions ?? []}
              workspaceId={workspace.id}
              onDone={() => setOpen(null)}
            />
          )}
          {!catalogue.can_manage && access && <NoAccess what="create or change roles" role={access.role_label} />}
        </div>
      )}
    </div>
  );
}
