"use client";

import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { RunDetail, SOURCE, STATUS, TRIGGER, who } from "@/components/settings/runs";
import { Bar } from "@/components/ui/Blocks";
import { Pill } from "@/components/ui/Pill";
import { api } from "@/lib/api";
import { ago, cn, humanize } from "@/lib/format";
import { useLive } from "@/lib/live";
import { useWorkspace } from "@/lib/workspace";
import { ProposalCard, QuestionCard, RunCard } from "./cards";
import { buildRuns } from "./timeline";

function Heading({ children, count }: { children: React.ReactNode; count?: number }) {
  return (
    <h3 className="flex items-center gap-2 px-1 text-meta font-medium text-muted">
      {children}
      {!!count && <span className="num text-ink">{count}</span>}
    </h3>
  );
}

/**
 * Everything happening in the workspace that isn't your conversation: what's waiting on you, what
 * Syruss is doing now, and what it did recently. Owners and admins see everyone's work; others
 * see their own. Chat answers stay in their chats.
 */
export function ActivityFeed() {
  const { workspace } = useWorkspace();
  const wid = workspace.id;
  const { events } = useLive();
  const me = useQuery({ queryKey: ["me"], queryFn: api.me.get, staleTime: 5 * 60_000 });
  const access = useQuery({ queryKey: ["settings", wid], queryFn: () => api.settings.get(wid) });
  const seesAll = !!access.data?.access.permissions.includes("audit.view");
  const [onlyMine, setOnlyMine] = useState(false);
  const mine = !seesAll || onlyMine;

  const questions = useQuery({ queryKey: ["questions", wid, "OPEN"], queryFn: () => api.questions.list(wid) });
  const proposals = useQuery({ queryKey: ["proposals", wid, "PENDING"], queryFn: () => api.proposals.list(wid) });
  const recent = useQuery({ queryKey: ["runs", wid, "activity", mine], queryFn: () => api.runs.list(wid, { mine, limit: 30 }) });
  const [open, setOpen] = useState<string | null>(null);

  const live = useMemo(
    () =>
      buildRuns(events)
        .filter((r) => !r.finished && r.origin?.source !== "chat")
        .filter((r) => !mine || (r.origin?.id && r.origin.id === me.data?.user_id))
        .reverse(),
    [events, mine, me.data],
  );
  const liveIds = new Set(live.map((r) => r.id));
  const done = (recent.data?.items ?? []).filter((r) => r.source !== "chat" && !(r.run_id && liveIds.has(r.run_id)));
  const waiting = (questions.data?.length ?? 0) + (proposals.data?.length ?? 0);

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto bg-chat px-3 py-4 scrollbar-thin sm:px-4">
      {seesAll && (
        <div className="flex gap-1" role="group" aria-label="Whose work">
          {[false, true].map((m) => (
            <button
              key={String(m)}
              type="button"
              aria-pressed={onlyMine === m}
              onClick={() => setOnlyMine(m)}
              className={cn("h-7 rounded-control px-2.5 text-body transition-colors", onlyMine === m ? "bg-card font-medium text-ink shadow-sm" : "text-muted hover:text-ink")}
            >
              {m ? "Mine" : "Everything"}
            </button>
          ))}
        </div>
      )}

      {waiting > 0 && (
        <section className="flex flex-col gap-2">
          <Heading count={waiting}>Waiting on you</Heading>
          {(questions.data ?? []).map((q) => <QuestionCard key={q.id} question={q} />)}
          {(proposals.data ?? []).map((p) => <ProposalCard key={p.id} proposal={p} />)}
        </section>
      )}

      {live.length > 0 && (
        <section className="flex flex-col gap-2">
          <Heading count={live.length}>Working now</Heading>
          {live.map((r) => <RunCard key={r.id} run={r} />)}
        </section>
      )}

      <section className="flex flex-col gap-2">
        <Heading>Recent</Heading>
        {recent.isLoading ? (
          <div className="flex flex-col gap-2">{[0, 1, 2].map((i) => <Bar key={i} className="h-14 w-full" />)}</div>
        ) : done.length === 0 ? (
          <p className="px-1 text-body text-muted">
            {mine ? "Nothing you started yet." : "Nothing yet."} Documents read, templates designed and data mapped show up here.
          </p>
        ) : (
          <ul className="flex flex-col gap-2">
            {done.map((r) => (
              <li key={r.id}>
                <button
                  type="button"
                  onClick={() => setOpen(r.id)}
                  className="flex w-full animate-rise flex-col gap-0.5 rounded-card border border-border bg-card px-3 py-2.5 text-left transition-colors hover:border-control"
                >
                  <span className="flex items-center gap-2">
                    <span className="min-w-0 flex-1 truncate text-body font-medium text-ink">
                      {TRIGGER[r.trigger] ?? humanize(r.trigger)}
                      {r.document_name && <span className="font-normal text-muted">: {r.document_name}</span>}
                    </span>
                    {STATUS[r.status] && r.status !== "COMPLETED" && <Pill tone={STATUS[r.status].tone}>{STATUS[r.status].label}</Pill>}
                  </span>
                  <span className="truncate text-meta text-muted">
                    {who(r)}, {SOURCE[r.source]?.toLowerCase() ?? r.source}, {ago(r.started_at).toLowerCase()}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
      <RunDetail id={open} onClose={() => setOpen(null)} />
    </div>
  );
}
