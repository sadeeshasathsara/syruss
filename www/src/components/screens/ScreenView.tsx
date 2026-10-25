"use client";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, ListFilter, Pencil, Search, Sparkles, Trash2, X } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Input } from "@/components/ui/Field";
import { useShell } from "@/components/shell/ShellContext";
import { Bar, Empty, ErrorNote, PageHeader, Panel } from "@/components/ui/Blocks";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Sheet";
import { Pill } from "@/components/ui/Pill";
import { useAccess } from "@/lib/access";
import { ApiError, api } from "@/lib/api";
import type { ScreenFilter } from "@/lib/types";
import { useWorkspace } from "@/lib/workspace";
import { AskForScreen } from "./AskForScreen";
import { BetaTag } from "./BetaTag";
import { FilterEditor, toRequest } from "./QuickFilters";
import { type SortState, ScreenTable } from "./ScreenTable";

const PAGE = 50;

/** One screen: its table now, with filters and sorting for looking around (the saved screen stays as it was built). */
export function ScreenView({ slug }: { slug: string }) {
  const { workspace, href } = useWorkspace();
  const wid = workspace.id;
  const { can } = useAccess(wid);
  const router = useRouter();
  const queryClient = useQueryClient();
  const { announce } = useShell();
  const screen = useQuery({
    queryKey: ["screen", wid, slug],
    queryFn: () => api.screens.get(wid, slug),
    retry: (n, e) => !(e instanceof ApiError && e.status < 500) && n < 2,
  });
  const [filters, setFilters] = useState<ScreenFilter[]>([]);
  const [sort, setSort] = useState<SortState>();
  const [page, setPage] = useState(0);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [text, setText] = useState("");
  const [search, setSearch] = useState("");
  const [showFilters, setShowFilters] = useState(false);
  const manage = can("screens.manage");
  const used = toRequest(filters);
  const looking = used.length > 0 || search.trim().length > 0;

  // The search box waits for a pause in typing before asking.
  useEffect(() => {
    const t = setTimeout(() => { setSearch(text); setPage(0); }, 350);
    return () => clearTimeout(t);
  }, [text]);

  const run = useQuery({
    queryKey: ["screen-run", wid, slug, JSON.stringify(used), JSON.stringify(sort), search, page],
    queryFn: () => api.screens.run(wid, slug, { offset: page * PAGE, limit: PAGE, filters: used, sort: sort ? [sort] : undefined, search }),
    enabled: screen.isSuccess,
    placeholderData: keepPreviousData,
  });

  const refresh = () => ["screens", "screen", "screen-run"].forEach((k) => queryClient.invalidateQueries({ queryKey: [k] }));
  const remove = useMutation({
    mutationFn: () => api.screens.remove(wid, slug),
    onSuccess: () => {
      announce(`You deleted the screen ${screen.data?.name ?? ""}`.trim());
      refresh();
      router.replace(href("analytics"));
    },
  });
  // What was filtered for a look can be kept on the screen, so everyone who opens it sees it that way.
  const keepFilters = useMutation({
    mutationFn: () => api.screens.update(wid, slug, { definition: { ...screen.data!.definition, filters: [...(screen.data!.definition.filters ?? []), ...used] } }),
    onSuccess: () => { setFilters([]); refresh(); announce(`You saved the filters on ${screen.data?.name ?? "the screen"}`); },
  });
  const accept = useMutation({ mutationFn: () => api.screens.accept(wid, slug), onSuccess: refresh });
  const dismiss = useMutation({
    mutationFn: () => api.screens.dismiss(wid, slug),
    onSuccess: () => { refresh(); router.replace(href("analytics")); },
  });

  const cycleSort = (column: string) => {
    setPage(0);
    setSort((s) => (s?.column !== column ? { column, dir: "asc" } : s.dir === "asc" ? { column, dir: "desc" } : undefined));
  };

  if (screen.error instanceof ApiError && screen.error.status === 404) {
    return (
      <Empty title="This screen isn't here" action={<Button asChild variant="secondary"><Link href={href("analytics")}>Your screens</Link></Button>}>
        It may have been renamed or deleted.
      </Empty>
    );
  }
  if (screen.isError) return <ErrorNote message="Couldn't open this screen." onRetry={() => screen.refetch()} />;
  const s = screen.data;
  const data = run.data;
  const from = data && data.total_rows ? data.offset + 1 : 0;
  const to = data ? Math.min(data.offset + data.rows.length, data.total_rows) : 0;

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <PageHeader
        title={<>{s?.name ?? " "}<BetaTag /></>}
        subtitle={s ? (s.description || (data ? `${data.total_rows.toLocaleString()} ${data.grouped ? "groups" : "rows"} from ${data.documents_searched.toLocaleString()} documents.` : " ")) : " "}
        actions={s && (
          <>
            {s.created_by_kind === "agent" && <Pill tone="accent">Built by Syruss</Pill>}
            <Button asChild variant="secondary"><a href={api.screens.exportUrl(wid, slug)} download><Download /> Export CSV</a></Button>
            {manage && <Button asChild variant="secondary"><Link href={href(`analytics/${slug}/edit`)}><Pencil /> Edit</Link></Button>}
            {manage && <Button variant="ghost" onClick={() => setConfirmDelete(true)}><Trash2 /> Delete</Button>}
          </>
        )}
      />

      {s?.status === "suggested" && (
        <Panel title="Syruss suggests this screen">
          <p className="text-body text-ink">{s.rationale || "Syruss thinks this would be worth keeping an eye on."}</p>
          {manage ? (
            <div className="mt-3 flex gap-2">
              <Button variant="primary" disabled={accept.isPending} onClick={() => accept.mutate()}>Keep it</Button>
              <Button variant="secondary" disabled={dismiss.isPending} onClick={() => dismiss.mutate()}>No thanks</Button>
            </div>
          ) : <p className="mt-2 text-meta text-muted">Someone who manages screens can keep or dismiss it.</p>}
        </Panel>
      )}

      {data && (
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <label className="relative min-w-0 flex-1 basis-56 @2xl:max-w-sm">
              <Search aria-hidden className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted" />
              <span className="sr-only">Search this screen</span>
              <Input className="pl-9" value={text} onChange={(e) => setText(e.target.value)} placeholder="Search every column" />
            </label>
            <Button variant={showFilters || used.length ? "primary" : "secondary"} aria-expanded={showFilters} onClick={() => setShowFilters((v) => !v)}>
              <ListFilter /> Filters{used.length ? ` (${used.length})` : ""}
            </Button>
            {looking && (
              <Button variant="ghost" onClick={() => { setFilters([]); setText(""); setSearch(""); setPage(0); }}>
                <X /> Clear
              </Button>
            )}
            {manage && used.length > 0 && (
              <Button variant="ghost" disabled={keepFilters.isPending} onClick={() => keepFilters.mutate()}>Keep these filters on the screen</Button>
            )}
          </div>
          {(showFilters || filters.length > 0) && (
            <div className="rounded-card border border-border bg-sheet p-3">
              <FilterEditor
                filters={filters}
                onChange={(next) => { setFilters(next); setPage(0); }}
                columns={data.columns.map((c) => ({ value: c.id, label: c.label }))}
                label="Add a filter"
                empty="Show only the rows that match every filter you add. Write a condition for anything more specific."
              />
            </div>
          )}
          {data.errors.some((e) => e.column === "filter") && (
            <p role="status" className="text-body text-[var(--pill-warning-ink)]">{data.errors.find((e) => e.column === "filter")?.message}</p>
          )}
          {keepFilters.error && <p className="text-body text-negative">{keepFilters.error.message}</p>}
        </div>
      )}

      {run.isError && <ErrorNote message="Couldn't load the rows." onRetry={() => run.refetch()} />}
      {(screen.isLoading || (run.isLoading && !data)) && <div className="flex flex-col gap-3">{[0, 1, 2, 3, 4].map((i) => <Bar key={i} className="h-8 w-full" />)}</div>}
      {data && data.rows.length === 0 && (
        <Empty title={looking ? "Nothing matches" : "No rows yet"}>
          {looking ? "Change the search or a filter to see more." : "When documents are read and saved, their rows appear here by themselves."}
        </Empty>
      )}
      {data && data.rows.length > 0 && (
        <>
          <ScreenTable run={data} sort={sort} onSort={cycleSort} />
          <div className="flex flex-wrap items-center justify-between gap-2 text-body text-muted">
            <span className="num">{from.toLocaleString()}–{to.toLocaleString()} of {data.total_rows.toLocaleString()} {data.grouped ? "groups" : "rows"}</span>
            <span className="flex gap-2">
              <Button size="sm" variant="secondary" disabled={page === 0} onClick={() => setPage(page - 1)}>Previous</Button>
              <Button size="sm" variant="secondary" disabled={to >= data.total_rows} onClick={() => setPage(page + 1)}>Next</Button>
            </span>
          </div>
          {data.records_truncated && <p className="text-meta text-muted">Only the newest 5,000 documents are included.</p>}
        </>
      )}

      {s && can("agent.chat") && (
        <Panel title={<span className="inline-flex items-center gap-2"><Sparkles className="size-4 text-muted" aria-hidden /> Change this with Syruss</span>}>
          <AskForScreen screen={slug} placeholder="Add a column, change the order, or ask what stands out…" examples={["What stands out here?", "Add a column for the total", "Show only the last 30 days"]} />
        </Panel>
      )}

      <Dialog open={confirmDelete} onOpenChange={setConfirmDelete} title="Delete this screen?" description="The documents and their records are not touched; only this view of them goes.">
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={() => setConfirmDelete(false)}>Keep it</Button>
          <Button variant="danger" disabled={remove.isPending} onClick={() => remove.mutate()}>Delete screen</Button>
        </div>
      </Dialog>
    </div>
  );
}
