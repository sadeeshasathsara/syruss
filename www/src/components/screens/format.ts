import type { ScreenAggregate, ScreenFilterOp, ScreenFormat, ScreenTone } from "@/lib/types";

/** A cell as the column's format says: numbers with their decimals, money, dates in the person's own style, hours as 7h 30m. */
export function formatCell(value: unknown, format: ScreenFormat = { type: "text" }): string {
  if (value === null || value === undefined || value === "") return "";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  const n = typeof value === "number" ? value : Number(value);
  const isNumber = typeof value === "number" || (typeof value === "string" && value.trim() !== "" && Number.isFinite(n));
  const digits = format.decimals;
  switch (format.type) {
    case "number":
      return isNumber ? n.toLocaleString(undefined, { minimumFractionDigits: digits ?? 0, maximumFractionDigits: digits ?? 2 }) : String(value);
    case "currency":
      if (!isNumber) return String(value);
      try {
        return n.toLocaleString(undefined, { style: "currency", currency: format.currency || "USD", minimumFractionDigits: digits, maximumFractionDigits: digits ?? 2 });
      } catch {
        return n.toLocaleString(undefined, { minimumFractionDigits: digits ?? 2, maximumFractionDigits: digits ?? 2 });
      }
    case "percent":
      return isNumber ? `${n.toLocaleString(undefined, { minimumFractionDigits: digits ?? 0, maximumFractionDigits: digits ?? 1 })}%` : String(value);
    case "duration": {
      if (!isNumber) return String(value);
      const minutes = Math.round(n * 60);
      const h = Math.trunc(Math.abs(minutes) / 60);
      const m = Math.abs(minutes) % 60;
      return `${minutes < 0 ? "-" : ""}${h}h${m ? ` ${m}m` : ""}`;
    }
    case "date": {
      const d = /^\d{4}-\d{2}-\d{2}$/.test(String(value)) ? new Date(`${value}T00:00:00`) : null;
      return d && !Number.isNaN(d.getTime()) ? d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" }) : String(value);
    }
    default:
      return String(value);
  }
}

/** Numbers line up on the right; everything else reads from the left. */
export function alignOf(format: ScreenFormat | undefined): "left" | "right" | "center" {
  if (format?.align) return format.align;
  return format && ["number", "currency", "percent", "duration"].includes(format.type) ? "right" : "left";
}

export const TONE_CLASS: Record<ScreenTone, string> = {
  positive: "bg-[var(--pill-positive-bg)] text-[var(--pill-positive-ink)]",
  warning: "bg-[var(--pill-warning-bg)] text-[var(--pill-warning-ink)]",
  negative: "bg-[var(--pill-negative-bg)] text-[var(--pill-negative-ink)]",
  info: "bg-[var(--pill-info-bg)] text-[var(--pill-info-ink)]",
};

export const FORMAT_CHOICES: { value: ScreenFormat["type"]; label: string }[] = [
  { value: "text", label: "Text" },
  { value: "number", label: "Number" },
  { value: "currency", label: "Money" },
  { value: "percent", label: "Percent" },
  { value: "date", label: "Date" },
  { value: "time", label: "Time" },
  { value: "duration", label: "Hours and minutes" },
];

export const AGGREGATE_CHOICES: { value: ScreenAggregate | "none"; label: string }[] = [
  { value: "none", label: "No total" },
  { value: "sum", label: "Sum" },
  { value: "avg", label: "Average" },
  { value: "min", label: "Smallest" },
  { value: "max", label: "Largest" },
  { value: "count", label: "Count" },
  { value: "count_distinct", label: "Count of different" },
];

export const OP_CHOICES: { value: ScreenFilterOp; label: string; needsValue: boolean }[] = [
  { value: "eq", label: "is", needsValue: true },
  { value: "ne", label: "is not", needsValue: true },
  { value: "contains", label: "contains", needsValue: true },
  { value: "gte", label: "is at least", needsValue: true },
  { value: "lte", label: "is at most", needsValue: true },
  { value: "between", label: "is between", needsValue: true },
  { value: "in", label: "is one of", needsValue: true },
  { value: "empty", label: "is blank", needsValue: false },
  { value: "not_empty", label: "is not blank", needsValue: false },
];
