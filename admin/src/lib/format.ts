import { clsx, type ClassValue } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

// Our type scale (text-meta, text-body…) is font size, not colour: tell tailwind-merge so it
// doesn't drop a text colour when both appear on one element.
const twMerge = extendTailwindMerge({
  extend: { classGroups: { "font-size": [{ text: ["meta", "body", "section", "title", "figure"] }] } },
});

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
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
  return new Date(iso).toLocaleString(undefined, { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

/** "1 organization", "3 organizations". */
export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n.toLocaleString()} ${n === 1 ? one : many}`;
}
