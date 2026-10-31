"use client";

import { ArrowDown, ArrowUp, ArrowUpDown, FileText } from "lucide-react";
import Link from "next/link";
import { Pill } from "@/components/ui/Pill";
import { cn } from "@/lib/format";
import type { ScreenRun } from "@/lib/types";
import { useWorkspace } from "@/lib/workspace";
import { alignOf, formatCell, TONE_CLASS } from "./format";

export type SortState = { column: string; dir: "asc" | "desc" } | undefined;

const ALIGN = { left: "text-left", right: "text-right", center: "text-center" } as const;

/**
 * A screen's rows as a table: sticky header you can sort by, a totals row, calculated cells marked where a rule says so, and
 * a link from every row to the document it came from, so any figure can be checked against the page.
 */
export function ScreenTable({ run, sort, onSort, maxHeight = "calc(100dvh - 300px)" }: {
  run: ScreenRun;
  sort?: SortState;
  onSort?: (column: string) => void;
  maxHeight?: string;
}) {
  const { href } = useWorkspace();
  const errors = new Map(run.errors.map((e) => [e.column, e]));
  const linked = !run.grouped;
  return (
    <div className="overflow-auto rounded-card border border-border bg-sheet scrollbar-thin" style={{ maxHeight }}>
      <table className="w-full border-collapse text-body">
        <thead className="sticky top-0 z-10 bg-raised text-meta text-muted">
          <tr>
            {run.columns.map((c) => {
              const active = sort?.column === c.id;
              const align = alignOf(c.format);
              return (
                <th
                  key={c.id}
                  scope="col"
                  aria-sort={active ? (sort!.dir === "asc" ? "ascending" : "descending") : "none"}
                  className={cn("whitespace-nowrap px-3 py-2 font-medium", ALIGN[align])}
                  style={c.width ? { minWidth: c.width } : undefined}
                >
                  <button
                    type="button"
                    onClick={() => onSort?.(c.id)}
                    disabled={!onSort}
                    className={cn("inline-flex items-center gap-1 hover:text-ink", align === "right" && "flex-row-reverse", active && "text-ink")}
                    title={c.formula ? `Calculated: ${c.formula}` : undefined}
                  >
                    <span>{c.label}</span>
                    {c.kind === "formula" && <span aria-hidden className="text-faint">ƒ</span>}
                    {onSort && (active ? (sort!.dir === "asc" ? <ArrowUp className="size-3" /> : <ArrowDown className="size-3" />) : <ArrowUpDown className="size-3 text-faint" />)}
                  </button>
                  {errors.get(c.id) && (
                    <span className="block text-[11px] font-normal text-[var(--pill-warning-ink)]" title={errors.get(c.id)!.message}>
                      {errors.get(c.id)!.count} blank: {errors.get(c.id)!.message}
                    </span>
                  )}
                </th>
              );
            })}
            {linked && <th scope="col" className="w-10 px-2"><span className="sr-only">Open the document</span></th>}
          </tr>
        </thead>
        <tbody>
          {run.rows.map((row) => (
            <tr key={row.id} className="border-t border-rule hover:bg-raised/60">
              {run.columns.map((c, i) => {
                const tone = row.tones?.[c.id];
                const text = formatCell(row.cells[c.id], c.format);
                return (
                  <td key={c.id} className={cn("px-3 py-2 align-top break-words", ALIGN[alignOf(c.format)], "num", ["date", "time", "number", "currency", "percent", "duration"].includes(c.format.type) && "whitespace-nowrap")}>
                    {tone ? <span className={cn("inline-block rounded-chip px-1.5", TONE_CLASS[tone])}>{text}</span> : text || <span className="text-faint">—</span>}
                    {row.size !== undefined && i === 0 && <span className="ml-2 text-meta text-faint">{row.size} {row.size === 1 ? "row" : "rows"}</span>}
                  </td>
                );
              })}
              {linked && (
                <td className="px-2 py-2 text-right align-top">
                  {row.document_id && (
                    <Link href={href(`documents/${row.document_id}`)} aria-label={`Open ${row.filename ?? "the document"}`} title={row.filename ?? undefined} className="inline-flex text-muted hover:text-ink">
                      <FileText className="size-4" />
                    </Link>
                  )}
                  {row.pending_review && <Pill tone="warning" className="ml-1">To review</Pill>}
                </td>
              )}
            </tr>
          ))}
        </tbody>
        {run.totals && (
          <tfoot className="sticky bottom-0 bg-raised font-medium text-ink">
            <tr className="border-t border-border">
              {run.columns.map((c, i) => (
                <td key={c.id} className={cn("num px-3 py-2", ALIGN[alignOf(c.format)])}>
                  {c.aggregate ? formatCell(run.totals![c.id], c.format) : i === 0 ? "Total" : ""}
                </td>
              ))}
              {linked && <td />}
            </tr>
          </tfoot>
        )}
      </table>
    </div>
  );
}
