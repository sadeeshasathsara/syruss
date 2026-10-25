"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Lock } from "lucide-react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useState } from "react";
import { Bar, Empty, ErrorNote, PageHeader } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Pill } from "@/components/ui/Pill";
import { Dialog } from "@/components/ui/Sheet";
import { OrgFrame } from "@/components/shell/OrgFrame";
import { useMe } from "@/lib/access";
import { api } from "@/lib/api";
import { ago } from "@/lib/format";
import type { Workspace } from "@/lib/types";

function finding(workspaces: Workspace[]): string {
  const mine = workspaces.filter((w) => w.my_role);
  const review = mine.reduce((n, w) => n + w.needs_review, 0);
  const questions = mine.reduce((n, w) => n + w.open_questions + w.pending_proposals, 0);
  const drafts = mine.filter((w) => w.status === "draft").length;
  const parts = [];
  if (review) parts.push(`${review} document${review === 1 ? "" : "s"} to review`);
  if (questions) parts.push(`${questions} question${questions === 1 ? "" : "s"} from the agents`);
  if (drafts) parts.push(`${drafts} template${drafts === 1 ? "" : "s"} still in design`);
  return parts.length ? `Waiting on you: ${parts.join(", ")}.` : "Nothing is waiting on you.";
}

function Count({ n, label }: { n: number; label: string }) {
  return (
    <span className={n ? "text-ink" : "text-faint"}>
      <span className="num font-medium">{n}</span> <span className="text-muted">{label}</span>
    </span>
  );
}

export default function WorkspacesPage() {
  const { org } = useParams<{ org: string }>();
  const router = useRouter();
  const queryClient = useQueryClient();
  const { canOrg } = useMe();
  const workspaces = useQuery({ queryKey: ["workspaces"], queryFn: api.workspaces.list });
  const [deleting, setDeleting] = useState<Workspace | null>(null);

  const join = useMutation({
    mutationFn: (slug: string) => api.workspaces.join(slug, "admin"),
    onSuccess: (w) => {
      queryClient.invalidateQueries({ queryKey: ["workspaces"] });
      router.push(`/${org}/w/${w.slug}${w.status === "draft" ? "/setup" : ""}`);
    },
  });
  const remove = useMutation({
    mutationFn: (slug: string) => api.workspaces.remove(slug),
    onSuccess: () => {
      setDeleting(null);
      queryClient.invalidateQueries({ queryKey: ["workspaces"] });
    },
  });

  const mine = (workspaces.data ?? []).filter((w) => w.my_role);
  const managed = (workspaces.data ?? []).filter((w) => !w.my_role);

  return (
    <OrgFrame org={org} title="Workspaces">
      <div className="flex flex-col gap-6">
        <PageHeader
          title="Workspaces"
          subtitle={workspaces.data ? finding(workspaces.data) : "Each workspace holds one kind of document and its own agent."}
          actions={
            canOrg("workspaces.create") ? (
              <Button asChild variant="primary">
                <Link href={`/${org}/new`}>New workspace</Link>
              </Button>
            ) : undefined
          }
        />

        {workspaces.isError && <ErrorNote message="Couldn't load workspaces." onRetry={() => workspaces.refetch()} />}

        {workspaces.isLoading && (
          <ul className="flex flex-col">
            {[0, 1, 2].map((i) => (
              <li key={i} className="flex flex-col gap-2 border-b border-rule py-4">
                <Bar className="h-4 w-48" />
                <Bar className="h-3 w-72" />
              </li>
            ))}
          </ul>
        )}

        {workspaces.data && mine.length === 0 && managed.length === 0 && (
          <Empty
            title={canOrg("workspaces.create") ? "Create your first workspace" : "You're not in a workspace yet"}
            action={
              canOrg("workspaces.create") ? (
                <Button asChild variant="primary">
                  <Link href={`/${org}/new`}>New workspace</Link>
                </Button>
              ) : undefined
            }
          >
            {canOrg("workspaces.create")
              ? "A workspace is one kind of document, such as supplier invoices, daily rosters or delivery notes. You upload a few samples, the agent proposes what to capture, and from then on it reads every document of that kind for you."
              : "Ask someone who runs a workspace to invite you. You'll see it here once you've joined."}
          </Empty>
        )}

        {mine.length > 0 && (
          <ul className="flex flex-col border-t border-rule">
            {mine.map((w) => (
              <li key={w.id} className="border-b border-rule">
                <Link
                  href={`/${org}/w/${w.slug}${w.status === "draft" ? "/setup" : ""}`}
                  className="grid grid-cols-1 gap-x-6 gap-y-1 px-1 py-3.5 hover:bg-raised @2xl:grid-cols-[minmax(0,1fr)_auto] @2xl:items-center"
                >
                  <span className="min-w-0">
                    <span className="flex items-center gap-2">
                      <span className="truncate text-section font-medium text-ink">{w.name}</span>
                      {w.status === "draft" && <Pill>Draft</Pill>}
                      {w.my_role_name && <Pill tone="neutral">{w.my_role_name}</Pill>}
                    </span>
                    <span className="block truncate text-body text-muted">
                      {w.status === "draft" ? "Template in design" : w.description || "Active"}, updated {ago(w.updated_at).toLowerCase()}
                    </span>
                  </span>
                  <span className="flex flex-wrap gap-x-5 gap-y-1 text-body">
                    <Count n={w.documents} label="documents" />
                    <Count n={w.needs_review} label="to review" />
                    <Count n={w.open_questions + w.pending_proposals} label="questions" />
                  </span>
                </Link>
              </li>
            ))}
          </ul>
        )}

        {managed.length > 0 && (
          <section className="flex flex-col gap-2" aria-labelledby="managed-h">
            <h2 id="managed-h" className="text-section font-semibold text-ink">You manage, but aren’t a member</h2>
            <p className="max-w-2xl text-body text-muted">
              Organization admins run the workspaces without reading their documents. Join one to open it; that is recorded in its audit log.
            </p>
            <ul className="flex flex-col border-t border-rule">
              {managed.map((w) => (
                <li key={w.id} className="flex flex-wrap items-center gap-3 border-b border-rule px-1 py-3">
                  <Lock className="size-4 flex-none text-muted" aria-hidden />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-body font-medium text-ink">{w.name}</span>
                    <span className="block truncate text-meta text-muted">{w.status === "draft" ? "Template in design" : w.description || "Active"}</span>
                  </span>
                  {canOrg("org.manage") && (
                    <Button size="sm" variant="secondary" disabled={join.isPending} onClick={() => join.mutate(w.slug)}>Join as admin</Button>
                  )}
                  {canOrg("workspaces.delete") && (
                    <Button size="sm" variant="ghost" onClick={() => setDeleting(w)}>Delete</Button>
                  )}
                </li>
              ))}
            </ul>
            {join.error && <p className="text-body text-negative">{join.error.message}</p>}
          </section>
        )}
      </div>

      <Dialog
        open={!!deleting}
        onOpenChange={(o) => !o && setDeleting(null)}
        title={`Delete ${deleting?.name}?`}
        description="Its documents, template, knowledge and chats are removed for everyone. This can't be undone."
      >
        {remove.error && <p className="mb-3 text-body text-negative">{remove.error.message}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={() => setDeleting(null)}>Cancel</Button>
          <Button variant="danger" disabled={remove.isPending} onClick={() => deleting && remove.mutate(deleting.slug)}>Delete workspace</Button>
        </div>
      </Dialog>
    </OrgFrame>
  );
}
