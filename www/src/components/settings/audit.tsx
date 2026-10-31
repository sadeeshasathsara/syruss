"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import { Bar, Empty, ErrorNote } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { api } from "@/lib/api";
import { dateTime, humanize } from "@/lib/format";
import { useWorkspace } from "@/lib/workspace";
import { NoAccess, SettingsHeader, useWorkspaceSettings } from "./kit";

const PAGE = 50;

function actorLabel(actor: string) {
  if (actor === "agent") return "The agent";
  if (actor === "system") return "The platform";
  if (actor.startsWith("apikey:")) return "An API key";
  return actor;
}

/** Everything that changed in this workspace, who did it, and when. */
export function AuditSettings() {
  const { workspace } = useWorkspace();
  const { access, can } = useWorkspaceSettings();
  const allowed = can("audit.view");
  const log = useInfiniteQuery({
    queryKey: ["audit", workspace.id],
    queryFn: ({ pageParam }) => api.settings.audit(workspace.id, PAGE, pageParam),
    initialPageParam: 0,
    getNextPageParam: (last, pages) => (last.length === PAGE ? pages.length * PAGE : undefined),
    enabled: allowed,
  });
  const rows = log.data?.pages.flat() ?? [];

  return (
    <div className="flex flex-col gap-5">
      <SettingsHeader title="Activity" description="Every change in this workspace, by people, API keys and the agent, newest first. Kept for compliance." />
      {access && !allowed ? (
        <NoAccess what="see the audit log" role={access.role_label} />
      ) : log.isError ? (
        <ErrorNote message="Couldn't load the audit log." onRetry={() => log.refetch()} />
      ) : log.isLoading ? (
        <div className="flex flex-col gap-3">{[0, 1, 2, 3].map((i) => <Bar key={i} className="h-10 w-full" />)}</div>
      ) : rows.length === 0 ? (
        <Empty title="Nothing recorded yet" />
      ) : (
        <>
          <div className="overflow-x-auto rounded-card border border-border">
            <table className="w-full min-w-[640px] border-collapse text-body">
              <thead>
                <tr className="bg-raised text-left text-meta text-muted">
                  <th className="px-4 py-2 font-normal">When</th>
                  <th className="px-3 py-2 font-normal">Who</th>
                  <th className="px-3 py-2 font-normal">What</th>
                  <th className="px-4 py-2 font-normal">Details</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.id} className="border-t border-rule align-top">
                    <td className="num whitespace-nowrap px-4 py-2 text-muted">{dateTime(r.created_at)}</td>
                    <td className="px-3 py-2 text-ink">{actorLabel(r.actor)}</td>
                    <td className="px-3 py-2 text-ink">{humanize(r.event_type.toLowerCase())}</td>
                    <td className="max-w-md truncate px-4 py-2 text-muted" title={JSON.stringify(r.details)}>
                      {Object.entries(r.details ?? {})
                        .filter(([, v]) => v !== null && v !== "" && typeof v !== "object")
                        .slice(0, 3)
                        .map(([k, v]) => `${humanize(k)}: ${String(v)}`)
                        .join(", ")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {log.hasNextPage && (
            <div>
              <Button variant="secondary" disabled={log.isFetchingNextPage} onClick={() => log.fetchNextPage()}>
                {log.isFetchingNextPage ? "Loading…" : "Load older"}
              </Button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
