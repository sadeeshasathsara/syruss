"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useParams } from "next/navigation";
import { useState } from "react";
import { InviteForm, PendingInvitations } from "@/components/settings/invitations";
import { Avatar } from "@/components/settings/members";
import { NoAccess, Section, SettingsHeader } from "@/components/settings/kit";
import { OrgFrame } from "@/components/shell/OrgFrame";
import { Bar } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Pill } from "@/components/ui/Pill";
import { Select } from "@/components/ui/Select";
import { Dialog } from "@/components/ui/Sheet";
import { useMe } from "@/lib/access";
import { api } from "@/lib/api";
import { ago } from "@/lib/format";
import type { OrgMemberInfo, OrgRole } from "@/lib/types";

const ROLE_HELP: Record<OrgRole, string> = {
  owner: "Runs the organization: people, workspaces and billing.",
  admin: "Manages people and workspaces. Opens a workspace's documents only by joining it.",
  member: "Belongs to the organization and enters the workspaces they are added to.",
};

export default function PeoplePage() {
  const { org } = useParams<{ org: string }>();
  const queryClient = useQueryClient();
  const { me, canOrg } = useMe();
  const manage = canOrg("org.members.manage");
  const members = useQuery({ queryKey: ["org-members"], queryFn: api.org.members });
  const invitations = useQuery({
    queryKey: ["org-invitations"],
    queryFn: api.org.invitations,
    enabled: manage,
    refetchInterval: (q) => (q.state.data?.some((i) => i.email_status === "SENDING") ? 2500 : false),
  });
  const [removing, setRemoving] = useState<OrgMemberInfo | null>(null);

  const refresh = () => ["org-members", "org-invitations"].forEach((k) => queryClient.invalidateQueries({ queryKey: [k] }));
  const change = useMutation({ mutationFn: ({ id, r }: { id: string; r: OrgRole }) => api.org.changeRole(id, r), onSuccess: refresh });
  const remove = useMutation({
    mutationFn: (id: string) => api.org.removeMember(id),
    onSuccess: () => {
      setRemoving(null);
      refresh();
    },
  });
  const isOwner = me?.org_role === "owner";

  return (
    <OrgFrame org={org} title="People">
      <div className="mx-auto w-full max-w-5xl">
        <SettingsHeader
          title="People"
          description="Everyone in this organization. Organization roles run the organization; a workspace's documents open only through a role in that workspace."
        />
        <Section title="Members" description={members.data ? `${members.data.length} ${members.data.length === 1 ? "person" : "people"}.` : undefined}>
          {members.isLoading ? (
            <Bar className="h-12 w-full" />
          ) : (
            <ul className="flex flex-col rounded-card border border-border bg-card">
              {(members.data ?? []).map((m) => (
                <li key={m.user_id} className="flex flex-wrap items-center gap-3 border-b border-rule px-4 py-3 last:border-0">
                  <Avatar name={m.name} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-body font-medium text-ink">{m.name} {m.is_you && <span className="font-normal text-muted">(you)</span>}</span>
                    <span className="block truncate text-meta text-muted">{m.email}, joined {ago(m.created_at).toLowerCase()}</span>
                  </span>
                  {manage && !m.is_you && (isOwner || m.role !== "owner") ? (
                    <Select
                      label={`Role of ${m.name}`}
                      value={m.role}
                      onChange={(r) => change.mutate({ id: m.user_id, r: r as OrgRole })}
                      options={(["owner", "admin", "member"] as OrgRole[]).filter((r) => r !== "owner" || isOwner).map((r) => ({ value: r, label: r[0].toUpperCase() + r.slice(1) }))}
                      className="w-32"
                    />
                  ) : (
                    <Pill tone={m.role === "owner" ? "accent" : "neutral"}>{m.role_label}</Pill>
                  )}
                  {(m.is_you || (manage && (isOwner || m.role !== "owner"))) && (
                    <Button size="sm" variant="ghost" onClick={() => setRemoving(m)}>{m.is_you ? "Leave" : "Remove"}</Button>
                  )}
                </li>
              ))}
            </ul>
          )}
          {change.error && <p className="mt-2 text-body text-negative">{change.error.message}</p>}
          {me && !manage && <div className="mt-3"><NoAccess what="change who is in the organization" role={me.org_role ?? undefined} /></div>}
        </Section>

        {manage && (
          <>
            <Section title="Invite people" description="They get an email with the organization role you pick, and you can always copy the link instead. Workspace roles are given inside each workspace.">
              <InviteForm
                roles={[
                  { value: "member", label: "Member", help: ROLE_HELP.member },
                  { value: "admin", label: "Admin", help: ROLE_HELP.admin },
                ]}
                defaultRole="member"
                roleLabel="Organization role"
                send={(email, r, note) => api.org.invite(email, r as "admin" | "member", note)}
                onSent={refresh}
              />
            </Section>

            <Section title="Pending" description="Not accepted yet. Resending or getting a new link replaces the old link.">
              <PendingInvitations
                invitations={invitations.data}
                loading={invitations.isLoading}
                resend={async (id) => {
                  const res = await api.org.resendInvitation(id);
                  refresh();
                  return res;
                }}
                renew={async (id) => {
                  const res = await api.org.renewInvitation(id);
                  refresh();
                  return res;
                }}
                revoke={async (id) => {
                  await api.org.revokeInvitation(id);
                  refresh();
                }}
              />
            </Section>
          </>
        )}
      </div>

      <Dialog
        open={!!removing}
        onOpenChange={(o) => !o && setRemoving(null)}
        title={removing?.is_you ? "Leave this organization?" : `Remove ${removing?.name}?`}
        description="They lose access to the organization and to every workspace in it straight away."
      >
        {remove.error && <p className="mb-3 text-body text-negative">{remove.error.message}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setRemoving(null)}>Cancel</Button>
          <Button variant="danger" disabled={remove.isPending} onClick={() => removing && remove.mutate(removing.user_id)}>{removing?.is_you ? "Leave" : "Remove"}</Button>
        </div>
      </Dialog>
    </OrgFrame>
  );
}
