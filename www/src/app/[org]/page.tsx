"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useParams } from "next/navigation";
import { Bar, Empty, ErrorNote, PageHeader, Panel } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { OrgFrame } from "@/components/shell/OrgFrame";
import { useMe } from "@/lib/access";
import { api } from "@/lib/api";
import { ago, cn } from "@/lib/format";

function Figure({ label, value, note, href }: { label: string; value?: number | string; note?: string; href: string }) {
  return (
    <Link href={href} className="flex min-w-0 flex-col gap-1 rounded-card border border-border bg-sheet px-4 py-3 transition-colors hover:bg-raised">
      <span className="text-body text-muted">{label}</span>
      {value === undefined ? <Bar className="h-7 w-12" /> : <span className={cn("num text-figure", value ? "text-ink" : "text-faint")}>{value}</span>}
      {note !== undefined && <span className="truncate text-meta text-muted">{note}</span>}
    </Link>
  );
}

export default function OrganizationOverview() {
  const { org } = useParams<{ org: string }>();
  const { canOrg } = useMe();
  const overview = useQuery({ queryKey: ["org-overview"], queryFn: api.org.overview });
  const workspaces = useQuery({ queryKey: ["workspaces"], queryFn: api.workspaces.list });
  const o = overview.data;
  const mine = (workspaces.data ?? []).filter((w) => w.my_role);

  const waiting = o ? [
    o.attention.needs_review && `${o.attention.needs_review} document${o.attention.needs_review === 1 ? "" : "s"} to review`,
    o.attention.questions && `${o.attention.questions} question${o.attention.questions === 1 ? "" : "s"} from the agents`,
    o.attention.drafts && `${o.attention.drafts} template${o.attention.drafts === 1 ? "" : "s"} still in design`,
  ].filter(Boolean) : [];

  return (
    <OrgFrame org={org} title="Overview">
      <div className="flex flex-col gap-6">
        <PageHeader
          title={o?.organization.name ?? "Overview"}
          subtitle={o ? (waiting.length ? `Waiting on you: ${waiting.join(", ")}.` : "Nothing is waiting on you.") : "Your organization at a glance."}
          actions={
            <>
              {canOrg("org.members.manage") && <Button asChild variant="secondary"><Link href={`/${org}/people`}>Invite people</Link></Button>}
              {canOrg("workspaces.create") && <Button asChild variant="primary"><Link href={`/${org}/new`}>New workspace</Link></Button>}
            </>
          }
        />
        {overview.isError && <ErrorNote message="Couldn't load the overview." onRetry={() => overview.refetch()} />}

        <section aria-label="The organization in numbers" className="grid grid-cols-2 gap-3 @3xl:grid-cols-4">
          <Figure label="Workspaces" value={o?.workspaces.total} note={o ? `${o.workspaces.draft} in design` : undefined} href={`/${org}/workspaces`} />
          <Figure label="People" value={o?.people.total} note={o ? `${o.people.owners} owner${o.people.owners === 1 ? "" : "s"}, ${o.people.admins} admin${o.people.admins === 1 ? "" : "s"}` : undefined} href={`/${org}/people`} />
          <Figure label="To review" value={o?.attention.needs_review} note="In your workspaces" href={`/${org}/workspaces`} />
          {canOrg("org.members.manage")
            ? <Figure label="Invitations waiting" value={o?.pending_invitations ?? undefined} note="Not accepted yet" href={`/${org}/people`} />
            : <Figure label="Questions" value={o?.attention.questions} note="From the agents" href={`/${org}/workspaces`} />}
        </section>

        <div className="grid grid-cols-1 items-start gap-6 @4xl:grid-cols-2">
          <Panel title="Your workspaces" aside={<Link href={`/${org}/workspaces`} className="hover:text-ink">See all</Link>}>
            {workspaces.isLoading ? (
              <div className="flex flex-col gap-3">{[0, 1, 2].map((i) => <Bar key={i} className="h-5 w-full" />)}</div>
            ) : mine.length === 0 ? (
              <Empty
                title={canOrg("workspaces.create") ? "Create your first workspace" : "You're not in a workspace yet"}
                action={canOrg("workspaces.create") ? <Button asChild variant="primary" size="sm"><Link href={`/${org}/new`}>New workspace</Link></Button> : undefined}
              >
                {canOrg("workspaces.create")
                  ? "A workspace holds one kind of document, such as supplier invoices or daily rosters. Upload a few samples and the agent proposes what to capture."
                  : "Ask someone who runs a workspace to invite you."}
              </Empty>
            ) : (
              <ul className="flex flex-col divide-y divide-rule">
                {mine.slice(0, 6).map((w) => (
                  <li key={w.id}>
                    <Link href={`/${org}/w/${w.slug}${w.status === "draft" ? "/setup" : ""}`} className="flex items-center gap-3 py-2.5 hover:underline">
                      <span className="min-w-0 flex-1 truncate text-body text-ink">{w.name}</span>
                      <span className="num text-meta text-muted">{w.needs_review + w.open_questions ? `${w.needs_review + w.open_questions} waiting` : `${w.documents} documents`}</span>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Panel>

          {canOrg("org.audit.view") ? (
            <Panel title="Recent activity" aside={<Link href={`/${org}/activity`} className="hover:text-ink">Open activity</Link>}>
              {!o ? (
                <div className="flex flex-col gap-3">{[0, 1, 2].map((i) => <Bar key={i} className="h-5 w-full" />)}</div>
              ) : (o.activity ?? []).length === 0 ? (
                <p className="text-body text-muted">Changes to people, roles and workspaces appear here.</p>
              ) : (
                <ul className="flex flex-col divide-y divide-rule">
                  {o.activity!.map((a) => (
                    <li key={a.id} className="flex items-baseline gap-3 py-2">
                      <span className="min-w-0 flex-1 text-body text-ink">{a.text}</span>
                      <span className="num flex-none text-meta text-muted">{ago(a.created_at)}</span>
                    </li>
                  ))}
                </ul>
              )}
            </Panel>
          ) : (
            <Panel title="How this works">
              <p className="text-body text-muted">
                Organization roles run the organization: its people and workspaces. A workspace&apos;s documents open only through a role in that workspace.
              </p>
            </Panel>
          )}
        </div>
      </div>
    </OrgFrame>
  );
}
