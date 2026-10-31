"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import { useParams } from "next/navigation";
import { NoAccess } from "@/components/settings/kit";
import { OrgFrame } from "@/components/shell/OrgFrame";
import { Bar, Empty, ErrorNote, PageHeader } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { useMe } from "@/lib/access";
import { api } from "@/lib/api";
import { dateTime } from "@/lib/format";

export default function OrganizationActivity() {
  const { org } = useParams<{ org: string }>();
  const { me, canOrg } = useMe();
  const allowed = canOrg("org.audit.view");
  const log = useInfiniteQuery({
    queryKey: ["org-activity"],
    queryFn: ({ pageParam }) => api.org.activity(pageParam),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_before,
    enabled: allowed,
  });
  const items = (log.data?.pages ?? []).flatMap((p) => p.items);

  return (
    <OrgFrame org={org} title="Activity">
      <div className="flex max-w-3xl flex-col gap-5">
        <PageHeader title="Activity" subtitle="Who changed what in this organization: people, roles, invitations and workspaces." />
        {me && !allowed && <NoAccess what="read the organization's activity" role={me.org_role ?? undefined} />}
        {log.isError && <ErrorNote message="Couldn't load the activity." onRetry={() => log.refetch()} />}
        {allowed && log.isLoading && <div className="flex flex-col gap-3">{Array.from({ length: 6 }, (_, i) => <Bar key={i} className="h-5 w-full" />)}</div>}
        {allowed && log.data && items.length === 0 && <Empty title="Nothing yet">Changes to people, roles and workspaces appear here.</Empty>}
        {items.length > 0 && (
          <ul className="flex flex-col divide-y divide-rule rounded-card border border-border bg-sheet px-4">
            {items.map((a) => (
              <li key={a.id} className="flex items-baseline gap-4 py-3">
                <span className="min-w-0 flex-1 text-body text-ink">{a.text}</span>
                <time dateTime={a.created_at} className="num flex-none text-meta text-muted">{dateTime(a.created_at)}</time>
              </li>
            ))}
          </ul>
        )}
        {log.hasNextPage && (
          <div><Button variant="secondary" size="sm" disabled={log.isFetchingNextPage} onClick={() => log.fetchNextPage()}>{log.isFetchingNextPage ? "Loading…" : "Load older"}</Button></div>
        )}
      </div>
    </OrgFrame>
  );
}
