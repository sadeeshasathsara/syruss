import type { Tone } from "@/components/ui/Pill";
import type { CatalogItem, CatalogKind, LimitInfo, Limits } from "./types";

/** "$49.00", "LKR 4,900.00": money in its own currency. */
export function money(cents: number, currency = "USD"): string {
  try {
    return new Intl.NumberFormat(undefined, { style: "currency", currency }).format(cents / 100);
  } catch {
    return `${(cents / 100).toFixed(2)} ${currency}`;
  }
}

/** A limit's value in words: "Unlimited", "2,000". */
export const amount = (v: number | null | undefined) => (v == null ? "Unlimited" : v.toLocaleString());

export const KIND_LABEL: Record<CatalogKind, { one: string; many: string }> = {
  plan: { one: "Plan", many: "Plans" },
  addon: { one: "Add-on", many: "Add-ons" },
  credits: { one: "Credit pack", many: "Credit packs" },
};

export const ITEM_STATUS: Record<CatalogItem["status"], { label: string; tone: Tone }> = {
  draft: { label: "Draft", tone: "warning" },
  published: { label: "On sale", tone: "positive" },
  archived: { label: "Archived", tone: "neutral" },
};

/** What a catalog item is, in one line: "2,000 pages a month, 5 channels", "1,000 pages", "+1,000 pages a month". */
export function itemSummary(item: Pick<CatalogItem, "kind" | "limits" | "pages">, limits: Record<string, LimitInfo>): string {
  if (item.kind === "credits") return `${(item.pages ?? 0).toLocaleString()} prepaid pages`;
  const sign = item.kind === "addon" ? "+" : "";
  const parts = Object.entries(item.limits as Limits).map(([k, v]) => `${sign}${amount(v)} ${limits[k] ? (v === 1 ? limits[k].one : limits[k].noun) : k}`);
  return parts.join(" · ") || (item.kind === "plan" ? "Every limit unlimited" : "Adds nothing yet");
}

export const price = (item: Pick<CatalogItem, "price_cents" | "currency" | "interval">) =>
  item.price_cents ? `${money(item.price_cents, item.currency)}${item.interval === "month" ? " a month" : ""}` : "No charge";
