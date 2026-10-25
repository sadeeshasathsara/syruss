"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { BetaTag } from "@/components/screens/BetaTag";
import { Bar, Empty, ErrorNote, PageHeader } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { useAccess } from "@/lib/access";
import { api } from "@/lib/api";
import { ago } from "@/lib/format";
import { useWorkspace } from "@/lib/workspace";

/** Screens Syruss thought of by itself. Nothing joins the sidebar until a person keeps it. */
export default function SuggestedScreens() {
  const { workspace, href } = useWorkspace();
  const wid = workspace.id;
  const { can } = useAccess(wid);
  const queryClient = useQueryClient();
  const screens = useQuery({ queryKey: ["screens", wid], queryFn: () => api.screens.list(wid) });
  const suggested = (screens.data ?? []).filter((s) => s.status === "suggested");
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["screens"] });
  const keep = useMutation({ mutationFn: (slug: string) => api.screens.accept(wid, slug), onSuccess: refresh });
  const drop = useMutation({ mutationFn: (slug: string) => api.screens.dismiss(wid, slug), onSuccess: refresh });
  const manage = can("screens.manage");
  const look = useMutation({ mutationFn: () => api.screens.lookForMore(wid), onSuccess: refresh });
  const LOOK_TEXT: Record<string, string> = {
    woke_agent: "Syruss is looking through your records. Suggestions appear here in a minute or two.",
    too_few_records: "There aren't enough saved records to see a pattern yet. Three or more are needed.",
    suggestions_waiting: "Several suggestions are already waiting. Keep or dismiss some first.",
    nothing_new: "Nothing new has been read since Syruss last looked.",
  };

  return (
    <div className="flex max-w-3xl flex-col gap-4">
      <PageHeader
        title={<>Suggested screens<BetaTag /></>}
        subtitle={screens.isLoading ? " " : suggested.length ? "Syruss looked through your records and thinks these are worth having. Keep the ones you want." : undefined}
      />
      {manage && (
        <div className="flex flex-col gap-1">
          <div><Button variant="secondary" disabled={look.isPending} onClick={() => look.mutate()}>{look.isPending ? "Asking…" : "Ask Syruss to look for more"}</Button></div>
          {look.data && <p className="text-body text-muted" role="status">{LOOK_TEXT[look.data.status] ?? look.data.status}</p>}
          {look.error && <p className="text-body text-negative">{look.error.message}</p>}
        </div>
      )}
      {screens.isError && <ErrorNote message="Couldn't load the suggestions." onRetry={() => screens.refetch()} />}
      {screens.isLoading && <div className="flex flex-col gap-3">{[0, 1].map((i) => <Bar key={i} className="h-16 w-full" />)}</div>}
      {screens.data && suggested.length === 0 && (
        <Empty title="No suggestions right now" action={<Button asChild variant="secondary"><Link href={href("analytics")}>Your screens</Link></Button>}>
          When Syruss notices something worth a screen of its own, it waits here for you.
        </Empty>
      )}
      <ul className="flex flex-col gap-3">
        {suggested.map((s) => (
          <li key={s.id} className="rounded-card border border-border bg-sheet p-4">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div className="min-w-0">
                <h2 className="text-section font-semibold text-ink">{s.name}</h2>
                <p className="text-meta text-muted">{s.columns} columns, suggested {ago(s.created_at).toLowerCase()}</p>
              </div>
              <div className="flex flex-wrap gap-2">
                <Button asChild variant="secondary" size="sm"><Link href={href(`analytics/${s.slug}`)}>Look first</Link></Button>
                {manage && <Button variant="primary" size="sm" disabled={keep.isPending} onClick={() => keep.mutate(s.slug)}>Keep it</Button>}
                {manage && <Button variant="ghost" size="sm" disabled={drop.isPending} onClick={() => drop.mutate(s.slug)}>No thanks</Button>}
              </div>
            </div>
            {(s.rationale || s.description) && <p className="mt-2 text-body text-ink">{s.rationale || s.description}</p>}
          </li>
        ))}
      </ul>
    </div>
  );
}
