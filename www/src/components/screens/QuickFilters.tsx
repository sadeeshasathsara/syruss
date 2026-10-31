"use client";

import { Plus, Sigma, X } from "lucide-react";
import { Button, IconButton } from "@/components/ui/Button";
import { Input } from "@/components/ui/Field";
import { Select } from "@/components/ui/Select";
import type { ScreenFilter, ScreenFilterOp } from "@/lib/types";
import { OP_CHOICES } from "./format";

export interface FilterChoice {
  value: string;
  label: string;
}

const needsValue = (op: ScreenFilterOp | undefined) => OP_CHOICES.find((o) => o.value === (op ?? "eq"))?.needsValue !== false;

/** A filter that is complete enough to use: a condition written out, or a column with what it needs (a value, or both ends of a range). */
export function usable(f: ScreenFilter): boolean {
  if (f.formula !== undefined) return f.formula.trim().length > 0;
  if (!f.field) return false;
  if (f.op === "between") return Array.isArray(f.value) && f.value.length === 2 && f.value.every((v) => v !== "" && v !== undefined);
  return !needsValue(f.op) || (f.value !== undefined && f.value !== "");
}

/** What the API takes: only the complete filters; "is one of" as a list; a written condition as a formula. */
export function toRequest(filters: ScreenFilter[]): ScreenFilter[] {
  return filters.filter(usable).map((f) => {
    if (f.formula !== undefined) return { formula: f.formula.trim() };
    if (f.op === "in") return { field: f.field, op: "in" as const, value: String(f.value).split(",").map((v) => v.trim()).filter(Boolean) };
    return { field: f.field, op: f.op ?? "eq", value: f.value };
  });
}

/**
 * Filters, from simple to advanced. A filter is a column, how to compare (is, is not, contains, at least, at most, between, one of a
 * list, blank) and a value; an advanced condition is written as a formula such as `hours > 10 and site == "S12"`. All of them have to hold.
 */
export function FilterEditor({ filters, onChange, columns, label = "Add a filter", empty }: {
  filters: ScreenFilter[];
  onChange: (next: ScreenFilter[]) => void;
  columns: FilterChoice[];
  label?: string;
  empty?: string;
}) {
  const set = (i: number, patch: Partial<ScreenFilter>) => onChange(filters.map((f, n) => (n === i ? { ...f, ...patch } : f)));
  const range = (f: ScreenFilter): [string, string] => (Array.isArray(f.value) ? [String(f.value[0] ?? ""), String(f.value[1] ?? "")] : ["", ""]);
  return (
    <div className="flex flex-col gap-2">
      {filters.length === 0 && empty && <p className="text-body text-muted">{empty}</p>}
      {filters.map((f, i) => {
        if (f.formula !== undefined) {
          return (
            <div key={i} className="flex flex-wrap items-center gap-2">
              <Sigma aria-hidden className="size-4 flex-none text-muted" />
              <Input
                aria-label="Condition"
                className="min-w-64 flex-1 font-mono"
                value={f.formula}
                maxLength={400}
                placeholder={`for example: hours > 10 and contains(name, "per")`}
                onChange={(e) => set(i, { formula: e.target.value })}
              />
              <IconButton aria-label="Remove this condition" onClick={() => onChange(filters.filter((_, n) => n !== i))}><X /></IconButton>
            </div>
          );
        }
        const op = f.op ?? "eq";
        const [lo, hi] = range(f);
        return (
          <div key={i} className="flex flex-wrap items-center gap-2">
            <Select value={f.field || "_"} label="Column" className="w-auto min-w-36" onChange={(v) => set(i, { field: v === "_" ? undefined : v })}
              options={[{ value: "_", label: "Choose a column" }, ...columns]} />
            <Select value={op as ScreenFilterOp} label="How to compare" className="w-auto min-w-32"
              onChange={(next) => set(i, { op: next, value: next === "between" ? ["", ""] : next === "in" || !needsValue(next) ? "" : f.value })}
              options={OP_CHOICES.map((o) => ({ value: o.value, label: o.label }))} />
            {op === "between" ? (
              <>
                <Input aria-label="From" className="w-28" value={lo} onChange={(e) => set(i, { value: [e.target.value, hi] })} placeholder="From" />
                <span className="text-body text-muted">and</span>
                <Input aria-label="To" className="w-28" value={hi} onChange={(e) => set(i, { value: [lo, e.target.value] })} placeholder="To" />
              </>
            ) : needsValue(op) ? (
              <Input aria-label="Value" className="w-52" value={f.value === undefined || f.value === null ? "" : String(f.value)}
                onChange={(e) => set(i, { value: e.target.value })} placeholder={op === "in" ? "A, B, C" : "Value"} />
            ) : null}
            <IconButton aria-label="Remove this filter" onClick={() => onChange(filters.filter((_, n) => n !== i))}><X /></IconButton>
          </div>
        );
      })}
      <div className="flex flex-wrap gap-2">
        <Button size="sm" variant="secondary" onClick={() => onChange([...filters, { field: undefined, op: "eq", value: "" }])}>
          <Plus /> {label}
        </Button>
        <Button size="sm" variant="ghost" onClick={() => onChange([...filters, { formula: "" }])}>
          <Sigma /> Write a condition
        </Button>
      </div>
    </div>
  );
}
