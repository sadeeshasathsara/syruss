"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight, Search, X } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Bar, ErrorNote, Panel } from "@/components/ui/Blocks";
import { Button, IconButton } from "@/components/ui/Button";
import { Select } from "@/components/ui/Select";
import { StatusPill } from "@/components/ui/Pill";
import { api } from "@/lib/api";
import { ago, cn, dateTime, display, humanize } from "@/lib/format";
import { useWorkspace } from "@/lib/workspace";

const SINCE = [
  { value: "all", label: "Any time" }, { value: "7", label: "Last 7 days" }, { value: "30", label: "Last 30 days" },
  { value: "90", label: "Last 90 days" }, { value: "365", label: "Last year" },
];

/**
 * The rows of an object (or of one thing) as the documents read them, in a table you can search, filter and sort. A row opens the
 * document it came from; a name opens the thing. When viewing one thing's records, connected object attributes are also shown.
 */
export function RecordsTable({ type, thing, title = "Records" }: { type: string; thing?: string; title?: string }) {
  const { workspace, href } = useWorkspace();
  const router = useRouter();
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  const [since, setSince] = useState("all");
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [sort, setSort] = useState<{ by: string; dir: "asc" | "desc" }>({ by: "seen", dir: "desc" });
  const [page, setPage] = useState(0);
  // The search runs a moment after typing stops
  useEffect(() => {
    const t = setTimeout(() => { setQ(text); setPage(0); }, 300);
    return () => clearTimeout(t);
  }, [text]);

  const records = useQuery({
    queryKey: ["records", workspace.id, type, thing ?? null, q, since, filters, sort, page],
    queryFn: () => api.objects.records(workspace.id, type, { thing, q, filters, sinceDays: since === "all" ? null : Number(since), sort: sort.by, direction: sort.dir, page }),
    placeholderData: keepPreviousData,
  });
  // The filter choices come from the whole object (or thing), not from the narrowed rows
  const everything = useQuery({ queryKey: ["records", workspace.id, type, thing ?? null, "choices"], queryFn: () => api.objects.records(workspace.id, type, { thing }), staleTime: 60_000 });
  const facets = everything.data?.facets ?? [];

  const filtered = !!q.trim() || since !== "all" || Object.values(filters).some(Boolean);
  const clear = () => { setText(""); setQ(""); setSince("all"); setFilters({}); setPage(0); };
  const sortBy = (by: string) => { setSort((s) => (s.by === by ? { by, dir: s.dir === "asc" ? "desc" : "asc" } : { by, dir: by === "seen" ? "desc" : "asc" })); setPage(0); };
  const d = records.data;

  const head = (by: string, label: React.ReactNode) => (
    <th key={by} scope="col" aria-sort={sort.by === by ? (sort.dir === "asc" ? "ascending" : "descending") : "none"} className="whitespace-nowrap px-3 py-2 text-left font-normal">
      <button type="button" onClick={() => sortBy(by)} className="inline-flex items-center gap-1 text-meta text-muted hover:text-ink">
        {label}
        {sort.by === by && (sort.dir === "asc" ? <ArrowUp className="size-3.5" aria-hidden /> : <ArrowDown className="size-3.5" aria-hidden />)}
      </button>
    </th>
  );

  // Split columns into own and connected
  const ownCols = d?.columns.filter((c) => !c.connected) ?? [];
  const connectedCols = d?.columns.filter((c) => c.connected) ?? [];

  return (
    <Panel title={title} aside={d ? <span className="num">{d.total}</span> : undefined} bodyClassName="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <label className="flex h-9 min-w-48 flex-1 items-center gap-2 rounded-control border border-control bg-sheet px-2.5 focus-within:border-ink">
          <Search className="size-4 flex-none text-muted" aria-hidden />
          <input value={text} onChange={(e) => setText(e.target.value)} placeholder="Search names, values and documents" aria-label="Search the records"
            className="min-w-0 flex-1 bg-transparent text-body text-ink outline-none placeholder:text-faint" />
        </label>
        <Select label="When" value={since} onChange={(v) => { setSince(v); setPage(0); }} options={SINCE} className="w-40" />
        {facets.map((f) => (
          <Select key={f.field} label={humanize(f.field)} value={filters[f.field] || "__all"} className="w-44"
            onChange={(v) => { setFilters((cur) => ({ ...cur, [f.field]: v === "__all" ? "" : v })); setPage(0); }}
            options={[{ value: "__all", label: `${humanize(f.field)}: all` }, ...f.values.map((v) => ({ value: v.value, label: `${v.value} (${v.count})` }))]} />
        ))}
        {filtered && <Button variant="ghost" size="sm" onClick={clear}><X /> Clear</Button>}
      </div>

      {records.isError ? <ErrorNote message={(records.error as Error).message} onRetry={() => records.refetch()} />
        : !d ? <Bar className="h-48 w-full" />
        : d.items.length === 0 ? (
          <p className="py-6 text-center text-body text-muted">{filtered ? "No record matches. Try fewer filters." : "No document has a row of it yet: they appear here once documents that have one are saved."}</p>
        ) : (
          <div className={cn("overflow-x-auto scrollbar-thin rounded-control border border-rule", records.isPlaceholderData && "opacity-60")}>
            <table className="w-full min-w-max border-collapse text-body table-auto">
              <thead className="bg-raised">
                <tr>
                  {head("seen", "Received")}
                  {head("doc", "Document")}
                  {!thing && head("label", "Name")}
                  {ownCols.filter((c) => thing || !c.identity).map((c) => head(`f:${c.name}`, humanize(c.name)))}
                  {connectedCols.map((c) => (
                    <th key={c.name} scope="col" className="whitespace-nowrap px-3 py-2 text-left font-normal">
                      <span className="flex flex-col gap-0.5">
                        <span className="text-[10px] uppercase tracking-wide text-muted leading-none">{c.connected_type}</span>
                        <span className="text-meta text-muted">{c.connected_label ? "Name" : humanize(c.name.split(".").pop()!)}</span>
                      </span>
                    </th>
                  ))}
                  <th scope="col" className="px-3 py-2 text-left text-meta font-normal text-muted">Status</th>
                </tr>
              </thead>
              <tbody>
                {d.items.map((r) => (
                  <tr key={r.id} onClick={() => router.push(r.thing ? href(r.thing.path) : href(`documents/${r.document_id}`))}
                    className="cursor-pointer border-t border-rule align-top hover:bg-raised">
                    <td className="whitespace-nowrap px-3 py-2 text-muted" title={dateTime(r.seen_at)}>{ago(r.seen_at)}</td>
                    <td className="min-w-56 max-w-64 px-3 py-2">
                      <Link href={href(`documents/${r.document_id}`)} onClick={(e) => e.stopPropagation()} className="block truncate text-ink underline-offset-2 hover:underline" title={r.file_name}>{r.document_name}</Link>
                    </td>
                    {!thing && (
                      <td className="min-w-48 max-w-56 px-3 py-2">
                        {r.thing ? <Link href={href(r.thing.path)} onClick={(e) => e.stopPropagation()} className="block truncate font-medium text-ink underline-offset-2 hover:underline">{r.thing.label}</Link> : "—"}
                      </td>
                    )}
                    {ownCols.filter((c) => thing || !c.identity).map((c) => (
                      <td key={c.name} className="min-w-44 whitespace-nowrap px-3 py-2 text-ink">{r.values[c.name] == null || r.values[c.name] === "" ? <span className="text-faint">—</span> : display(r.values[c.name])}</td>
                    ))}
                    {connectedCols.map((c) => (
                      <td key={c.name} className="min-w-44 whitespace-nowrap px-3 py-2 text-muted">
                        {r.values[c.name] == null || r.values[c.name] === "" ? <span className="text-faint">—</span> : display(r.values[c.name])}
                      </td>
                    ))}
                    <td className="min-w-24 whitespace-nowrap px-3 py-2"><StatusPill status={r.status} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

      {d && d.total > d.page_size && (
        <div className="flex items-center justify-between gap-3 text-meta text-muted">
          <span className="num">{d.page * d.page_size + 1}–{Math.min(d.total, (d.page + 1) * d.page_size)} of {d.total}</span>
          <span className="flex gap-1">
            <IconButton aria-label="Previous page" disabled={page === 0} onClick={() => setPage((p) => Math.max(0, p - 1))}><ChevronLeft /></IconButton>
            <IconButton aria-label="Next page" disabled={(page + 1) * d.page_size >= d.total} onClick={() => setPage((p) => p + 1)}><ChevronRight /></IconButton>
          </span>
        </div>
      )}
    </Panel>
  );
}

/**
 * Unified flat table for a specific thing's profile page.
 *
 * Shows ONE merged table driven entirely by the workspace template:
 *  - Own fields: the identity object's own attributes (employee_no, name, etc.)
 *  - Referencing fields: every field from every object type that resolves_to this
 *    identity object (attendance entries, duty rows, invoice lines, trip logs…)
 *
 * Column headers are grouped by source type (a thin label above each group).
 * Works for ANY template domain — no hardcoding.
 */
export function ThingRecordsTable({ type, thingId, title = "Records" }: { type: string; thingId: string; title?: string }) {
  const { workspace, href } = useWorkspace();
  const router = useRouter();
  const [text, setText] = useState("");
  const [q, setQ] = useState("");
  const [since, setSince] = useState("all");
  const [sort, setSort] = useState<{ by: string; dir: "asc" | "desc" }>({ by: "seen", dir: "desc" });
  const [page, setPage] = useState(0);

  useEffect(() => {
    const t = setTimeout(() => { setQ(text); setPage(0); }, 300);
    return () => clearTimeout(t);
  }, [text]);

  const records = useQuery({
    queryKey: ["thing-full-records", workspace.id, type, thingId, q, since, sort, page],
    queryFn: () =>
      api.objects.thingFullRecords(workspace.id, type, thingId, {
        q,
        sinceDays: since === "all" ? null : Number(since),
        sort: sort.by,
        direction: sort.dir,
        page,
      }),
    placeholderData: keepPreviousData,
  });

  const filtered = !!q.trim() || since !== "all";
  const clear = () => { setText(""); setQ(""); setSince("all"); setPage(0); };
  const sortBy = (by: string) => {
    setSort((s) => (s.by === by ? { by, dir: s.dir === "asc" ? "desc" : "asc" } : { by, dir: by === "seen" ? "desc" : "asc" }));
    setPage(0);
  };
  const d = records.data;

  const head = (by: string, label: React.ReactNode) => (
    <th key={by} scope="col" aria-sort={sort.by === by ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}
      className="whitespace-nowrap px-3 py-2 text-left font-normal">
      <button type="button" onClick={() => sortBy(by)}
        className="inline-flex items-center gap-1 text-meta text-muted hover:text-ink">
        {label}
        {sort.by === by && (sort.dir === "asc" ? <ArrowUp className="size-3.5" aria-hidden /> : <ArrowDown className="size-3.5" aria-hidden />)}
      </button>
    </th>
  );

  // Group columns by source_type for the sub-header row
  const cols = d?.columns ?? [];
  const ownCols = cols.filter((c) => c.source === "own");
  const refCols = cols.filter((c) => c.source === "referencing");

  // Collect distinct referencing source types in order
  const refTypes: string[] = [];
  for (const c of refCols) {
    if (c.source_type && !refTypes.includes(c.source_type)) refTypes.push(c.source_type);
  }

  const hasGroupHeaders = refTypes.length > 0;

  return (
    <Panel title={title} aside={d ? <span className="num">{d.total}</span> : undefined} bodyClassName="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <label className="flex h-9 min-w-48 flex-1 items-center gap-2 rounded-control border border-control bg-sheet px-2.5 focus-within:border-ink">
          <Search className="size-4 flex-none text-muted" aria-hidden />
          <input value={text} onChange={(e) => setText(e.target.value)}
            placeholder="Search names, values and documents" aria-label="Search records"
            className="min-w-0 flex-1 bg-transparent text-body text-ink outline-none placeholder:text-faint" />
        </label>
        <Select label="When" value={since} onChange={(v) => { setSince(v); setPage(0); }} options={SINCE} className="w-40" />
        {filtered && <Button variant="ghost" size="sm" onClick={clear}><X /> Clear</Button>}
      </div>

      {records.isError ? (
        <ErrorNote message={(records.error as Error).message} onRetry={() => records.refetch()} />
      ) : !d ? (
        <Bar className="h-48 w-full" />
      ) : d.items.length === 0 ? (
        <p className="py-6 text-center text-body text-muted">
          {filtered ? "No record matches. Try fewer filters." : "No records found in any document yet."}
        </p>
      ) : (
        <div className={cn("overflow-x-auto scrollbar-thin rounded-control border border-rule", records.isPlaceholderData && "opacity-60")}>
          <table className="w-full min-w-max border-collapse text-body table-auto">
            <thead className="bg-raised">
              {/* Group header row — only shown when referencing types exist */}
              {hasGroupHeaders && (
                <tr>
                  {/* Received + Document span */}
                  <th colSpan={2} className="px-3 py-1" />
                  {/* Own fields group */}
                  {ownCols.length > 0 && (
                    <th colSpan={ownCols.length}
                      className="border-l border-rule px-3 py-1 text-left text-[10px] uppercase tracking-wide text-muted">
                      {humanize(type)}
                    </th>
                  )}
                  {/* One group header per referencing type */}
                  {refTypes.map((rt) => {
                    const count = refCols.filter((c) => c.source_type === rt).length;
                    return (
                      <th key={rt} colSpan={count}
                        className="border-l border-rule px-3 py-1 text-left text-[10px] uppercase tracking-wide text-muted">
                        {humanize(rt)}
                      </th>
                    );
                  })}
                  {/* Status */}
                  <th className="px-3 py-1" />
                </tr>
              )}

              {/* Column header row */}
              <tr>
                {head("seen", "Received")}
                {head("doc", "Document")}
                {ownCols.map((c) => (
                  <th key={c.name} scope="col"
                    className={cn("whitespace-nowrap px-3 py-2 text-left font-normal", hasGroupHeaders && "border-l border-rule first-of-type:border-l-0 [&:first-of-type]:border-l")}>
                    <button type="button" onClick={() => sortBy(`f:${c.name}`)}
                      className="inline-flex items-center gap-1 text-meta text-muted hover:text-ink">
                      {humanize(c.name)}
                      {sort.by === `f:${c.name}` && (sort.dir === "asc" ? <ArrowUp className="size-3.5" aria-hidden /> : <ArrowDown className="size-3.5" aria-hidden />)}
                    </button>
                  </th>
                ))}
                {refCols.map((c, i) => {
                  const prevType = i > 0 ? refCols[i - 1].source_type : null;
                  const isFirstOfType = c.source_type !== prevType;
                  return (
                    <th key={c.name} scope="col"
                      className={cn("whitespace-nowrap px-3 py-2 text-left font-normal", hasGroupHeaders && isFirstOfType && "border-l border-rule")}>
                      <button type="button" onClick={() => sortBy(`f:${c.name}`)}
                        className="inline-flex items-center gap-1 text-meta text-muted hover:text-ink">
                        {humanize(c.name.includes(".") ? c.name.split(".").pop()! : c.name)}
                        {sort.by === `f:${c.name}` && (sort.dir === "asc" ? <ArrowUp className="size-3.5" aria-hidden /> : <ArrowDown className="size-3.5" aria-hidden />)}
                      </button>
                    </th>
                  );
                })}
                <th scope="col" className="px-3 py-2 text-left text-meta font-normal text-muted">Status</th>
              </tr>
            </thead>
            <tbody>
              {d.items.map((r) => (
                <tr key={r.id}
                  onClick={() => router.push(href(`documents/${r.document_id}`))}
                  className="cursor-pointer border-t border-rule align-top hover:bg-raised">
                  <td className="whitespace-nowrap px-3 py-2 text-muted" title={dateTime(r.seen_at)}>{ago(r.seen_at)}</td>
                  <td className="min-w-56 max-w-64 px-3 py-2">
                    <Link href={href(`documents/${r.document_id}`)} onClick={(e) => e.stopPropagation()}
                      className="block truncate text-ink underline-offset-2 hover:underline" title={r.file_name}>
                      {r.document_name}
                    </Link>
                  </td>
                  {ownCols.map((c) => (
                    <td key={c.name} className="min-w-44 whitespace-nowrap px-3 py-2 text-ink">
                      {r.values[c.name] == null || r.values[c.name] === "" ? <span className="text-faint">—</span> : display(r.values[c.name])}
                    </td>
                  ))}
                  {refCols.map((c, i) => {
                    const prevType = i > 0 ? refCols[i - 1].source_type : null;
                    const isFirstOfType = c.source_type !== prevType;
                    return (
                      <td key={c.name}
                        className={cn("min-w-44 whitespace-nowrap px-3 py-2 text-ink", hasGroupHeaders && isFirstOfType && "border-l border-rule")}>
                        {r.values[c.name] == null || r.values[c.name] === "" ? <span className="text-faint">—</span> : display(r.values[c.name])}
                      </td>
                    );
                  })}
                  <td className="min-w-24 whitespace-nowrap px-3 py-2"><StatusPill status={r.status} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {d && d.total > d.page_size && (
        <div className="flex items-center justify-between gap-3 text-meta text-muted">
          <span className="num">{d.page * d.page_size + 1}–{Math.min(d.total, (d.page + 1) * d.page_size)} of {d.total}</span>
          <span className="flex gap-1">
            <IconButton aria-label="Previous page" disabled={page === 0} onClick={() => setPage((p) => Math.max(0, p - 1))}><ChevronLeft /></IconButton>
            <IconButton aria-label="Next page" disabled={(page + 1) * d.page_size >= d.total} onClick={() => setPage((p) => p + 1)}><ChevronRight /></IconButton>
          </span>
        </div>
      )}
    </Panel>
  );
}
