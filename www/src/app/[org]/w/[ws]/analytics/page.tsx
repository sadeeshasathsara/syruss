"use client";

import { useQuery } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import Link from "next/link";
import { AskForScreen } from "@/components/screens/AskForScreen";
import { BetaTag } from "@/components/screens/BetaTag";
import { EmptyScreens } from "@/components/screens/EmptyScreens";
import { Bar, ErrorNote, PageHeader, Panel } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Pill } from "@/components/ui/Pill";
import { useAccess } from "@/lib/access";
import { api } from "@/lib/api";
import { ago } from "@/lib/format";
import { useWorkspace } from "@/lib/workspace";

/**
 * Analytics: your analytics screens (tables over your saved records, with calculated columns) and a way to make more. With none
 * yet it says what they are and offers a first one.
 */
export default function AnalyticsIndex() {
  const { workspace, href } = useWorkspace();
  const { can } = useAccess(workspace.id);
  const screens = useQuery({ queryKey: ["screens", workspace.id], queryFn: () => api.screens.list(workspace.id) });
  const manage = can("screens.manage");
  const mine = (screens.data ?? []).filter((s) => s.status === "active");
  const suggested = (screens.data ?? []).filter((s) => s.status === "suggested");

  if (screens.isError) return <ErrorNote message="Couldn't load the analytics screens." onRetry={() => screens.refetch()} />;
  if (screens.isLoading) return <div className="flex flex-col gap-3">{[0, 1, 2].map((i) => <Bar key={i} className="h-16 w-full" />)}</div>;
  if (mine.length === 0 && suggested.length === 0) return <EmptyScreens />;

  return (
    <div className="flex max-w-4xl flex-col gap-5">
      <PageHeader
        title={<>Your screens<BetaTag /></>}
        subtitle="Analytics screens: your saved records as tables you shape, with calculations, groups and totals."
        actions={manage ? <Button asChild variant="primary"><Link href={href("analytics/new")}><Plus /> New screen</Link></Button> : undefined}
      />

      {suggested.length > 0 && (
        <Panel>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <p className="text-body text-ink">
              Syruss suggests {suggested.length} new {suggested.length === 1 ? "screen" : "screens"}: {suggested.map((s) => s.name).slice(0, 2).join(", ")}{suggested.length > 2 ? "…" : ""}
            </p>
            <Button asChild variant="secondary" size="sm"><Link href={href("analytics/suggested")}>Have a look</Link></Button>
          </div>
        </Panel>
      )}

      {mine.length === 0 ? (
        <p className="text-body text-muted">You haven&apos;t kept a screen yet. Have a look at what Syruss suggests, or make your own.</p>
      ) : (
        <ul className="grid grid-cols-1 gap-3 @2xl:grid-cols-2">
          {mine.map((s) => (
            <li key={s.id}>
              <Link href={href(`analytics/${s.slug}`)} className="flex h-full flex-col gap-1 rounded-card border border-border bg-sheet p-4 transition-colors hover:border-control">
                <span className="flex items-start justify-between gap-2">
                  <span className="text-section font-semibold text-ink">{s.name}</span>
                  {s.created_by_kind === "agent" && <Pill tone="accent">Built by Syruss</Pill>}
                </span>
                {s.description && <span className="line-clamp-2 text-body text-muted">{s.description}</span>}
                <span className="mt-auto pt-2 text-meta text-muted">{s.object ? `${s.object} rows, ` : ""}{s.columns} columns, changed {ago(s.updated_at).toLowerCase()}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}

      {can("agent.chat") && (
        <Panel title="Ask Syruss for another">
          <AskForScreen
            placeholder="Describe the table you want, for example hours worked by each guard this month"
            examples={["A full table of everything", "Total hours by person", "Show what looks unusual this month"]}
          />
        </Panel>
      )}
    </div>
  );
}
