"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Table2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Bar, ErrorNote, PageHeader, Panel } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { useAccess } from "@/lib/access";
import { api } from "@/lib/api";
import type { ScreenStarter } from "@/lib/types";
import { useWorkspace } from "@/lib/workspace";
import { AskForScreen } from "./AskForScreen";
import { BetaTag } from "./BetaTag";

/**
 * What Analytics shows before there is an analytics screen: what they are, one-click screens made from this workspace's own template,
 * a way to build one, and a way to ask Syruss. People who can only look are told who to ask.
 */
export function EmptyScreens() {
  const { workspace, href } = useWorkspace();
  const wid = workspace.id;
  const { can } = useAccess(wid);
  const router = useRouter();
  const queryClient = useQueryClient();
  const manage = can("screens.manage");
  const starters = useQuery({ queryKey: ["screen-starters", wid], queryFn: () => api.screens.starters(wid), enabled: manage });
  const add = useMutation({
    mutationFn: (s: ScreenStarter) => api.screens.create(wid, { name: s.name, description: s.description, definition: s.definition }),
    onSuccess: (screen) => {
      queryClient.invalidateQueries({ queryKey: ["screens"] });
      router.push(href(`analytics/${screen.slug}`));
    },
  });

  return (
    <div className="flex max-w-3xl flex-col gap-5">
      <PageHeader title={<>Analytics<BetaTag /></>} subtitle="Analytics screens: your saved records as tables you shape. Choose the columns, add calculations, keep the views you use." />
      <Panel>
        <div className="flex items-start gap-3">
          <Table2 aria-hidden className="mt-0.5 size-5 flex-none text-muted" />
          <div className="flex flex-col gap-2 text-body text-ink">
            <p>An analytics screen lists rows from your saved documents, such as every guard on every roster or every line of every invoice.</p>
            <p className="text-muted">
              Show only what you need, add columns that work things out (hours worked, overtime, totals), and group rows to see sums by person or by month.
              Every row opens the document it came from.
            </p>
          </div>
        </div>
      </Panel>

      {manage ? (
        <>
          <Panel title="Create a screen from your template">
            {starters.isLoading && <Bar className="h-12 w-full" />}
            {starters.isError && <ErrorNote message="Couldn't prepare the starting points." onRetry={() => starters.refetch()} />}
            {starters.data && starters.data.length === 0 && <p className="text-body text-muted">Your template has no repeating rows yet, so there is nothing to list. Build one once documents are read.</p>}
            <ul className="flex flex-col">
              {(starters.data ?? []).map((s) => (
                <li key={s.name} className="flex flex-wrap items-center gap-3 border-b border-rule py-3 last:border-0">
                  <span className="min-w-0 flex-1">
                    <span className="block text-body font-medium text-ink">{s.name}</span>
                    <span className="block text-meta text-muted">{s.description} {s.definition.columns.length} columns.</span>
                  </span>
                  <Button variant="secondary" disabled={add.isPending} onClick={() => add.mutate(s)}><Plus /> Add this screen</Button>
                </li>
              ))}
            </ul>
            {add.error && <p className="mt-2 text-body text-negative">{add.error.message}</p>}
          </Panel>
          <div><Button asChild variant="primary"><Link href={href("analytics/new")}><Plus /> Create a screen of your own</Link></Button></div>
        </>
      ) : (
        <p className="text-body text-muted">There are no analytics screens yet. Someone who manages this workspace (an Editor, Admin or Owner) can add them.</p>
      )}

      {manage && can("agent.chat") && (
        <Panel title="Or just ask Syruss">
          <AskForScreen
            placeholder="Describe the table you want, for example hours worked by each guard this month"
            examples={["Total hours by person", "Which sites had the most overtime?", "Show what looks unusual this month"]}
          />
        </Panel>
      )}
    </div>
  );
}
