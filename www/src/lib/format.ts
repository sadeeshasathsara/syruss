import { clsx, type ClassValue } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";
import type { DocumentStatus, TemplateField } from "./types";

// Our type scale (text-meta, text-body…) is font size, not colour: tell tailwind-merge so it
// doesn't drop a text colour when both appear on one element.
const twMerge = extendTailwindMerge({
  extend: { classGroups: { "font-size": [{ text: ["meta", "body", "section", "title", "figure"] }] } },
});

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function fieldName(field: string | TemplateField): string {
  return typeof field === "string" ? field : field.name;
}

/** "employee_no" → "Employee no" */
export function humanize(key: string): string {
  const s = key.replace(/[_-]+/g, " ").replace(/([a-z])([A-Z])/g, "$1 $2").trim();
  return s.charAt(0).toUpperCase() + s.slice(1).toLowerCase();
}

export function percent(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${Math.round(value * 100)}%`;
}

export function fileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** Recency in words for lists: "Just now", "12 min", "3 h", "Yesterday", "14 Oct". */
export function ago(iso: string, now = Date.now()): string {
  const then = new Date(iso).getTime();
  const min = Math.round((now - then) / 60000);
  if (min < 1) return "Just now";
  if (min < 60) return `${min} min`;
  const h = Math.round(min / 60);
  if (h < 24) return `${h} h`;
  if (h < 48) return "Yesterday";
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

export function dateTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function shortId(id: string): string {
  return id.slice(0, 8);
}

export const STATUS_LABEL: Record<DocumentStatus, string> = {
  PENDING: "Uploaded",
  QUEUED: "Queued",
  PROCESSING: "Reading",
  AWAITING_INPUT: "Waiting for answer",
  NEEDS_REVIEW: "Needs review",
  COMPLETED: "Done",
  FAILED: "Failed",
};

export type Tone = "neutral" | "positive" | "negative" | "warning" | "info" | "accent";

export const STATUS_TONE: Record<DocumentStatus, Tone> = {
  PENDING: "neutral",
  QUEUED: "neutral",
  PROCESSING: "accent",
  AWAITING_INPUT: "info",
  NEEDS_REVIEW: "warning",
  COMPLETED: "positive",
  FAILED: "negative",
};

/** Records are keyed by object type; single objects may be a dict instead of a list. */
export function rowsOf(value: unknown): Record<string, unknown>[] {
  if (Array.isArray(value)) return value.filter((v): v is Record<string, unknown> => !!v && typeof v === "object");
  if (value && typeof value === "object") return [value as Record<string, unknown>];
  return [];
}

export function display(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return String(value);
}
